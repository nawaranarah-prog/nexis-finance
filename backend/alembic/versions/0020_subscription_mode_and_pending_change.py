"""Subscriptions: Stripe mode (test or live) and a plan change waiting for payment

Additive only: four nullable columns on ``subscriptions``. Existing rows keep working (an unknown mode is treated as a
test subscription, which is what every subscription before the live launch is). Downgrade drops exactly these columns.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-13 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions") as t:
        t.add_column(sa.Column("livemode", sa.Boolean()))
        t.add_column(sa.Column("pending_plan", sa.String(16)))
        t.add_column(sa.Column("pending_invoice_id", sa.String(80)))
        t.add_column(sa.Column("pending_expires_at", sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as t:
        for c in ("pending_expires_at", "pending_invoice_id", "pending_plan", "livemode"):
            t.drop_column(c)
