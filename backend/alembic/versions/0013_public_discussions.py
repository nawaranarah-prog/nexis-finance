"""nexis pulse: public discussions collected from other sites (origin and quote excerpts)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-04 20:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("pulse_discussions", schema=None) as b:
        b.add_column(sa.Column("origin", sa.JSON(), nullable=True))
        b.add_column(sa.Column("quotes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.execute("delete from pulse_discussions where kind = 'public'")
    with op.batch_alter_table("pulse_discussions", schema=None) as b:
        b.drop_column("quotes")
        b.drop_column("origin")
