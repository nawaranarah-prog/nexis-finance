"""Financial entities, evidence records, evidence-backed relationships, members' saved research

Additive only: seven new tables, nothing existing is altered. Downgrade drops exactly these tables.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-10 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fin_entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("wikidata_qid", sa.String(20), unique=True),
        sa.Column("symbol", sa.String(32)),
        sa.Column("cik", sa.String(12)),
        sa.Column("country", sa.String(80)),
        sa.Column("relations_fetched_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_fin_entities_kind", "fin_entities", ["kind"])
    op.create_index("ix_fin_entities_symbol", "fin_entities", ["symbol"])

    op.create_table(
        "fin_entity_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("fin_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_norm", sa.String(200), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.UniqueConstraint("entity_id", "alias_norm", name="uq_fin_entity_aliases_entity_alias"),
    )
    op.create_index("ix_fin_entity_aliases_alias_norm", "fin_entity_aliases", ["alias_norm"])

    op.create_table(
        "evidence_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("url", sa.String(1500)),
        sa.Column("external_id", sa.String(200)),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("publisher", sa.String(160)),
        sa.Column("published_at", sa.DateTime()),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.Column("excerpt", sa.String(600)),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
    )

    op.create_table(
        "fin_relationships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("fin_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("fin_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rel_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.Column("note", sa.String(300)),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_verified", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_id", "target_id", "rel_type", name="uq_fin_relationships_edge"),
    )
    op.create_index("ix_fin_relationships_source_id", "fin_relationships", ["source_id"])
    op.create_index("ix_fin_relationships_target", "fin_relationships", ["target_id"])

    op.create_table(
        "relationship_evidence",
        sa.Column("relationship_id", sa.Integer(), sa.ForeignKey("fin_relationships.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("evidence_records.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "research_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("fin_entities.id", ondelete="SET NULL")),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_research_items_user_kind", "research_items", ["user_id", "kind", "updated_at"])

    op.create_table(
        "research_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("research_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_type", sa.String(16), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_research_runs_item_id", "research_runs", ["item_id"])


def downgrade() -> None:
    op.drop_table("research_runs")
    op.drop_table("research_items")
    op.drop_table("relationship_evidence")
    op.drop_table("fin_relationships")
    op.drop_table("evidence_records")
    op.drop_table("fin_entity_aliases")
    op.drop_table("fin_entities")
