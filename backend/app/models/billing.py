"""Nexis Pro billing: subscriptions, processed payment-provider events, and usage of metered features.

* ``Subscription`` — one row per account that has ever started checkout. ``provider`` is "stripe" today; another
  provider (for example Tabby) would fill the same row shape with its own ids. Status and period dates are only ever
  written from the provider's own data (webhooks, or a fetch of the subscription), never from the browser.
* ``BillingEvent`` — every provider event id processed, so a repeated webhook delivery is recognised and ignored.
* ``UsageEvent`` — one row per use of a metered feature (AI Advisor question, PDF report, AI brief), counted
  server-side against the plan's limit.

No card data is stored anywhere in Nexis: Stripe Checkout collects and keeps it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utcnow


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), default="stripe", nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    subscription_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    price_id: Mapped[str | None] = mapped_column(String(80))
    plan: Mapped[str | None] = mapped_column(String(16))  # plus_monthly | pro_monthly (older: pro_yearly)
    # The provider's status, verbatim: active | trialing | past_due | canceled | incomplete | incomplete_expired | unpaid | paused
    status: Mapped[str | None] = mapped_column(String(24), index=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Set when the latest invoice failed and cleared when one is paid (drives the "update your payment method" notice).
    payment_failed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The investments (symbols) the member chose to keep active when their plan's allowance shrinks (cancellation or
    # a move to a smaller plan). Applied only once the provider confirms the change; never chosen by Nexis.
    downgrade_keep: Mapped[list | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class BillingEvent(Base):
    __tablename__ = "billing_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    event_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # processed (state saved) | ignored (an event type Nexis doesn't act on, or no matching account)
    status: Mapped[str] = mapped_column(String(12), default="processed", server_default="processed", nullable=False)
    # Ids that make the event traceable without storing the payload: object, customer, subscription, invoice.
    details: Mapped[dict | None] = mapped_column(JSON)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_user_feature_created", "user_id", "feature", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    feature: Mapped[str] = mapped_column(String(24), nullable=False)  # advisor | report | brief
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    units: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
