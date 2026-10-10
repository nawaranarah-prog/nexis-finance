"""Accounts: email verification time; reports: owner (private reports)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-10 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as b:
        b.add_column(sa.Column("email_verified_at", sa.DateTime(), nullable=True))
    # Reports made by a member belong to them; reports of the shared research workspace stay public (owner NULL).
    with op.batch_alter_table("reports", schema=None) as b:
        b.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_reports_owner_id_users", "users", ["owner_id"], ["id"], ondelete="CASCADE")
        b.create_index("ix_reports_owner_id", ["owner_id"])
    # Accounts that signed in with Google have an address Google already verified.
    op.execute("update users set email_verified_at = created_at where auth_provider = 'google' and email is not null")


def downgrade() -> None:
    with op.batch_alter_table("reports", schema=None) as b:
        b.drop_index("ix_reports_owner_id")
        b.drop_constraint("fk_reports_owner_id_users", type_="foreignkey")
        b.drop_column("owner_id")
    with op.batch_alter_table("users", schema=None) as b:
        b.drop_column("email_verified_at")
