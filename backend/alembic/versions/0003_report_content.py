"""store generated report bytes in the database

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30 15:10:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Serverless deployments have no durable disk, so the PDF itself is kept with its metadata.
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.add_column(sa.Column('content', sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_column('content')
