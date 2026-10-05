"""nexis pro billing: subscriptions, processed billing events, usage of metered features

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-05 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False, server_default="stripe"),
        sa.Column("customer_id", sa.String(length=80), nullable=True),
        sa.Column("subscription_id", sa.String(length=80), nullable=True),
        sa.Column("price_id", sa.String(length=80), nullable=True),
        sa.Column("plan", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("current_period_start", sa.DateTime(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("payment_failed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_subscriptions_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint("user_id", name=op.f("uq_subscriptions_user_id")),
        sa.UniqueConstraint("customer_id", name=op.f("uq_subscriptions_customer_id")),
        sa.UniqueConstraint("subscription_id", name=op.f("uq_subscriptions_subscription_id")),
    )
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])
    op.create_table(
        "billing_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_billing_events_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_billing_events_event_id")),
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("feature", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_usage_events_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
    )
    op.create_index("ix_usage_events_user_feature_created", "usage_events", ["user_id", "feature", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_user_feature_created", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_table("billing_events")
    op.drop_index("ix_subscriptions_status", table_name="subscriptions")
    op.drop_table("subscriptions")
