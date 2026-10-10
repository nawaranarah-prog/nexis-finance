"""Financial entities, their evidence-backed relationships, and members' saved research.

* ``FinEntity`` / ``FinEntityAlias`` — companies, assets, industries, countries, exchanges… with the identifiers used to
  resolve them (Wikidata QID, Nexis symbol, SEC CIK) and normalised names/tickers for search and ambiguity checks.
* ``EvidenceRecord`` — where a claim comes from: source kind, URL/identifier, title, publisher, dates, a short excerpt.
  Bounded size; never a copy of a whole document.
* ``FinRelationship`` (+ ``RelationshipEvidence``) — a typed, directed edge between two entities with an evidence
  status (verified / historical / inferred / uncertain) and validity dates. No edge exists without evidence.
* ``ResearchItem`` / ``ResearchRun`` — what a member saves (graph investigation, scenario, thesis, report) and its
  immutable executions (inputs, data snapshot, model version, outputs). Past runs are never rewritten.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class FinEntity(Base):
    __tablename__ = "fin_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    # company | asset | industry | sector | country | exchange | commodity | indicator | organization | place
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300))
    wikidata_qid: Mapped[str | None] = mapped_column(String(20), unique=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)  # Nexis market symbol, e.g. EMAAR.AE
    cik: Mapped[str | None] = mapped_column(String(12))
    country: Mapped[str | None] = mapped_column(String(80))
    # when the entity's own relationships were last fetched from their source (None = never expanded)
    relations_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class FinEntityAlias(Base):
    __tablename__ = "fin_entity_aliases"
    __table_args__ = (UniqueConstraint("entity_id", "alias_norm", name="uq_fin_entity_aliases_entity_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("fin_entities.id", ondelete="CASCADE"), nullable=False)
    alias_norm: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # name | ticker | symbol | label


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # wikidata | sec | news | market_data | user
    url: Mapped[str | None] = mapped_column(String(1500))
    external_id: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(160))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(String(600))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class FinRelationship(Base):
    __tablename__ = "fin_relationships"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "rel_type", name="uq_fin_relationships_edge"),
        Index("ix_fin_relationships_target", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("fin_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("fin_entities.id", ondelete="CASCADE"), nullable=False)
    rel_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # verified | historical | inferred | uncertain
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(300))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_verified: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RelationshipEvidence(Base):
    __tablename__ = "relationship_evidence"

    relationship_id: Mapped[int] = mapped_column(ForeignKey("fin_relationships.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence_records.id", ondelete="CASCADE"), primary_key=True)


class ResearchItem(Base):
    __tablename__ = "research_items"
    __table_args__ = (Index("ix_research_items_user_kind", "user_id", "kind", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # graph | scenario | thesis | report
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("fin_entities.id", ondelete="SET NULL"))
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active | under_review | archived
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("research_items.id", ondelete="CASCADE"), nullable=False, index=True)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)  # scenario | thesis | graph_snapshot
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)  # data used, with timestamps and evidence ids
    output: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="completed", nullable=False)  # completed | failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


__all__ = [
    "EvidenceRecord",
    "FinEntity",
    "FinEntityAlias",
    "FinRelationship",
    "RelationshipEvidence",
    "ResearchItem",
    "ResearchRun",
]
