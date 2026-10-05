"""Datasets, assets, market data, ingestion and data quality."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConfigurationError, NotFoundError
from app.data.providers import CSVMarketDataProvider, PublicMarketDataProvider, SyntheticMarketDataProvider
from app.data.synthetic import SyntheticConfig
from app.db.session import get_db
from app.models import Asset, DataQualityIssue, DataQualityRun, Dataset, IngestionRun, MarketData
from app.schemas.requests import IngestRequest
from app.services import auth, ingestion, market_data, quality

router = APIRouter(tags=["data"])


@router.get("/datasets")
def list_datasets(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [market_data.dataset_summary(db, d) for d in db.scalars(select(Dataset).order_by(Dataset.id))]


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    ds = market_data.get_dataset(db, dataset_id)
    out = market_data.dataset_summary(db, ds)
    cfg = ds.config or {}
    man = cfg.get("manifest") or {}
    out["generator"] = cfg.get("generator")
    out["manifest_summary"] = (
        {
            "market_events": len(man.get("market_events", [])),
            "data_defects": len(man.get("data_defects", [])),
            "regime_runs": len(man.get("regime_runs", [])),
            "listing_events": man.get("listing_events", []),
            "disclaimer": man.get("disclaimer"),
            "calendar": man.get("calendar"),
        }
        if man
        else None
    )
    return out


@router.get("/datasets/{dataset_id}/manifest")
def get_manifest(dataset_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    ds = market_data.get_dataset(db, dataset_id)
    if not ds.is_synthetic or not (ds.config or {}).get("manifest"):
        raise NotFoundError("a ground-truth manifest exists only for synthetic datasets")
    return ds.config["manifest"]


@router.get("/assets")
def list_assets(
    dataset_id: int | None = None, q: str | None = None, include_benchmarks: bool = True, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    ds = market_data.get_dataset(db, dataset_id)
    stmt = (
        select(
            Asset.symbol,
            Asset.name,
            Asset.asset_type,
            Asset.sector,
            Asset.is_benchmark,
            Asset.id,
            func.min(MarketData.date),
            func.max(MarketData.date),
            func.count(MarketData.id),
        )
        .outerjoin(MarketData, MarketData.asset_id == Asset.id)
        .where(Asset.dataset_id == ds.id)
        .group_by(Asset.id)
        .order_by(Asset.is_benchmark.desc(), Asset.symbol)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Asset.symbol.ilike(like) | Asset.name.ilike(like) | Asset.sector.ilike(like))
    if not include_benchmarks:
        stmt = stmt.where(Asset.is_benchmark.is_(False))
    return [
        {
            "symbol": s,
            "name": n,
            "asset_type": t,
            "sector": sec,
            "is_benchmark": b,
            "id": i,
            "first_date": d0.isoformat() if d0 else None,
            "last_date": d1.isoformat() if d1 else None,
            "observations": c,
        }
        for s, n, t, sec, b, i, d0, d1, c in db.execute(stmt)
    ]


@router.get("/assets/{symbol}")
def get_asset(symbol: str, dataset_id: int | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    ds = market_data.get_dataset(db, dataset_id)
    a = db.scalars(select(Asset).where(Asset.dataset_id == ds.id, Asset.symbol == symbol.upper())).first()
    if a is None:
        raise NotFoundError(f"asset {symbol} not found in dataset {ds.code}")
    stats = db.execute(
        select(
            func.min(MarketData.date),
            func.max(MarketData.date),
            func.count(MarketData.id),
            func.count(MarketData.id) - func.count(MarketData.volume),
        ).where(MarketData.asset_id == a.id)
    ).first()
    return {
        "symbol": a.symbol,
        "name": a.name,
        "asset_type": a.asset_type,
        "sector": a.sector,
        "is_benchmark": a.is_benchmark,
        "currency": a.currency,
        "attributes": a.attributes,
        "dataset": ds.code,
        "first_date": stats[0].isoformat() if stats[0] else None,
        "last_date": stats[1].isoformat() if stats[1] else None,
        "observations": stats[2],
        "missing_volume": int(stats[3] or 0),
    }


@router.get("/market-data")
def get_market_data(
    symbol: str,
    dataset_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=5000, ge=1, le=20000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ds = market_data.get_dataset(db, dataset_id)
    a = db.scalars(select(Asset).where(Asset.dataset_id == ds.id, Asset.symbol == symbol.upper())).first()
    if a is None:
        raise NotFoundError(f"asset {symbol} not found")
    q = select(MarketData).where(MarketData.asset_id == a.id)
    if start:
        q = q.where(MarketData.date >= start)
    if end:
        q = q.where(MarketData.date <= end)
    rows = db.scalars(q.order_by(MarketData.date.desc()).limit(limit)).all()
    return {
        "symbol": a.symbol,
        "dataset": ds.code,
        "dataset_version": ds.version_label,
        "is_synthetic": ds.is_synthetic,
        "bars": [
            {
                "date": r.date.isoformat(),
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "adj_close": r.adj_close,
                "volume": r.volume,
            }
            for r in reversed(rows)
        ],
    }


@router.post("/market-data/ingest", status_code=201, dependencies=[Depends(auth.workspace_editor)])
def ingest(req: IngestRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    if req.provider == "synthetic":
        cfg = SyntheticConfig(seed=req.seed)
        provider = SyntheticMarketDataProvider(cfg)
        ds_cfg = {"generator": cfg.to_dict(), "manifest": provider.manifest}
        desc = "Synthetic multi-sector universe (DEMO / SYNTHETIC DATA MODE). See manifest for generation details."
    else:
        if not settings.public_provider_enabled:
            raise ConfigurationError("the public data provider is disabled; set NEXIS_PUBLIC_PROVIDER_ENABLED=true")
        provider = PublicMarketDataProvider(timeout=settings.public_provider_timeout_seconds)
        ds_cfg, desc = None, "Daily bars from the Yahoo Finance public chart endpoint (unofficial, no SLA)."
    run = ingestion.ingest(
        db,
        provider,
        req.dataset_code,
        req.dataset_name,
        req.symbols,
        req.start,
        req.end,
        req.mode,
        desc,
        ds_cfg,
        req.benchmark_symbols,
    )
    return ingestion.serialize_run(run)


@router.post("/market-data/upload", status_code=201, dependencies=[Depends(auth.workspace_editor)])
async def upload_csv(
    file: UploadFile = File(...),
    dataset_code: str = Form(..., pattern=r"^[A-Za-z0-9_.-]{2,64}$"),
    dataset_name: str = Form(..., min_length=2, max_length=200),
    default_symbol: str | None = Form(None),
    benchmark_symbol: str | None = Form(None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = get_settings()
    if not (file.filename or "").lower().endswith(".csv"):
        raise ConfigurationError("only .csv files are accepted")
    limit = settings.max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise ConfigurationError(f"file exceeds the {settings.max_upload_mb} MB upload limit")
    provider = CSVMarketDataProvider(content, default_symbol.upper() if default_symbol else None)
    symbols = [a.symbol for a in provider.list_assets()]
    run = ingestion.ingest(
        db,
        provider,
        dataset_code,
        dataset_name,
        symbols,
        None,
        None,
        "incremental",
        f"Uploaded from {file.filename}",
        None,
        [benchmark_symbol.upper()] if benchmark_symbol else None,
    )
    return ingestion.serialize_run(run)


@router.get("/ingestion-runs")
def list_runs(
    dataset_id: int | None = None, limit: int = Query(default=50, le=500), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    q = select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit)
    if dataset_id:
        q = q.where(IngestionRun.dataset_id == dataset_id)
    return [ingestion.serialize_run(r) for r in db.scalars(q)]


@router.get("/data-quality")
def data_quality(dataset_id: int | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    ds = market_data.get_dataset(db, dataset_id)
    runs = db.scalars(
        select(DataQualityRun).where(DataQualityRun.dataset_id == ds.id).order_by(DataQualityRun.created_at.desc()).limit(20)
    ).all()
    by_check = db.execute(
        select(
            DataQualityIssue.check, DataQualityIssue.category, DataQualityIssue.severity, DataQualityIssue.action, func.count()
        )
        .where(DataQualityIssue.dataset_id == ds.id, DataQualityIssue.ingestion_run_id.is_not(None))
        .group_by(DataQualityIssue.check, DataQualityIssue.category, DataQualityIssue.severity, DataQualityIssue.action)
    ).all()
    return {
        "dataset": market_data.dataset_summary(db, ds),
        "latest": quality.serialize_run(runs[0]) if runs else None,
        "history": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat(),
                "overall_score": r.overall_score,
                "status": r.status,
                "dataset_version": r.dataset_version,
            }
            for r in runs
        ],
        "ingestion_issue_summary": [
            {"check": c, "category": cat, "severity": sev, "action": act, "count": n} for c, cat, sev, act, n in by_check
        ],
    }


@router.post("/data-quality/run", status_code=201, dependencies=[Depends(auth.workspace_editor)])
def run_quality(dataset_id: int | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    ds = market_data.get_dataset(db, dataset_id)
    return quality.serialize_run(quality.run_quality_check(db, ds.id))


@router.get("/data-quality/issues")
def issues(
    dataset_id: int | None = None,
    check: str | None = None,
    source: str | None = Query(None, pattern="^(ingestion|quality_run)$"),
    symbol: str | None = None,
    severity: str | None = None,
    limit: int = Query(default=500, le=5000),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    ds = market_data.get_dataset(db, dataset_id)
    q = select(DataQualityIssue).where(DataQualityIssue.dataset_id == ds.id)
    if check:
        q = q.where(DataQualityIssue.check == check)
    if symbol:
        q = q.where(DataQualityIssue.symbol == symbol.upper())
    if severity:
        q = q.where(DataQualityIssue.severity == severity)
    if source == "ingestion":
        q = q.where(DataQualityIssue.ingestion_run_id.is_not(None))
    elif source == "quality_run":
        latest = db.scalar(select(func.max(DataQualityRun.id)).where(DataQualityRun.dataset_id == ds.id))
        q = q.where(DataQualityIssue.dq_run_id == latest)
    rows = db.scalars(q.order_by(DataQualityIssue.id.desc()).limit(limit)).all()
    return [
        {
            "id": i.id,
            "symbol": i.symbol,
            "date": i.date.isoformat() if i.date else None,
            "check": i.check,
            "category": i.category,
            "severity": i.severity,
            "action": i.action,
            "detail": i.detail,
            "source": "ingestion" if i.ingestion_run_id else "quality_run",
            "created_at": i.created_at.isoformat(),
        }
        for i in rows
    ]


@router.get("/market-data/coverage")
def coverage(dataset_id: int | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Monthly observation counts per symbol (a completeness heatmap)."""
    ds = market_data.get_dataset(db, dataset_id)
    panel = market_data.load_panel(db, ds.id)
    counts = panel.close.notna().groupby(panel.close.index.to_period("M")).sum()
    expected = panel.close.notna().any(axis=1).groupby(panel.close.index.to_period("M")).sum()
    ratio = counts.div(expected.replace(0, pd.NA), axis=0).astype(float)
    return {
        "months": [str(p) for p in ratio.index],
        "symbols": list(ratio.columns),
        "coverage": [[None if pd.isna(v) else round(float(v), 4) for v in ratio[c]] for c in ratio.columns],
    }
