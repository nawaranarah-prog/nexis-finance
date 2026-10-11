"""Investor profiles (what the AI Advisor tailors to) and the advice log (what it answered)

Additive only: two new tables, nothing existing is altered. Downgrade drops exactly these tables.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-11 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investor_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("goals", sa.JSON(), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("monthly_budget_aed", sa.Integer()),
        sa.Column("savings_band", sa.String(16)),
        sa.Column("experience", sa.String(16), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("risk_band", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "advice_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("profile", sa.JSON()),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("model", sa.String(80)),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_advice_log_user_created", "advice_log", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("advice_log")
    op.drop_table("investor_profiles")
