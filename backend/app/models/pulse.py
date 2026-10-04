"""Nexis Pulse: anonymous community discussions, Nexis editorial discussions, and their moderation.

Pulse has its own tables, separate from the Finstagram feed (``posts`` / ``comments``), because Finstagram is a
public-identity product and Pulse is not: nothing here is ever served with the author attached.

* ``PulseDiscussion`` — one discussion. ``kind = "community"`` is written by a member (``author_id``, never
  exposed publicly); ``kind = "editorial"`` is written by Nexis (no author) and carries the structured context:
  what happened, what is debated, bull / bear / neutral points, open questions.
* ``PulseDiscussionSource`` — a source an editorial discussion relies on (title, link, publisher, dates).
* ``PulseDiscussionUpdate`` — append-only history of how an editorial discussion changed, each with a timestamp.
* ``PulseComment`` — an anonymous comment or reply. Comments are never edited after posting, so the record of
  what was said stays intact; the author can delete (the text is then erased) and moderators can remove.
* ``PulseReaction`` — agree / disagree / interesting on a discussion or a comment, one of each per member.
* ``PulseFollow`` / ``PulseSave`` — a member follows (gets updates about) or saves a discussion. Private.
* ``ContentReport`` / ``ModerationAction`` — reports from members and the audit trail of every moderation
  decision, automatic or manual.
* ``LegalAcceptance`` — which versions of the Terms and Privacy Policy a member accepted, and when.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utcnow


class PulseDiscussion(Base, TimestampMixin):
    __tablename__ = "pulse_discussions"
    __table_args__ = (
        Index("ix_pulse_discussions_status_activity", "status", "last_activity_at"),
        Index("ix_pulse_discussions_status_created", "status", "created_at"),
        Index("ix_pulse_discussions_symbol", "primary_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Random, URL-safe identifier used everywhere publicly (internal ids are never exposed).
    public_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # community | editorial
    # Internal only: who wrote a community discussion (moderation, rate limits, deletion). Null for editorial.
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The author's own framing of a community discussion: bullish | bearish | neutral | question (optional).
    stance: Mapped[str | None] = mapped_column(String(10))
    primary_symbol: Mapped[str | None] = mapped_column(String(32))
    asset_name: Mapped[str | None] = mapped_column(String(160))
    symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # ---- editorial structure (empty for community discussions)
    what_happened: Mapped[str | None] = mapped_column(Text)
    debate: Mapped[str | None] = mapped_column(Text)  # what is being debated
    bull_case: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # [{"point", "sources": [source ids]}]
    bear_case: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    neutral_case: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    open_questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # [str]
    ai_assisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))
    # Dedupe key for discussions the editorial engine maintains ("asset:NVDA", "theme:rates").
    editorial_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(40))
    # ---- state
    # visible | held (waiting for review, hidden publicly) | removed (by a moderator) | deleted (by the author)
    status: Mapped[str] = mapped_column(String(10), default="visible", nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # ---- counters (real activity only)
    comment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    participant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    follower_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    save_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    agree_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disagree_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    interesting_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # When the editorial content last changed (null for community discussions).
    content_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The Finstagram-era post this discussion was migrated from (old links keep working).
    legacy_post_id: Mapped[int | None] = mapped_column(Integer, unique=True)


class PulseDiscussionTopic(Base):
    __tablename__ = "pulse_discussion_topics"

    discussion_id: Mapped[int] = mapped_column(ForeignKey("pulse_discussions.id", ondelete="CASCADE"), primary_key=True)
    topic: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)


class PulseDiscussionAsset(Base):
    """A discussion is about this asset (indexed, so per-asset and personalised lookups stay cheap)."""

    __tablename__ = "pulse_discussion_assets"
    __table_args__ = (Index("ix_pulse_discussion_assets_symbol", "symbol", "discussion_id"),)

    discussion_id: Mapped[int] = mapped_column(ForeignKey("pulse_discussions.id", ondelete="CASCADE"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)


class PulseDiscussionSource(Base):
    __tablename__ = "pulse_discussion_sources"
    __table_args__ = (UniqueConstraint("discussion_id", "url", name="uq_pulse_discussion_sources_discussion_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    discussion_id: Mapped[int] = mapped_column(ForeignKey("pulse_discussions.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1500), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(160))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("source_events.id", ondelete="SET NULL"))


class PulseDiscussionUpdate(Base):
    """One entry in an editorial discussion's history. Append-only."""

    __tablename__ = "pulse_discussion_updates"
    __table_args__ = (Index("ix_pulse_discussion_updates_discussion_created", "discussion_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    discussion_id: Mapped[int] = mapped_column(ForeignKey("pulse_discussions.id", ondelete="CASCADE"), nullable=False)
    # opened | development | what_changed | new_bull | new_bear | open_question | correction
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    headline: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    source_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class PulseComment(Base, TimestampMixin):
    __tablename__ = "pulse_comments"
    __table_args__ = (Index("ix_pulse_comments_discussion_created", "discussion_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    discussion_id: Mapped[int] = mapped_column(ForeignKey("pulse_discussions.id", ondelete="CASCADE"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("pulse_comments.id", ondelete="CASCADE"), index=True)
    # Internal only (moderation, rate limits, deletion); never serialized.
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # agree | disagree | question | null — how the author framed the reply (optional).
    stance: Mapped[str | None] = mapped_column(String(10))
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="visible", nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    agree_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disagree_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    interesting_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PulseReaction(Base, TimestampMixin):
    __tablename__ = "pulse_reactions"
    __table_args__ = (
        UniqueConstraint("user_id", "target_type", "target_id", "kind", name="uq_pulse_reactions_user_target_kind"),
        Index("ix_pulse_reactions_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(12), nullable=False)  # discussion | comment
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # agree | disagree | interesting


class PulseFollow(Base, TimestampMixin):
    __tablename__ = "pulse_follows"
    __table_args__ = (UniqueConstraint("user_id", "discussion_id", name="uq_pulse_follows_user_discussion"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    discussion_id: Mapped[int] = mapped_column(ForeignKey("pulse_discussions.id", ondelete="CASCADE"), nullable=False, index=True)


class PulseSave(Base, TimestampMixin):
    __tablename__ = "pulse_saves"
    __table_args__ = (UniqueConstraint("user_id", "discussion_id", name="uq_pulse_saves_user_discussion"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    discussion_id: Mapped[int] = mapped_column(ForeignKey("pulse_discussions.id", ondelete="CASCADE"), nullable=False, index=True)


class ContentReport(Base, TimestampMixin):
    __tablename__ = "content_reports"
    __table_args__ = (
        UniqueConstraint("reporter_id", "target_type", "target_id", name="uq_content_reports_reporter_target"),
        Index("ix_content_reports_status_created", "status", "created_at"),
        Index("ix_content_reports_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(String(12), nullable=False)  # discussion | comment
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)  # open | actioned | dismissed
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ModerationAction(Base):
    """Audit trail of every moderation decision. Append-only; ``actor_id`` null means the automatic checks."""

    __tablename__ = "moderation_actions"
    __table_args__ = (Index("ix_moderation_actions_target", "target_type", "target_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(12), nullable=False)  # discussion | comment | user
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # auto_flag | auto_hold | approve | hide | remove | restore | lock | unlock | dismiss | suspend | unsuspend
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class LegalAcceptance(Base):
    __tablename__ = "legal_acceptances"
    __table_args__ = (Index("ix_legal_acceptances_user_accepted", "user_id", "accepted_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    terms_version: Mapped[str] = mapped_column(String(20), nullable=False)
    privacy_version: Mapped[str] = mapped_column(String(20), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # signup | prompt (accepted after signing in with Google/phone, or after the documents changed)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
