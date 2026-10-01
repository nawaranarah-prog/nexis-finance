"""linked reddit and x accounts

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01 22:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "linked_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=12), nullable=False),
        sa.Column("provider_user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_linked_provider_user"),
        sa.UniqueConstraint("user_id", "provider", name="uq_linked_user_provider"),
    )
    op.create_index("ix_linked_accounts_user_id", "linked_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_linked_accounts_user_id", table_name="linked_accounts")
    op.drop_table("linked_accounts")
