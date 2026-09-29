"""Machine-learning experiment orchestration and persistence."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, NexisError
from app.ml.anomalies import run_anomaly_experiment
from app.ml.features import VOL_FEATURES
from app.ml.regimes import run_regime_experiment
from app.ml.volatility import run_volatility_experiment
from app.models import Anomaly, DataQualityIssue, Dataset, Experiment, MLPrediction
from app.services import experiments as exp_svc
from app.services.market_data import get_dataset, load_panel
from app.services.notifications import notify

Progress = Callable[[float, str | None], None] | None


def _manifest(ds: Dataset) -> dict[str, Any] | None:
    return (ds.config or {}).get("manifest") if ds.is_synthetic else None


def _wrap(db: Session, exp: Experiment, label: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except NexisError as exc:
        db.rollback()
        exp_svc.fail_experiment(db, exp.id, exc.message)
        notify(db, "error", "ml", f"{label} {exp.code} failed", exc.message, link="/machine-learning")
        db.commit()
        raise
    except Exception as exc:
        db.rollback()
        exp_svc.fail_experiment(db, exp.id, f"model failure: {exc.__class__.__name__}")
        notify(
            db,
            "error",
            "ml",
            f"{label} {exp.code} failed",
            "The model raised an unexpected error; see server logs.",
            link="/machine-learning",
        )
        db.commit()
        raise


def run_volatility(db: Session, cfg: dict[str, Any], progress: Progress = None, parent_id: int | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    ds = get_dataset(db, cfg["dataset_id"])
    panel = load_panel(db, ds.id)
    panel.require(cfg["symbols"])
    bench = cfg.get("benchmark_symbol") or (panel.benchmarks[0] if panel.benchmarks else None)
    features = cfg.get("features") or list(VOL_FEATURES)
    exp = exp_svc.start_experiment(
        db,
        "volatility_forecast",
        cfg.get("name") or f"Volatility forecast h={cfg.get('horizon', 10)}",
        ds,
        cfg,
        seed=cfg.get("seed", 42),
        features=features,
        train=(cfg.get("start") or panel.close.index[0], cfg["train_end"]),
        test=(
            pd.Timestamp(cfg.get("validation_end") or cfg["train_end"]) + pd.Timedelta(days=1),
            cfg.get("end") or panel.close.index[-1],
        ),
        notes=cfg.get("notes"),
        parent_id=parent_id,
    )

    def work() -> dict[str, Any]:
        res = run_volatility_experiment(
            panel.adj_close,
            panel.high,
            panel.low,
            panel.volume,
            panel.adj_close[bench] if bench else None,
            {**cfg, "features": features},
            (lambda p, m=None: progress(p * 0.9, m)) if progress else None,
        )
        preds = res.pop("predictions")
        rows = [{**r, "experiment_id": exp.id} for r in preds]
        for i in range(0, len(rows), 5000):
            db.execute(insert(MLPrediction), rows[i : i + 5000])
        mrows = []
        for model, splits in res["metrics"].items():
            for split, m in splits.items():
                mrows += exp_svc.metric_rows_from(split, {k: v for k, v in m.items() if k != "n"}, model)
        sel = res["selection"]
        exp_svc.complete_experiment(
            db,
            exp,
            mrows,
            summary={
                "preferred_model": sel["preferred_model"],
                "best_baseline": sel["best_baseline"],
                "best_fitted_model": sel["best_fitted_model"],
                "rmse_improvement_vs_baseline": sel["rmse_improvement_vs_baseline"],
                "test_rmse_preferred": res["metrics"][sel["preferred_model"]]["test"]["rmse"],
                "horizon": res["split"]["horizon"],
            },
            artifacts=res,
            duration=time.perf_counter() - t0,
            model=sel["preferred_model"],
        )
        notify(
            db,
            "success",
            "ml",
            f"Volatility experiment {exp.code} completed",
            f"Preferred model: {sel['preferred_model']} (selected on {sel['selection_split']} RMSE).",
            link=f"/machine-learning?id={exp.id}",
        )
        db.commit()
        return {"experiment_id": exp.id, "experiment_code": exp.code}

    return _wrap(db, exp, "Volatility experiment", work)


def _true_regimes(ds: Dataset, index: pd.DatetimeIndex) -> pd.Series | None:
    man = _manifest(ds)
    if not man:
        return None
    s = pd.Series(index=index, dtype=object)
    for r in man.get("regime_runs", []):
        s.loc[pd.Timestamp(r["start"]) : pd.Timestamp(r["end"])] = r["regime"]
    return s


def run_regime(db: Session, cfg: dict[str, Any], progress: Progress = None, parent_id: int | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    ds = get_dataset(db, cfg["dataset_id"])
    panel = load_panel(db, ds.id)
    bench = cfg.get("benchmark_symbol") or (panel.benchmarks[0] if panel.benchmarks else None)
    if not bench:
        raise ConfigurationError("a benchmark symbol is required for regime features")
    panel.require([bench])
    exp = exp_svc.start_experiment(
        db,
        "regime",
        cfg.get("name") or f"Regime model k={cfg.get('n_regimes', 4)}",
        ds,
        cfg,
        seed=cfg.get("seed", 42),
        model=cfg.get("method", "gmm"),
        features=cfg.get("features"),
        train=(cfg.get("start") or panel.close.index[0], cfg["train_end"]),
        test=(pd.Timestamp(cfg["train_end"]) + pd.Timedelta(days=1), cfg.get("end") or panel.close.index[-1]),
        notes=cfg.get("notes"),
        parent_id=parent_id,
    )

    def work() -> dict[str, Any]:
        if progress:
            progress(0.2, "building regime features")
        uni = panel.returns(panel.tradable)
        res = run_regime_experiment(panel.adj_close[bench].dropna(), uni, cfg, _true_regimes(ds, panel.close.index))
        mrows = [("full", None, "n_regimes", res["n_regimes"])]
        for s in res["regime_stats"]:
            mrows.append(("full", s["regime"], "frequency", s["frequency"]))
            mrows.append(("full", s["regime"], "average_duration_days", s["average_duration_days"]))
        if res["evaluation_vs_synthetic_truth"]:
            mrows.append(
                ("full", None, "adjusted_rand_index_vs_truth", res["evaluation_vs_synthetic_truth"]["adjusted_rand_index"])
            )
        for b in res["model_selection"]:
            mrows.append(("train", f"k={b['k']}", "bic", b["bic"]))
        exp.features = res["features"]
        exp_svc.complete_experiment(
            db,
            exp,
            mrows,
            summary={
                "latest": res["latest"],
                "order": res["order"],
                "benchmark": bench,
                "ari_vs_truth": (res["evaluation_vs_synthetic_truth"] or {}).get("adjusted_rand_index"),
            },
            artifacts=res,
            duration=time.perf_counter() - t0,
        )
        notify(
            db,
            "success",
            "ml",
            f"Regime experiment {exp.code} completed",
            f"Latest classified regime ({res['latest']['date']}): {res['latest']['regime']}. Descriptive label, not a forecast.",
            link=f"/regimes?id={exp.id}",
        )
        db.commit()
        return {"experiment_id": exp.id, "experiment_code": exp.code}

    return _wrap(db, exp, "Regime experiment", work)


def run_anomaly(db: Session, cfg: dict[str, Any], progress: Progress = None, parent_id: int | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    ds = get_dataset(db, cfg["dataset_id"])
    panel = load_panel(db, ds.id)
    symbols = cfg.get("symbols") or panel.tradable
    panel.require(symbols)
    exp = exp_svc.start_experiment(
        db,
        "anomaly",
        cfg.get("name") or "Anomaly scan",
        ds,
        cfg,
        seed=cfg.get("seed", 42),
        model="IsolationForest + rolling z-score",
        features=["ret_z", "volume_z", "range_z", "gap_z", "rv_ratio"],
        notes=cfg.get("notes"),
        parent_id=parent_id,
    )

    def work() -> dict[str, Any]:
        if progress:
            progress(0.2, "computing anomaly features")
        dq_keys = {
            (s, d.isoformat())
            for s, d in db.execute(
                select(DataQualityIssue.symbol, DataQualityIssue.date).where(
                    DataQualityIssue.dataset_id == ds.id,
                    DataQualityIssue.check.in_(["missing_volume", "zero_volume_price_change"]),
                )
            )
            if s and d
        }
        man = _manifest(ds)
        injected = None
        if man:
            injected = list(man.get("market_events", [])) + [d for d in man.get("data_defects", []) if d["type"] == "bad_tick"]
        res = run_anomaly_experiment(
            panel.close, panel.open, panel.high, panel.low, panel.volume, {**cfg, "symbols": symbols}, dq_keys, injected
        )
        rows = [
            {
                "experiment_id": exp.id,
                "symbol": r["symbol"],
                "date": r["date"],
                "score": r["score"],
                "severity": r["severity"],
                "category": r["category"],
                "method": r["method"],
                "top_feature": r["top_feature"],
                "features": {**r["features"], "category_reason": r["category_reason"]},
                "matches_injected_event": r["matches_injected_event"],
            }
            for r in res.pop("anomalies")
        ]
        for i in range(0, len(rows), 5000):
            db.execute(insert(Anomaly), rows[i : i + 5000])
        s = res["summary"]
        mrows = [("full", m, "flagged", n) for m, n in s["flagged_by_method"].items()]
        mrows += [
            ("full", None, "flagged_by_both", s["flagged_by_both"]),
            ("full", None, "data_quality_flags", s["data_quality_flags"]),
            ("full", None, "market_behaviour_flags", s["market_behaviour_flags"]),
        ]
        ev = res.get("evaluation_vs_injected")
        if ev:
            for m, e in ev["methods"].items():
                mrows += [
                    ("full", m, "recall_injected", e["recall"]),
                    ("full", m, "precision_vs_injected", e["precision_vs_injected"]),
                ]
        exp_svc.complete_experiment(db, exp, mrows, summary=s, artifacts=res, duration=time.perf_counter() - t0)
        notify(
            db,
            "success",
            "ml",
            f"Anomaly scan {exp.code} completed",
            f"{s['flagged_by_method'].get('isolation_forest', 0)} Isolation Forest and "
            f"{s['flagged_by_method'].get('rolling_zscore', 0)} z-score flags over {s['observations']:,} observations.",
            link=f"/anomalies?id={exp.id}",
        )
        db.commit()
        return {"experiment_id": exp.id, "experiment_code": exp.code}

    return _wrap(db, exp, "Anomaly scan", work)


def predictions(
    db: Session, exp_id: int, model: str | None = None, symbol: str | None = None, limit: int = 20000
) -> list[dict[str, Any]]:
    q = select(MLPrediction).where(MLPrediction.experiment_id == exp_id)
    if model:
        q = q.where(MLPrediction.model == model)
    if symbol:
        q = q.where(MLPrediction.symbol == symbol)
    rows = db.scalars(q.order_by(MLPrediction.date, MLPrediction.symbol).limit(limit)).all()
    return [
        {
            "model": r.model,
            "symbol": r.symbol,
            "date": r.date.isoformat(),
            "split": r.split,
            "y_true": r.y_true,
            "y_pred": r.y_pred,
        }
        for r in rows
    ]


def anomalies(
    db: Session,
    exp_id: int,
    method: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    symbol: str | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    q = select(Anomaly).where(Anomaly.experiment_id == exp_id)
    if method:
        q = q.where(Anomaly.method == method)
    if category:
        q = q.where(Anomaly.category == category)
    if severity:
        q = q.where(Anomaly.severity == severity)
    if symbol:
        q = q.where(Anomaly.symbol == symbol)
    rows = db.scalars(q.order_by(Anomaly.score.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "symbol": r.symbol,
            "date": r.date.isoformat(),
            "score": r.score,
            "severity": r.severity,
            "category": r.category,
            "method": r.method,
            "top_feature": r.top_feature,
            "features": r.features,
            "matches_injected_event": r.matches_injected_event,
        }
        for r in rows
    ]


exp_svc.register_runner("volatility_forecast", run_volatility)
exp_svc.register_runner("regime", run_regime)
exp_svc.register_runner("anomaly", run_anomaly)
