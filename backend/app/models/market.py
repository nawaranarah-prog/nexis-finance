"""Market data, datasets, ingestion and data-quality tables."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow


class Dataset(Base, TimestampMixin):
    """A versioned collection of assets and their price history (a research universe)."""

    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # synthetic | public | csv
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Incremented every time an ingestion changes stored records.
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    assets: Mapped[list[Asset]] = relationship(back_populates="dataset", cascade="all, delete-orphan")

    @property
    def version_label(self) -> str:
        return f"{self.code}-v{self.version}"


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("dataset_id", "symbol", name="uq_assets_dataset_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, default="equity")
    sector: Mapped[str | None] = mapped_column(String(64))
    is_benchmark: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    attributes: Mapped[dict | None] = mapped_column(JSON)

    dataset: Mapped[Dataset] = relationship(back_populates="assets")


class MarketData(Base):
    """Daily OHLCV bar. One row per (asset, date), enforced by a unique composite index."""

    __tablename__ = "market_data"
    __table_args__ = (
        UniqueConstraint("asset_id", "date", name="uq_market_data_asset_date"),
        Index("ix_market_data_date", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="incremental")  # full | incremental
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running|success|warning|failed
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    requested_start: Mapped[date | None] = mapped_column(Date)
    requested_end: Mapped[date | None] = mapped_column(Date)
    symbols: Mapped[list | None] = mapped_column(JSON)
    records_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_skipped_existing: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicates: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    warnings: Mapped[list | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON)


class DataQualityRun(Base, TimestampMixin):
    __tablename__ = "data_quality_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_version: Mapped[str | None] = mapped_column(String(80))
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pass | warning | fail
    components: Mapped[dict] = mapped_column(JSON, nullable=False)
    checks: Mapped[list] = mapped_column(JSON, nullable=False)
    per_symbol: Mapped[list | None] = mapped_column(JSON)
    duration_seconds: Mapped[float | None] = mapped_column(Float)


class DataQualityIssue(Base, TimestampMixin):
    """Individual record-level finding from ingestion validation or a data-quality run."""

    __tablename__ = "data_quality_issues"
    __table_args__ = (Index("ix_dq_issues_dataset_check", "dataset_id", "check"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="CASCADE"))
    dq_run_id: Mapped[int | None] = mapped_column(ForeignKey("data_quality_runs.id", ondelete="CASCADE"))
    symbol: Mapped[str | None] = mapped_column(String(32))
    date: Mapped[date | None] = mapped_column(Date)
    check: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # completeness|validity|uniqueness|consistency|outlier|freshness
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # error | warning | info
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # rejected | stored | flagged | deduplicated
    detail: Mapped[str] = mapped_column(Text, nullable=False)
