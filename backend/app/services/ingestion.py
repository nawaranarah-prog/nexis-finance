"""Ingestion orchestration: fetch → normalise → validate → persist → record metadata.

Modes
-----
``incremental`` (default) Only bars dated after each symbol's latest stored bar are inserted; the
                provider is asked only for the missing range, so history is never re-downloaded.
``full``        Existing bars for the requested symbols are replaced.
"""

from __future__ import annotations

import hashlib
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, NexisError
from app.core.logging import get_logger, log_event
from app.data.providers import MarketDataProvider
from app.data.validation import validate_bars
from app.db.base import utcnow
from app.models import Asset, DataQualityIssue, Dataset, IngestionRun, MarketData
from app.services import market_data
from app.services.notifications import notify

log = get_logger(__name__)
CHUNK = 5000


def get_or_create_dataset(
    db: Session,
    code: str,
    name: str,
    provider: MarketDataProvider,
    description: str | None = None,
    config: dict[str, Any] | None = None,
) -> Dataset:
    ds = db.scalars(select(Dataset).where(Dataset.code == code)).first()
    if ds is None:
        ds = Dataset(
            code=code,
            name=name,
            source=provider.name,
            is_synthetic=provider.is_synthetic,
            description=description,
            config=config,
            version=0,
        )
        db.add(ds)
        db.flush()
    elif ds.source != provider.name:
        raise ConfigurationError(f"dataset {code} is sourced from '{ds.source}', not '{provider.name}'")
    return ds


def compute_content_hash(db: Session, dataset_id: int) -> tuple[str, int, date | None, date | None]:
    rows = db.execute(
        select(
            Asset.symbol, MarketData.date, MarketData.open, MarketData.high, MarketData.low, MarketData.close, MarketData.volume
        )
        .join(Asset, Asset.id == MarketData.asset_id)
        .where(Asset.dataset_id == dataset_id)
        .order_by(Asset.symbol, MarketData.date)
    ).all()
    if not rows:
        return hashlib.sha256(b"").hexdigest(), 0, None, None
    df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    # Round to 8 significant decimals so the hash is stable across database float round-trips.
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].round(8)
    digest = hashlib.sha256(pd.util.hash_pandas_object(df.astype(str), index=False).to_numpy().tobytes()).hexdigest()
    return digest, len(df), min(df["date"]), max(df["date"])


def ingest(
    db: Session,
    provider: MarketDataProvider,
    dataset_code: str,
    dataset_name: str,
    symbols: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    mode: str = "incremental",
    description: str | None = None,
    dataset_config: dict[str, Any] | None = None,
    benchmark_symbols: list[str] | None = None,
) -> IngestionRun:
    if mode not in ("incremental", "full"):
        raise ConfigurationError("mode must be 'incremental' or 'full'")
    if start and end and start > end:
        raise ConfigurationError("start date must not be after end date")
    t0 = time.perf_counter()
    ds = get_or_create_dataset(db, dataset_code, dataset_name, provider, description, dataset_config)
    run = IngestionRun(
        dataset_id=ds.id,
        provider=provider.name,
        mode=mode,
        status="running",
        requested_start=start,
        requested_end=end,
        symbols=symbols,
    )
    db.add(run)
    db.commit()

    try:
        infos = provider.list_assets(symbols)
        if not infos:
            raise ConfigurationError("provider returned no assets for the requested symbols")
        bench = set(benchmark_symbols or [])
        existing = {a.symbol: a for a in db.scalars(select(Asset).where(Asset.dataset_id == ds.id))}
        for info in infos:
            a = existing.get(info.symbol)
            if a is None:
                a = Asset(
                    dataset_id=ds.id,
                    symbol=info.symbol,
                    name=info.name,
                    asset_type=info.asset_type,
                    sector=info.sector,
                    is_benchmark=info.is_benchmark or info.symbol in bench,
                    currency=info.currency,
                    attributes=info.attributes,
                )
                db.add(a)
                existing[info.symbol] = a
            else:
                a.name, a.attributes = info.name, info.attributes
                if info.symbol in bench:
                    a.is_benchmark = True
        db.flush()
        syms = [i.symbol for i in infos]
        asset_ids = {s: existing[s].id for s in syms}

        last_dates: dict[str, date] = {}
        if mode == "incremental":
            for aid, mx in db.execute(
                select(MarketData.asset_id, func.max(MarketData.date))
                .where(MarketData.asset_id.in_(list(asset_ids.values())))
                .group_by(MarketData.asset_id)
            ):
                sym = next(s for s, i in asset_ids.items() if i == aid)
                last_dates[sym] = mx
        else:
            db.execute(delete(MarketData).where(MarketData.asset_id.in_(list(asset_ids.values()))))

        # Ask the provider only for what is missing: symbols are grouped by the date they resume from,
        # so one stale symbol (e.g. a delisting) does not force re-downloading everyone's history.
        groups: dict[date | None, list[str]] = {}
        for s in syms:
            resume = last_dates[s] + timedelta(days=1) if s in last_dates else None
            if start and (resume is None or start > resume):
                resume = start
            groups.setdefault(resume, []).append(s)
        frames = [provider.fetch(group, resume, end) for resume, group in groups.items() if not (resume and end and resume > end)]
        frames = [f for f in frames if len(f)]
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "date"])
        run.records_received = len(raw)

        skipped = 0
        if mode == "incremental" and last_dates and len(raw):
            dts = pd.to_datetime(raw["date"], errors="coerce")
            cutoff = raw["symbol"].map(lambda s: pd.Timestamp(last_dates[s]) if s in last_dates else pd.Timestamp.min)
            old = dts.notna() & (dts <= cutoff)
            skipped = int(old.sum())
            raw = raw[~old]
        run.records_skipped_existing = skipped

        report = validate_bars(raw)
        clean = report.clean
        run.records_rejected = report.rejected
        run.duplicates = report.duplicates

        rows = [
            {
                "asset_id": asset_ids[r.symbol],
                "date": r.date.date(),
                "open": _f(r.open),
                "high": _f(r.high),
                "low": _f(r.low),
                "close": float(r.close),
                "adj_close": _f(r.adj_close),
                "volume": None if pd.isna(r.volume) else int(r.volume),
                "ingestion_run_id": run.id,
            }
            for r in clean.itertuples(index=False)
            if r.symbol in asset_ids
        ]
        for i in range(0, len(rows), CHUNK):
            db.execute(insert(MarketData), rows[i : i + CHUNK])
        run.records_inserted = len(rows)

        issue_rows = [{**iss, "dataset_id": ds.id, "ingestion_run_id": run.id, "created_at": utcnow()} for iss in report.issues]
        for i in range(0, len(issue_rows), CHUNK):
            db.execute(insert(DataQualityIssue), issue_rows[i : i + CHUNK])

        digest, count, d0, d1 = compute_content_hash(db, ds.id)
        if digest != ds.content_hash:
            ds.version += 1
        ds.content_hash, ds.record_count, ds.start_date, ds.end_date = digest, count, d0, d1
        if dataset_config:
            ds.config = dataset_config

        s = report.summary()
        run.warnings = [f"{k}: {v}" for k, v in s["by_check"].items()]
        run.status = "warning" if (report.rejected or report.duplicates or report.warnings) else "success"
        run.dataset_version = ds.version_label
        run.finished_at = utcnow()
        run.duration_seconds = time.perf_counter() - t0
        if report.rejected:
            notify(
                db,
                "warning",
                "data_quality",
                f"Ingestion {ds.code}: {report.rejected} records rejected",
                f"{report.rejected} invalid records were rejected and {report.duplicates} duplicates removed. "
                f"See the Data Quality page for record-level details.",
                link="/data-quality",
            )
        db.commit()
        market_data.invalidate(ds.id)
        log_event(
            log,
            "ingestion finished",
            dataset=ds.code,
            provider=provider.name,
            mode=mode,
            received=run.records_received,
            inserted=run.records_inserted,
            rejected=run.records_rejected,
            duration_ms=round(run.duration_seconds * 1000),
        )
        return run
    except NexisError as exc:
        db.rollback()
        _fail(db, run.id, exc.message, t0)
        raise
    except Exception as exc:
        db.rollback()
        log.exception("ingestion failed")
        _fail(db, run.id, f"internal error: {exc.__class__.__name__}", t0)
        raise


def _fail(db: Session, run_id: int, message: str, t0: float) -> None:
    run = db.get(IngestionRun, run_id)
    if run is None:
        return
    run.status = "failed"
    run.errors = [message]
    run.finished_at = utcnow()
    run.duration_seconds = time.perf_counter() - t0
    notify(db, "error", "ingestion", f"Ingestion failed ({run.provider})", message, link="/market-data")
    db.commit()


def serialize_run(r: IngestionRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "dataset_id": r.dataset_id,
        "provider": r.provider,
        "mode": r.mode,
        "status": r.status,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "duration_seconds": r.duration_seconds,
        "requested_start": _iso(r.requested_start),
        "requested_end": _iso(r.requested_end),
        "records_received": r.records_received,
        "records_inserted": r.records_inserted,
        "records_rejected": r.records_rejected,
        "records_skipped_existing": r.records_skipped_existing,
        "duplicates": r.duplicates,
        "dataset_version": r.dataset_version,
        "warnings": r.warnings or [],
        "errors": r.errors or [],
        "symbols": r.symbols,
    }


def _f(v: Any) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None
