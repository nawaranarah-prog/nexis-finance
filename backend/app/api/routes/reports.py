"""Research reports (PDF) and data exports (CSV / JSON)."""

from __future__ import annotations

import io
import json
from datetime import date
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError
from app.db.session import get_db
from app.schemas.requests import ReportRequest
from app.services import backtests as bt_svc
from app.services import experiments as exp_svc
from app.services import jobs
from app.services import ml as ml_svc
from app.services import portfolios as port_svc
from app.services import reports as rep_svc

router = APIRouter(tags=["reports & exports"])


@router.post("/reports", status_code=202)
def create_report(req: ReportRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    params = req.model_dump()

    def work(s: Session, p: dict[str, Any], prog: Any) -> dict[str, Any]:
        prog(0.1, "collecting results")
        r = rep_svc.generate(
            s, p["title"], p["portfolio_id"], p["backtest_id"], p["experiment_ids"], p["stress_test_ids"], p["notes"]
        )
        return {"report_id": r.id, "file_name": r.file_name}

    return jobs.serialize(jobs.submit(db, "report", params, work))


@router.get("/reports")
def list_reports(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return rep_svc.list_reports(db)


@router.get("/reports/{report_id}/download")
def download_report(report_id: int, db: Session = Depends(get_db)) -> Response:
    content, r = rep_svc.report_bytes(db, report_id)
    return Response(
        content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{r.file_name}"'}
    )


def _csv(df: pd.DataFrame, name: str) -> StreamingResponse:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'}
    )


def _json(obj: Any, name: str) -> Response:
    return Response(
        json.dumps(obj, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
    )


def _fmt(fmt: str) -> str:
    if fmt not in ("csv", "json"):
        raise ConfigurationError("format must be csv or json")
    return fmt


@router.get("/exports/backtests/{bt_id}/trades")
def export_trades(bt_id: int, format: str = "csv", db: Session = Depends(get_db)) -> Response:
    rows = bt_svc.trades(db, bt_id, None, 1_000_000)["trades"]
    return (
        _csv(pd.DataFrame(rows), f"backtest-{bt_id}-trades") if _fmt(format) == "csv" else _json(rows, f"backtest-{bt_id}-trades")
    )


@router.get("/exports/backtests/{bt_id}/results")
def export_backtest(bt_id: int, format: str = "csv", db: Session = Depends(get_db)) -> Response:
    bt = bt_svc.get_backtest(db, bt_id)
    s = bt.series
    df = pd.DataFrame(
        {
            k: s[k]
            for k in (
                "dates",
                "equity",
                "gross_equity",
                "net_returns",
                "drawdown",
                "costs",
                "turnover",
                "gross_exposure",
                "net_exposure",
            )
            if k in s
        }
    ).rename(columns={"dates": "date"})
    if _fmt(format) == "csv":
        return _csv(df, f"backtest-{bt_id}-daily")
    exp = exp_svc.get_experiment(db, bt.experiment_id)
    return _json(
        {
            "backtest": bt_svc.serialize(bt, exp, include_series=False),
            "metrics": bt.metrics,
            "daily": df.to_dict(orient="records"),
        },
        f"backtest-{bt_id}",
    )


@router.get("/exports/experiments/{exp_id}")
def export_experiment(exp_id: int, format: str = "json", db: Session = Depends(get_db)) -> Response:
    e = exp_svc.get_experiment(db, exp_id)
    if _fmt(format) == "csv":
        return _csv(
            pd.DataFrame(
                [
                    {"experiment": e.code, "split": m.split, "model": m.model, "metric": m.metric, "value": m.value}
                    for m in e.metrics
                ]
            ),
            f"{e.code}-metrics",
        )
    return _json(exp_svc.serialize(e, include_artifacts=False), e.code)


@router.get("/exports/experiments")
def export_registry(format: str = "csv", db: Session = Depends(get_db)) -> Response:
    exps = exp_svc.list_experiments(db, limit=500)
    rows = [
        {
            "code": e.code,
            "type": e.experiment_type,
            "name": e.name,
            "status": e.status,
            "dataset_version": e.dataset_version,
            "seed": e.seed,
            "model": e.model,
            "train_start": e.train_start,
            "train_end": e.train_end,
            "test_start": e.test_start,
            "test_end": e.test_end,
            "duration_seconds": e.duration_seconds,
            "created_at": e.created_at,
            "parent_id": e.parent_id,
            "notes": e.notes,
            "config": json.dumps(e.config),
        }
        for e in exps
    ]
    return _csv(pd.DataFrame(rows), "experiment-registry") if _fmt(format) == "csv" else _json(rows, "experiment-registry")


@router.get("/exports/experiments/{exp_id}/predictions")
def export_predictions(exp_id: int, format: str = "csv", db: Session = Depends(get_db)) -> Response:
    e = exp_svc.get_experiment(db, exp_id)
    rows = ml_svc.predictions(db, exp_id, limit=1_000_000)
    return _csv(pd.DataFrame(rows), f"{e.code}-predictions") if _fmt(format) == "csv" else _json(rows, f"{e.code}-predictions")


@router.get("/exports/experiments/{exp_id}/anomalies")
def export_anomalies(exp_id: int, format: str = "csv", db: Session = Depends(get_db)) -> Response:
    e = exp_svc.get_experiment(db, exp_id)
    rows = ml_svc.anomalies(db, exp_id, limit=100_000)
    if _fmt(format) == "csv":
        flat = [
            {**{k: v for k, v in r.items() if k != "features"}, **{f"feature_{k}": v for k, v in r["features"].items()}}
            for r in rows
        ]
        return _csv(pd.DataFrame(flat), f"{e.code}-anomalies")
    return _json(rows, f"{e.code}-anomalies")


@router.get("/exports/portfolios/{pid}/metrics")
def export_portfolio(
    pid: int, format: str = "csv", start: date | None = None, end: date | None = None, db: Session = Depends(get_db)
) -> Response:
    a = port_svc.analytics(db, pid, start, end)
    name = f"portfolio-{pid}-metrics"
    if _fmt(format) == "json":
        return _json(
            {
                k: a[k]
                for k in (
                    "portfolio",
                    "period",
                    "summary",
                    "benchmark",
                    "var",
                    "risk_decomposition",
                    "concentration",
                    "correlation_exposure",
                    "assets",
                    "turnover",
                    "notes",
                )
            },
            name,
        )
    rows = [{"metric": k, "value": v} for k, v in a["summary"].items()]
    for c, v in a["var"].items():
        if "error" in v:
            continue
        for method in ("historical", "parametric_normal", "cornish_fisher"):
            rows.append({"metric": f"var_{c}_{method}", "value": v[method]["var"]})
            rows.append({"metric": f"cvar_{c}_{method}", "value": v[method]["cvar"]})
    return _csv(pd.DataFrame(rows), name)


@router.get("/exports/portfolios/{pid}/returns")
def export_portfolio_returns(pid: int, db: Session = Depends(get_db)) -> Response:
    a = port_svc.analytics(db, pid)
    s = a["series"]
    return _csv(
        pd.DataFrame(
            {
                "date": s["dates"],
                "value": s["value"],
                "daily_return": s["returns"],
                "drawdown": s["drawdown"],
                "benchmark_value": s["benchmark_value"],
            }
        ),
        f"portfolio-{pid}-returns",
    )


@router.get("/exports/risk/{pid}")
def export_risk(pid: int, db: Session = Depends(get_db)) -> Response:
    port_svc.get_portfolio(db, pid)
    return _csv(pd.DataFrame(port_svc.risk_history(db, pid, limit=10_000)), f"portfolio-{pid}-risk-metrics")


@router.get("/exports/stress-tests/{test_id}")
def export_stress(test_id: int, format: str = Query(default="csv"), db: Session = Depends(get_db)) -> Response:
    from app.services import stress

    t = stress.get_test(db, test_id)
    if _fmt(format) == "json":
        return _json(t, f"stress-test-{test_id}")
    return _csv(pd.DataFrame(t["results"].get("assets", [])), f"stress-test-{test_id}-assets")
