"""Market evidence and the Nexis Pulse market debate, personal holdings, and per-user notifications.

* ``SourceEvent`` — one piece of real, verifiable evidence (a news headline, a company filing, a market move, a
  Nexis Research note) with its link and publication time exactly as the source gave them. It is the evidence
  store for Nexis Pulse: ``sentiment``/``analysis`` hold how the Pulse pipeline read it.
* ``SourceEventAsset`` — which assets an evidence item is about (indexed, so per-asset lookups stay cheap).
* ``MarketDiscussion`` — the current structured market debate for one asset (bull case, bear case, themes, score),
  synthesised from evidence and cached. Not a user account and not written by anyone in particular.
* ``PulseSnapshot`` — one recorded Pulse reading per asset per day. Only days the pipeline actually ran exist,
  so sentiment history is never back-filled or invented.
* ``Persona`` — retired. Nexis no longer generates fictional investors; the table only remains so existing rows
  can be removed with ``python -m app.seeds.retire_personas``.
* ``UserHolding`` — what a member owns. Private: only ever read through the owner's session.
* ``UserNotification`` / ``NotificationPreference`` / ``AlertRule`` — personal alerts about tracked assets.
* ``DigestRecord`` — digests prepared for email; ``status`` records honestly whether anything was sent.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Persona(Base, TimestampMixin):
    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    archetype: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Structured profile: experience, risk, asset classes, sectors, markets, traits, voice, framework, biases, topics.
    traits: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Lightweight memory: {"NVDA": {"stance": "bearish", "point": "valuation", "at": "..."}} — capped in size.
    memory: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime)


class SourceEvent(Base, TimestampMixin):
    __tablename__ = "source_events"
    __table_args__ = (Index("ix_source_events_status_published", "status", "published_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # news | price_move | research | earnings | dividend | filing | macro | rates
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # Which provider found it (newsfeed, market_data, nexis_research, …).
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1500))
    publisher: Mapped[str | None] = mapped_column(String(160))
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Verified facts only, as given by the source (headline, summary, market numbers).
    facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    assets: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    topics: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    importance: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # new → discussed | skipped (with reason in facts["skip"])
    status: Mapped[str] = mapped_column(String(12), default="new", nullable=False)
    external_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    # How Nexis Pulse read this item: positive | neutral | negative (null until analysed).
    sentiment: Mapped[str | None] = mapped_column(String(8), index=True)
    # {"score": -1..1, "method": "lexicon" | "ai", "topics": [...], "terms": [...], "model": ...}
    analysis: Mapped[dict | None] = mapped_column(JSON)


class SourceEventAsset(Base):
    """An evidence item is about this asset."""

    __tablename__ = "source_event_assets"
    __table_args__ = (Index("ix_source_event_assets_symbol_event", "symbol", "event_id"),)

    event_id: Mapped[int] = mapped_column(ForeignKey("source_events.id", ondelete="CASCADE"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)


class MarketDiscussion(Base, TimestampMixin):
    """The current Nexis Pulse market debate for one asset, synthesised from evidence (one row per asset)."""

    __tablename__ = "market_discussions"
    __table_args__ = (Index("ix_market_discussions_computed", "computed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(160))
    exchange: Mapped[str | None] = mapped_column(String(80))
    asset_type: Mapped[str | None] = mapped_column(String(24))
    # market_debate | market_update | earnings_debate | valuation_debate | sector_debate | macro_debate | event_analysis
    content_type: Mapped[str] = mapped_column(String(24), default="market_debate", nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)  # what's happening
    bull_case: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # [{"point", "source_ids"}]
    bear_case: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    debate: Mapped[dict | None] = mapped_column(JSON)  # {"question", "bull_side", "bear_side"}
    split: Mapped[str | None] = mapped_column(Text)  # where the debate is split
    what_changed: Mapped[dict | None] = mapped_column(JSON)
    analysis: Mapped[str | None] = mapped_column(Text)  # Nexis analysis
    themes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # extremely_unfavorable | unfavorable | mixed | favorable | extremely_favorable | insufficient
    sentiment: Mapped[str] = mapped_column(String(24), default="insufficient", nullable=False, index=True)
    pulse_score: Mapped[int | None] = mapped_column(Integer)
    # Evidence coverage: none | limited | developing | strong
    confidence: Mapped[str] = mapped_column(String(12), default="none", nullable=False)
    source_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # The evidence as analysed (including live market data, which is not stored as a SourceEvent).
    evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # distribution, contributors, quote, trend
    # How the narrative was written: "ai" (model synthesis of the evidence) or "rules" (deterministic template).
    method: Mapped[str] = mapped_column(String(8), default="rules", nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))
    evidence_hash: Mapped[str | None] = mapped_column(String(40))
    synthesized_at: Mapped[datetime | None] = mapped_column(DateTime)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PulseSnapshot(Base, TimestampMixin):
    """One recorded Pulse reading for an asset on one day (the latest run of that day)."""

    __tablename__ = "pulse_snapshots"
    __table_args__ = (UniqueConstraint("symbol", "day", name="uq_pulse_snapshots_symbol_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    sentiment: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[str] = mapped_column(String(12), nullable=False)
    items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # {"positive": n, "neutral": n, "negative": n}, top themes with their net contribution, evidence ids used
    distribution: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    themes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class UserHolding(Base, TimestampMixin):
    __tablename__ = "user_holdings"
    __table_args__ = (Index("ix_user_holdings_user_active", "user_id", "archived_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    portfolio: Mapped[str] = mapped_column(String(60), default="Main", nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(160))
    # stock | etf | fund | bond | crypto | other
    asset_type: Mapped[str] = mapped_column(String(12), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    purchase_price: Mapped[float | None] = mapped_column(Float)
    purchase_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(8))
    note: Mapped[str | None] = mapped_column(String(300))
    # Archived investments are kept (read-only) but don't count toward the plan's allowance. ``archived_reason`` is
    # "plan" (over the allowance after a downgrade: restored automatically when the allowance grows) or "member".
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    archived_reason: Mapped[str | None] = mapped_column(String(12))


class UserNotification(Base, TimestampMixin):
    __tablename__ = "user_notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_user_notifications_dedupe"),
        Index("ix_user_notifications_user_read", "user_id", "read_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Preference category: important | news | earnings | portfolio | watchlist | pulse
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    # Event type: news | earnings | dividend | price_move | filing | regulation | rates | macro | bond | pulse
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), default="info", nullable=False)  # info | notable | high
    symbol: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(String(160))
    source_url: Mapped[str | None] = mapped_column(String(1500))
    link: Mapped[str | None] = mapped_column(String(300))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("source_events.id", ondelete="SET NULL"))
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    # immediate (shown in the bell right away) | digest (collected into the member's daily/weekly digest)
    delivery: Mapped[str] = mapped_column(String(10), default="immediate", server_default="immediate", nullable=False)
    digested_at: Mapped[datetime | None] = mapped_column(DateTime)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # {"important": "immediate", "news": "daily", ...} — immediate | daily | weekly | off
    channels: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Price-move alerts fire at or above this daily move (percent).
    price_move_pct: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)


class AlertRule(Base, TimestampMixin):
    __tablename__ = "alert_rules"
    __table_args__ = (UniqueConstraint("user_id", "symbol", "event_type", name="uq_alert_rules_user_symbol_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Null symbol = every asset the user tracks.
    symbol: Mapped[str | None] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float)


class DigestRecord(Base, TimestampMixin):
    __tablename__ = "digest_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(8), nullable=False)  # daily | weekly
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    # prepared (no email provider configured) | sent | failed
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(300))
