"""Dataset access and cached price panels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from threading import Lock
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import InsufficientDataError, NotFoundError
from app.models import Asset, Dataset, MarketData


@dataclass
class Panel:
    """Wide (date x symbol) price/volume frames for one dataset version."""

    dataset_id: int
    version: int
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    adj_close: pd.DataFrame
    volume: pd.DataFrame
    assets: dict[str, dict[str, Any]]

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    @property
    def benchmarks(self) -> list[str]:
        return [s for s, a in self.assets.items() if a["is_benchmark"]]

    @property
    def tradable(self) -> list[str]:
        return [s for s, a in self.assets.items() if not a["is_benchmark"] and s in self.close.columns]

    def require(self, symbols: list[str]) -> None:
        missing = [s for s in symbols if s not in self.close.columns]
        if missing:
            raise NotFoundError(f"unknown symbol(s) in dataset: {', '.join(missing)}", details={"symbols": missing})

    def returns(self, symbols: list[str] | None = None, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        """Close-to-close returns from *adjusted* closes. Gaps stay NaN."""
        px = self.adj_close if symbols is None else self.adj_close[symbols]
        r = px.pct_change(fill_method=None)
        return r.loc[pd.Timestamp(start) if start else None : pd.Timestamp(end) if end else None]


_cache: dict[tuple[int, int], Panel] = {}
_lock = Lock()


def get_dataset(db: Session, dataset_id: int | None = None) -> Dataset:
    if dataset_id is None:
        ds = db.scalars(select(Dataset).order_by(Dataset.id)).first()
        if ds is None:
            raise NotFoundError("no datasets exist yet — run scripts/seed_demo.py or ingest data first")
        return ds
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise NotFoundError(f"dataset {dataset_id} not found")
    return ds


def load_panel(db: Session, dataset_id: int) -> Panel:
    ds = get_dataset(db, dataset_id)
    key = (ds.id, ds.version)
    with _lock:
        if key in _cache:
            return _cache[key]
    assets = db.scalars(select(Asset).where(Asset.dataset_id == ds.id)).all()
    if not assets:
        raise InsufficientDataError(f"dataset {ds.code} has no assets")
    by_id = {a.id: a.symbol for a in assets}
    rows = db.execute(
        select(
            MarketData.asset_id,
            MarketData.date,
            MarketData.open,
            MarketData.high,
            MarketData.low,
            MarketData.close,
            MarketData.adj_close,
            MarketData.volume,
        ).where(MarketData.asset_id.in_(list(by_id)))
    ).all()
    if not rows:
        raise InsufficientDataError(f"dataset {ds.code} has no market data")
    df = pd.DataFrame(rows, columns=["asset_id", "date", "open", "high", "low", "close", "adj_close", "volume"])
    df["symbol"] = df["asset_id"].map(by_id)
    df["date"] = pd.to_datetime(df["date"])
    frames = {}
    for col in ("open", "high", "low", "close", "adj_close", "volume"):
        frames[col] = df.pivot(index="date", columns="symbol", values=col).sort_index().astype(float)
    frames["adj_close"] = frames["adj_close"].fillna(frames["close"])
    meta = {
        a.symbol: {
            "name": a.name,
            "asset_type": a.asset_type,
            "sector": a.sector,
            "is_benchmark": a.is_benchmark,
            "currency": a.currency,
            "attributes": a.attributes or {},
        }
        for a in assets
        if a.symbol in frames["close"]
    }
    panel = Panel(dataset_id=ds.id, version=ds.version, assets=meta, **frames)
    with _lock:
        for k in [k for k in _cache if k[0] == ds.id]:
            del _cache[k]  # drop superseded versions of this dataset
        _cache[key] = panel
    return panel


def invalidate(dataset_id: int | None = None) -> None:
    with _lock:
        for k in list(_cache):
            if dataset_id is None or k[0] == dataset_id:
                del _cache[k]


def dataset_summary(db: Session, ds: Dataset) -> dict[str, Any]:
    n_assets = db.scalar(select(func.count(Asset.id)).where(Asset.dataset_id == ds.id)) or 0
    n_bench = db.scalar(select(func.count(Asset.id)).where(Asset.dataset_id == ds.id, Asset.is_benchmark.is_(True))) or 0
    return {
        "id": ds.id,
        "code": ds.code,
        "name": ds.name,
        "source": ds.source,
        "is_synthetic": ds.is_synthetic,
        "description": ds.description,
        "version": ds.version,
        "version_label": ds.version_label,
        "content_hash": ds.content_hash,
        "start_date": _iso(ds.start_date),
        "end_date": _iso(ds.end_date),
        "record_count": ds.record_count,
        "asset_count": int(n_assets),
        "benchmark_count": int(n_bench),
        "created_at": ds.created_at.isoformat(),
        "updated_at": ds.updated_at.isoformat(),
        "mode_label": "DEMO / SYNTHETIC DATA MODE" if ds.is_synthetic else f"LIVE PUBLIC DATA ({ds.source})",
    }


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None
