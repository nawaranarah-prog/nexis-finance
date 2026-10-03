"""nexis pulse discussions: asset, title, sentiment and source on posts; topics; comment replies

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("posts", schema=None) as b:
        b.add_column(sa.Column("source", sa.String(length=16), nullable=False, server_default="nexis"))
        b.add_column(sa.Column("asset", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("asset_name", sa.String(length=160), nullable=True))
        b.add_column(sa.Column("title", sa.String(length=160), nullable=True))
        b.add_column(sa.Column("sentiment", sa.String(length=8), nullable=True))
        b.add_column(sa.Column("ai_sentiment", sa.String(length=8), nullable=True))
        b.add_column(sa.Column("edited_at", sa.DateTime(), nullable=True))
        b.create_index("ix_posts_asset_created", ["asset", "created_at"])
    with op.batch_alter_table("comments", schema=None) as b:
        b.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_comments_parent_id", "comments", ["parent_id"], ["id"], ondelete="CASCADE")
        b.create_index("ix_comments_parent_id", ["parent_id"])
    op.create_table(
        "post_topics",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("post_id", "topic"),
    )
    op.create_index("ix_post_topics_topic", "post_topics", ["topic"])


def downgrade() -> None:
    op.drop_index("ix_post_topics_topic", table_name="post_topics")
    op.drop_table("post_topics")
    with op.batch_alter_table("comments", schema=None) as b:
        b.drop_index("ix_comments_parent_id")
        b.drop_constraint("fk_comments_parent_id", type_="foreignkey")
        b.drop_column("parent_id")
    with op.batch_alter_table("posts", schema=None) as b:
        b.drop_index("ix_posts_asset_created")
        for col in ("edited_at", "ai_sentiment", "sentiment", "title", "asset_name", "asset", "source"):
            b.drop_column(col)
