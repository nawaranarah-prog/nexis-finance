"""billing events: processed status and traceable ids (follow-up to 0014, which is already applied)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-05 16:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("billing_events", schema=None) as b:
        b.add_column(sa.Column("status", sa.String(length=12), nullable=False, server_default="processed"))
        b.add_column(sa.Column("details", sa.JSON(), nullable=True))
    # Usage recorded for the report and AI-brief allowances that PR #8 introduced and the final plans dropped.
    op.execute("delete from usage_events where feature in ('report', 'brief')")


def downgrade() -> None:
    with op.batch_alter_table("billing_events", schema=None) as b:
        b.drop_column("details")
        b.drop_column("status")
