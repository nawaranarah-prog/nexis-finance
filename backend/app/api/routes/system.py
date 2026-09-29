"""Health, configuration, glossary, search, notifications, jobs and the overview dashboard."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.core.config import APP_VERSION, get_settings
from app.core.errors import InsufficientDataError
from app.core.glossary import GLOSSARY
from app.db.session import get_db
from app.models import Backtest, DataQualityRun, Experiment, Portfolio
from app.schemas.requests import MarkReadRequest
from app.services import experiments as exp_svc
from app.services import jobs, notifications, system
from app.services import portfolios as port_svc
from app.services.market_data import dataset_summary, get_dataset, load_panel

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe: the API process is up."""
    return {"status": "ok", "version": APP_VERSION}


@router.get("/system/health")
def system_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    return system.health(db)


@router.get("/system/config")
def system_config() -> dict[str, Any]:
    s = get_settings()
    return {
        "version": APP_VERSION,
        "environment": s.env,
        "database_engine": "sqlite" if s.is_sqlite else "postgresql",
        "public_provider_enabled": s.public_provider_enabled,
        "default_risk_free_rate": s.risk_free_rate,
        "trading_days_per_year": s.trading_days,
        "max_upload_mb": s.max_upload_mb,
        "job_workers": s.job_workers,
    }


@router.get("/meta/glossary")
def glossary() -> dict[str, dict[str, str]]:
    return GLOSSARY


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=100), db: Session = Depends(get_db)) -> dict[str, Any]:
    return system.search(db, q)


@router.get("/notifications")
def list_notifications(
    unread_only: bool = False, limit: int = Query(default=50, le=200), db: Session = Depends(get_db)
) -> dict[str, Any]:
    items = notifications.list_notifications(db, unread_only, limit)
    unread = len(notifications.list_notifications(db, True, 500))
    return {"items": items, "unread": unread}


@router.post("/notifications/read")
def mark_read(req: MarkReadRequest, db: Session = Depends(get_db)) -> dict[str, int]:
    return {"updated": notifications.mark_read(db, req.ids)}


@router.get("/jobs")
def list_jobs(
    status: str | None = None, limit: int = Query(default=50, le=200), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return [jobs.serialize(j) for j in jobs.list_jobs(db, limit, status)]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return jobs.serialize(jobs.get_job(db, job_id))


@router.get("/overview")
def overview(
    dataset_id: int | None = None,
    portfolio_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ds = get_dataset(db, dataset_id)
    panel = load_panel(db, ds.id)
    dq = db.scalars(
        select(DataQualityRun).where(DataQualityRun.dataset_id == ds.id).order_by(DataQualityRun.created_at.desc()).limit(1)
    ).first()
    coverage = None
    if dq and dq.per_symbol:
        coverage = float(np.mean([p["completeness"] for p in dq.per_symbol]))
    market = {
        "dataset": dataset_summary(db, ds),
        "assets": len(panel.tradable),
        "benchmarks": panel.benchmarks,
        "latest_date": panel.close.index[-1].date().isoformat(),
        "first_date": panel.close.index[0].date().isoformat(),
        "coverage": coverage,
        "data_quality": {"score": dq.overall_score, "status": dq.status, "run_at": dq.created_at.isoformat()} if dq else None,
    }

    # Portfolio snapshot
    p = None
    if portfolio_id:
        p = port_svc.get_portfolio(db, portfolio_id)
    else:
        p = db.scalars(select(Portfolio).where(Portfolio.dataset_id == ds.id).order_by(Portfolio.id)).first()
    port: dict[str, Any] | None = None
    if p is not None and p.dataset_id == ds.id:
        try:
            a = port_svc.analytics(db, p.id, start, end)
            s = a["summary"]
            v95 = a["var"].get("95", {})
            port = {
                "id": p.id,
                "name": p.name,
                "benchmark": p.benchmark_symbol,
                "period": a["period"],
                "metrics": {
                    k: s.get(k)
                    for k in (
                        "cumulative_return",
                        "annualized_return",
                        "annualized_volatility",
                        "sharpe_ratio",
                        "max_drawdown",
                        "beta",
                    )
                },
                "var_95": (v95.get("historical") or {}).get("var"),
                "cvar_95": (v95.get("historical") or {}).get("cvar"),
                "series": {
                    k: a["series"][k]
                    for k in (
                        "dates",
                        "value",
                        "benchmark_value",
                        "drawdown",
                        "rolling_volatility",
                        "benchmark_rolling_volatility",
                    )
                },
            }
        except InsufficientDataError as exc:
            port = {"id": p.id, "name": p.name, "error": exc.message}

    # Research snapshot
    counts = exp_svc.counts(db)
    last_bt = db.execute(
        select(Backtest, Experiment)
        .join(Experiment, Experiment.id == Backtest.experiment_id)
        .order_by(Backtest.created_at.desc())
        .limit(1)
    ).first()
    last_ml = db.scalars(
        select(Experiment)
        .where(Experiment.experiment_type.in_(["volatility_forecast", "regime", "anomaly"]))
        .order_by(Experiment.created_at.desc())
        .limit(1)
    ).first()
    last_regime = db.scalars(
        select(Experiment)
        .where(Experiment.experiment_type == "regime", Experiment.status == "completed", Experiment.dataset_id == ds.id)
        .order_by(Experiment.created_at.desc())
        .limit(1)
    ).first()
    research = {
        "experiment_counts": counts,
        "total_experiments": sum(counts.values()),
        "latest_backtest": {
            "id": last_bt[0].id,
            "code": last_bt[1].code,
            "name": last_bt[0].name,
            "headline": last_bt[1].summary.get("headline") if last_bt[1].summary else None,
            "created_at": last_bt[0].created_at.isoformat(),
        }
        if last_bt
        else None,
        "latest_ml": {
            "id": last_ml.id,
            "code": last_ml.code,
            "type": last_ml.experiment_type,
            "status": last_ml.status,
            "model": last_ml.model,
            "created_at": last_ml.created_at.isoformat(),
        }
        if last_ml
        else None,
    }
    regime = None
    if last_regime and last_regime.summary:
        regime = {
            "experiment_id": last_regime.id,
            "code": last_regime.code,
            **(last_regime.summary.get("latest") or {}),
            "note": "Descriptive cluster label from an unsupervised model; not a forecast.",
        }

    # Cross-sectional asset performance + sector correlation
    px = panel.adj_close[panel.tradable].loc[pd.Timestamp(start) if start else None : pd.Timestamp(end) if end else None]
    rets = px.pct_change(fill_method=None)
    perf = []
    rf = get_settings().risk_free_rate
    for sym in panel.tradable:
        r = rets[sym].dropna()
        if len(r) < 20:
            continue
        perf.append(
            {
                "symbol": sym,
                "sector": panel.assets[sym]["sector"],
                "cumulative_return": M.cumulative_return(r),
                "annualized_volatility": M.annualized_volatility(r),
                "sharpe_ratio": M.sharpe_ratio(r, rf),
            }
        )
    sectors = sorted({panel.assets[s]["sector"] for s in panel.tradable if panel.assets[s]["sector"]})
    sec_r = pd.DataFrame(
        {sec: rets[[s for s in panel.tradable if panel.assets[s]["sector"] == sec]].mean(axis=1) for sec in sectors}
    )
    sec_corr = sec_r.corr().round(4)
    return {
        "market": market,
        "portfolio": port,
        "research": research,
        "regime": regime,
        "asset_performance": perf,
        "sector_correlation": {
            "sectors": sectors,
            "matrix": [[None if pd.isna(v) else float(v) for v in row] for row in sec_corr.to_numpy()],
        },
        "filters": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
    }
