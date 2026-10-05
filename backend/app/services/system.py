"""Search, system health and overview aggregation."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import APP_VERSION, get_settings
from app.core.logging import request_metrics
from app.db import autoseed
from app.models import (
    Anomaly,
    Asset,
    Backtest,
    BacktestTrade,
    DataQualityIssue,
    DataQualityRun,
    Dataset,
    Experiment,
    IngestionRun,
    Job,
    MarketData,
    MLPrediction,
    Notification,
    Portfolio,
    Report,
    RiskMetric,
    StressTest,
)

TABLES = {
    "datasets": Dataset,
    "assets": Asset,
    "market_data": MarketData,
    "ingestion_runs": IngestionRun,
    "data_quality_runs": DataQualityRun,
    "data_quality_issues": DataQualityIssue,
    "portfolios": Portfolio,
    "risk_metrics": RiskMetric,
    "stress_tests": StressTest,
    "experiments": Experiment,
    "backtests": Backtest,
    "backtest_trades": BacktestTrade,
    "ml_predictions": MLPrediction,
    "anomalies": Anomaly,
    "reports": Report,
    "jobs": Job,
    "notifications": Notification,
}


def search(db: Session, q: str, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    q = q.strip()
    if len(q) < 1:
        return {"assets": [], "portfolios": [], "experiments": [], "backtests": []}
    like = f"%{q}%"
    assets = db.execute(
        select(Asset.id, Asset.symbol, Asset.name, Asset.sector, Asset.dataset_id, Dataset.code)
        .join(Dataset, Dataset.id == Asset.dataset_id)
        .where(or_(Asset.symbol.ilike(like), Asset.name.ilike(like), Asset.sector.ilike(like)))
        .order_by(func.length(Asset.symbol), Asset.symbol)
        .limit(limit)
    ).all()
    ports = db.scalars(select(Portfolio).where(or_(Portfolio.name.ilike(like), Portfolio.notes.ilike(like))).limit(limit)).all()
    exps = db.scalars(
        select(Experiment)
        .where(
            or_(
                Experiment.code.ilike(like),
                Experiment.name.ilike(like),
                Experiment.model.ilike(like),
                Experiment.notes.ilike(like),
            )
        )
        .order_by(Experiment.created_at.desc())
        .limit(limit)
    ).all()
    bts = db.scalars(
        select(Backtest)
        .where(or_(Backtest.name.ilike(like), Backtest.strategy_key.ilike(like)))
        .order_by(Backtest.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "assets": [
            {
                "symbol": a.symbol,
                "name": a.name,
                "sector": a.sector,
                "dataset_id": a.dataset_id,
                "dataset": a.code,
                "link": f"/asset-research?symbol={a.symbol}&dataset={a.dataset_id}",
            }
            for a in assets
        ],
        "portfolios": [{"id": p.id, "name": p.name, "link": f"/portfolio-lab?id={p.id}"} for p in ports],
        "experiments": [
            {
                "id": e.id,
                "code": e.code,
                "name": e.name,
                "type": e.experiment_type,
                "status": e.status,
                "link": f"/experiments?id={e.id}",
            }
            for e in exps
        ],
        "backtests": [{"id": b.id, "name": b.name, "strategy": b.strategy_key, "link": f"/backtesting?id={b.id}"} for b in bts],
    }


def health(db: Session) -> dict[str, Any]:
    t0 = time.perf_counter()
    db_ok, db_error = True, None
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok, db_error = False, exc.__class__.__name__
    latency = (time.perf_counter() - t0) * 1000
    revision = None
    if db_ok:
        try:
            revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            db.rollback()
    settings = get_settings()
    out: dict[str, Any] = {
        "api": "ok",
        "version": APP_VERSION,
        "environment": settings.env,
        "database": {
            "ok": db_ok,
            "error": db_error,
            "latency_ms": round(latency, 2),
            "engine": "sqlite" if settings.is_sqlite else "postgresql",
            "migration": revision,
        },
        "requests": request_metrics.summary(),
        "seeding": autoseed.status(),
    }
    if not db_ok:
        return out
    out["record_counts"] = {name: int(db.scalar(select(func.count()).select_from(m)) or 0) for name, m in TABLES.items()}
    last_ing = db.scalars(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(10)).all()
    out["recent_ingestions"] = [
        {
            "id": r.id,
            "dataset_id": r.dataset_id,
            "provider": r.provider,
            "status": r.status,
            "started_at": r.started_at.isoformat(),
            "duration_seconds": r.duration_seconds,
            "records_inserted": r.records_inserted,
            "records_rejected": r.records_rejected,
        }
        for r in last_ing
    ]
    datasets = db.scalars(select(Dataset)).all()
    fresh = []
    for ds in datasets:
        lag = None
        if ds.end_date:
            import numpy as np

            lag = int(np.busday_count(ds.end_date, date.today())) if date.today() > ds.end_date else 0
        dq = db.scalars(
            select(DataQualityRun).where(DataQualityRun.dataset_id == ds.id).order_by(DataQualityRun.created_at.desc()).limit(1)
        ).first()
        fresh.append(
            {
                "dataset_id": ds.id,
                "code": ds.code,
                "version": ds.version_label,
                "is_synthetic": ds.is_synthetic,
                "end_date": ds.end_date.isoformat() if ds.end_date else None,
                "business_days_since_end": lag,
                "freshness_note": "static synthetic dataset" if ds.is_synthetic else None,
                "records": ds.record_count,
                "dq_score": dq.overall_score if dq else None,
                "dq_status": dq.status if dq else None,
            }
        )
    out["datasets"] = fresh
    failed = db.scalars(select(Job).where(Job.status == "failed").order_by(Job.created_at.desc()).limit(10)).all()
    out["failed_jobs"] = [
        {"id": j.id, "job_type": j.job_type, "error": j.error, "created_at": j.created_at.isoformat()} for j in failed
    ]
    out["failed_ingestions"] = int(
        db.scalar(select(func.count()).select_from(IngestionRun).where(IngestionRun.status == "failed")) or 0
    )
    out["running_jobs"] = int(db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))) or 0)
    durations = db.execute(
        select(
            Experiment.experiment_type, func.avg(Experiment.duration_seconds), func.max(Experiment.duration_seconds), func.count()
        )
        .where(Experiment.status == "completed")
        .group_by(Experiment.experiment_type)
    ).all()
    out["experiment_durations"] = [{"type": t, "avg_seconds": a, "max_seconds": m, "count": n} for t, a, m, n in durations]
    ing = db.execute(
        select(func.avg(IngestionRun.duration_seconds), func.max(IngestionRun.duration_seconds)).where(
            IngestionRun.status != "failed"
        )
    ).first()
    out["ingestion_durations"] = {"avg_seconds": ing[0] if ing else None, "max_seconds": ing[1] if ing else None}
    return out
