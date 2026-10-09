"""Plus/Pro tiers: archived investments and the member's downgrade selection

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-09 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_holdings", schema=None) as b:
        b.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("archived_reason", sa.String(length=12), nullable=True))
    op.create_index("ix_user_holdings_user_active", "user_holdings", ["user_id", "archived_at"])
    with op.batch_alter_table("subscriptions", schema=None) as b:
        b.add_column(sa.Column("downgrade_keep", sa.JSON(), nullable=True))
    # Pulse discussions and comments are no longer metered (participation is free on every plan).
    op.execute("delete from usage_events where feature in ('pulse_discussions', 'pulse_comments')")


def downgrade() -> None:
    with op.batch_alter_table("subscriptions", schema=None) as b:
        b.drop_column("downgrade_keep")
    op.drop_index("ix_user_holdings_user_active", table_name="user_holdings")
    with op.batch_alter_table("user_holdings", schema=None) as b:
        b.drop_column("archived_reason")
        b.drop_column("archived_at")
