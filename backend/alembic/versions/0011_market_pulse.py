"""nexis pulse market debate: evidence analysis, evidence-asset links, market discussions, daily snapshots

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-04 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_events", schema=None) as b:
        b.add_column(sa.Column("sentiment", sa.String(length=8), nullable=True))
        b.add_column(sa.Column("analysis", sa.JSON(), nullable=True))
        b.create_index("ix_source_events_sentiment", ["sentiment"])

    op.create_table(
        "source_event_assets",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["source_events.id"], name=op.f("fk_source_event_assets_event_id_source_events"),
                                ondelete="CASCADE"),  # fmt: skip
        sa.PrimaryKeyConstraint("event_id", "symbol", name=op.f("pk_source_event_assets")),
    )
    op.create_index("ix_source_event_assets_symbol_event", "source_event_assets", ["symbol", "event_id"])

    op.create_table(
        "market_discussions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=True),
        sa.Column("exchange", sa.String(length=80), nullable=True),
        sa.Column("asset_type", sa.String(length=24), nullable=True),
        sa.Column("content_type", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("bull_case", sa.JSON(), nullable=False),
        sa.Column("bear_case", sa.JSON(), nullable=False),
        sa.Column("debate", sa.JSON(), nullable=True),
        sa.Column("split", sa.Text(), nullable=True),
        sa.Column("what_changed", sa.JSON(), nullable=True),
        sa.Column("analysis", sa.Text(), nullable=True),
        sa.Column("themes", sa.JSON(), nullable=False),
        sa.Column("sentiment", sa.String(length=24), nullable=False),
        sa.Column("pulse_score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.String(length=12), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("evidence_hash", sa.String(length=40), nullable=True),
        sa.Column("synthesized_at", sa.DateTime(), nullable=True),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_discussions")),
        sa.UniqueConstraint("symbol", name=op.f("uq_market_discussions_symbol")),
    )
    op.create_index("ix_market_discussions_computed", "market_discussions", ["computed_at"])
    op.create_index("ix_market_discussions_content_type", "market_discussions", ["content_type"])
    op.create_index("ix_market_discussions_sentiment", "market_discussions", ["sentiment"])

    op.create_table(
        "pulse_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("sentiment", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.String(length=12), nullable=False),
        sa.Column("items", sa.Integer(), nullable=False),
        sa.Column("distribution", sa.JSON(), nullable=False),
        sa.Column("themes", sa.JSON(), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pulse_snapshots")),
        sa.UniqueConstraint("symbol", "day", name="uq_pulse_snapshots_symbol_day"),
    )
    op.create_index("ix_pulse_snapshots_symbol", "pulse_snapshots", ["symbol"])

    # Link existing evidence to its assets (the JSON ``assets`` column stays as the source of truth for old code).
    conn = op.get_bind()
    events = sa.table("source_events", sa.column("id", sa.Integer), sa.column("assets", sa.JSON))
    links = sa.table("source_event_assets", sa.column("event_id", sa.Integer), sa.column("symbol", sa.String))
    rows = [
        {"event_id": eid, "symbol": str(sym)[:32]}
        for eid, assets in conn.execute(sa.select(events.c.id, events.c.assets))
        for sym in dict.fromkeys(assets or [])
        if sym
    ]
    if rows:
        op.bulk_insert(links, rows)


def downgrade() -> None:
    op.drop_index("ix_pulse_snapshots_symbol", table_name="pulse_snapshots")
    op.drop_table("pulse_snapshots")
    op.drop_index("ix_market_discussions_sentiment", table_name="market_discussions")
    op.drop_index("ix_market_discussions_content_type", table_name="market_discussions")
    op.drop_index("ix_market_discussions_computed", table_name="market_discussions")
    op.drop_table("market_discussions")
    op.drop_index("ix_source_event_assets_symbol_event", table_name="source_event_assets")
    op.drop_table("source_event_assets")
    with op.batch_alter_table("source_events", schema=None) as b:
        b.drop_index("ix_source_events_sentiment")
        b.drop_column("analysis")
        b.drop_column("sentiment")
