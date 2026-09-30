"""Financial connectivity layer: connections, sync runs, imported accounts/holdings/transactions,
record-level lineage, external economic & regulatory data, audit log, API keys and webhooks."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utcnow


class Connection(Base, TimestampMixin):
    """A configured data source. Credentials are stored only as ciphertext and never returned."""

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # markets|accounts|economics|regulatory|files
    # connected | syncing | needs_attention | disconnected | unavailable
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="disconnected")
    auth_type: Mapped[str] = mapped_column(String(24), nullable=False)  # none | api_key | oauth | file
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    credential_hint: Mapped[str | None] = mapped_column(String(32))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    config: Mapped[dict | None] = mapped_column(JSON)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_sync_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("connections.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running|success|warning|failed
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    records_added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_removed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warnings: Mapped[list | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON)
    details: Mapped[dict | None] = mapped_column(JSON)


class ImportBatch(Base, TimestampMixin):
    """One uploaded file (or one API pull) normalised into canonical records."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"), index=True)
    source_label: Mapped[str] = mapped_column(String(160), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # file | api
    data_class: Mapped[str] = mapped_column(String(24), nullable=False)  # user_imported | real_external | synthetic
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(260))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    file_format: Mapped[str | None] = mapped_column(String(16))
    record_kind: Mapped[str] = mapped_column(String(24), nullable=False)  # holdings | transactions | market_data
    mapping: Mapped[dict | None] = mapped_column(JSON)
    mapping_confidence: Mapped[dict | None] = mapped_column(JSON)
    options: Mapped[dict | None] = mapped_column(JSON)
    as_of: Mapped[date | None] = mapped_column(Date)
    rows_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    issues: Mapped[list | None] = mapped_column(JSON)


class SourceRecord(Base):
    """The original row, kept verbatim so every normalised record is traceable."""

    __tablename__ = "source_records"
    __table_args__ = (Index("ix_source_records_batch_row", "batch_id", "row_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # imported | rejected | duplicate
    message: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(24))
    entity_id: Mapped[int | None] = mapped_column(Integer)


class Account(Base, TimestampMixin):
    """A brokerage/investment account. ``account_key`` = institution + external id, so the same
    account reported by two sources maps to one row (the basis for double-count prevention)."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    institution: Mapped[str] = mapped_column(String(120), nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_type: Mapped[str | None] = mapped_column(String(40))
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    first_connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"))


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (Index("ix_holdings_account_batch", "account_id", "batch_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    asset_class: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    average_cost: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    market_value: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    as_of: Mapped[date | None] = mapped_column(Date)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id", ondelete="SET NULL"))


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "dedupe_hash", name="uq_transactions_account_hash"),
        Index("ix_transactions_account_date", "account_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"))
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32))
    tx_type: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    description: Mapped[str | None] = mapped_column(String(300))
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(ForeignKey("source_records.id", ondelete="SET NULL"))


class EconomicSeries(Base):
    __tablename__ = "economic_series"
    __table_args__ = (UniqueConstraint("source", "series_code", name="uq_economic_series_source_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    series_code: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    units: Mapped[str | None] = mapped_column(String(80))
    frequency: Mapped[str | None] = mapped_column(String(24))
    country: Mapped[str | None] = mapped_column(String(64))
    last_retrieved: Mapped[datetime | None] = mapped_column(DateTime)
    coverage_start: Mapped[date | None] = mapped_column(Date)
    coverage_end: Mapped[date | None] = mapped_column(Date)


class EconomicObservation(Base):
    __tablename__ = "economic_observations"
    __table_args__ = (UniqueConstraint("series_id", "date", name="uq_economic_obs_series_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("economic_series.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)


class CompanyProfile(Base):
    """Issuer metadata from SEC EDGAR submissions (SIC industry, business address)."""

    __tablename__ = "company_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    cik: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sic: Mapped[str | None] = mapped_column(String(8))
    sic_description: Mapped[str | None] = mapped_column(String(200))
    sic_major_group: Mapped[str | None] = mapped_column(String(120))
    sic_division: Mapped[str | None] = mapped_column(String(120))
    business_state_or_country: Mapped[str | None] = mapped_column(String(8))
    business_country: Mapped[str | None] = mapped_column(String(64))
    state_of_incorporation: Mapped[str | None] = mapped_column(String(8))
    exchanges: Mapped[list | None] = mapped_column(JSON)
    fiscal_year_end: Mapped[str | None] = mapped_column(String(8))
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id", ondelete="SET NULL"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Fundamental(Base):
    __tablename__ = "fundamentals"
    __table_args__ = (UniqueConstraint("profile_id", "concept", "period_end", "fp", name="uq_fundamentals_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("company_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    concept: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    fy: Mapped[int | None] = mapped_column(Integer)
    fp: Mapped[str | None] = mapped_column(String(8))
    form: Mapped[str | None] = mapped_column(String(16))
    filed: Mapped[date | None] = mapped_column(Date)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSON)


class ApiKey(Base, TimestampMixin):
    """Only a SHA-256 hash of the key is stored; the plaintext is shown once at creation."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class WebhookEndpoint(Base, TimestampMixin):
    __tablename__ = "webhook_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))


class WebhookDelivery(Base, TimestampMixin):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_endpoint", "endpoint_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False)
    delivery_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|delivered|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
