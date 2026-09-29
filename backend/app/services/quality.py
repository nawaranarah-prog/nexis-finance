"""Data-quality runs over stored data."""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.data.quality import assess_quality
from app.db.base import utcnow
from app.models import Asset, DataQualityIssue, DataQualityRun, IngestionRun, MarketData
from app.services.market_data import get_dataset
from app.services.notifications import notify


def run_quality_check(db: Session, dataset_id: int) -> DataQualityRun:
    t0 = time.perf_counter()
    ds = get_dataset(db, dataset_id)
    rows = db.execute(
        select(
            Asset.symbol, MarketData.date, MarketData.open, MarketData.high, MarketData.low, MarketData.close, MarketData.volume
        )
        .join(Asset, Asset.id == MarketData.asset_id)
        .where(Asset.dataset_id == ds.id)
    ).all()
    bars = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    totals: dict[str, int] = {
        "received": int(
            db.scalar(
                select(func.coalesce(func.sum(IngestionRun.records_received - IngestionRun.records_skipped_existing), 0)).where(
                    IngestionRun.dataset_id == ds.id, IngestionRun.status != "failed"
                )
            )
            or 0
        )
    }
    for check, n in db.execute(
        select(DataQualityIssue.check, func.count())
        .where(DataQualityIssue.dataset_id == ds.id, DataQualityIssue.ingestion_run_id.is_not(None))
        .group_by(DataQualityIssue.check)
    ):
        totals[check] = int(n)
    declared_end = None
    if ds.is_synthetic and ds.config:
        end = (ds.config.get("generator") or {}).get("end")
        declared_end = pd.Timestamp(end).date() if end else None
    result = assess_quality(bars, totals, ds.is_synthetic, declared_end)
    run = DataQualityRun(
        dataset_id=ds.id,
        dataset_version=ds.version_label,
        overall_score=result["overall_score"],
        status=result["status"],
        components=result["components"],
        checks=result["checks"],
        per_symbol=result["per_symbol"],
        duration_seconds=time.perf_counter() - t0,
    )
    db.add(run)
    db.flush()
    issue_rows = [{**i, "dataset_id": ds.id, "dq_run_id": run.id, "created_at": utcnow()} for i in result["issues"]]
    if issue_rows:
        db.execute(insert(DataQualityIssue), issue_rows)
    if result["status"] != "pass":
        notify(
            db,
            "warning",
            "data_quality",
            f"Data quality {result['status']}: {ds.code}",
            f"Overall score {result['overall_score']:.1%}. Review failing checks on the Data Quality page.",
            link="/data-quality",
        )
    db.commit()
    return run


def serialize_run(r: DataQualityRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "dataset_id": r.dataset_id,
        "dataset_version": r.dataset_version,
        "overall_score": r.overall_score,
        "status": r.status,
        "components": r.components,
        "checks": r.checks,
        "per_symbol": r.per_symbol,
        "duration_seconds": r.duration_seconds,
        "created_at": r.created_at.isoformat(),
    }
