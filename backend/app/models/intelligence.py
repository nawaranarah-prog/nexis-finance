"""Pulse personas and source events, personal holdings, and per-user notifications.

* ``Persona`` — the profile of a Nexis-generated (AI) participant. Its public identity is a ``users`` row with
  ``kind = "persona"``; it can never sign in and is always labelled as generated.
* ``SourceEvent`` — a real, verifiable event (a news article, a market move, a Nexis Research note) that Pulse
  discussions are grounded in. Generated discussions point at the event they discuss (``posts.event_id``).
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


class UserHolding(Base, TimestampMixin):
    __tablename__ = "user_holdings"

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
