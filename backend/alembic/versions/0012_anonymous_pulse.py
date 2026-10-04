"""anonymous nexis pulse: discussions, comments, reactions, follows, saves, sources, updates, reports, moderation,
legal acceptance, member roles

Existing Pulse discussions (Finstagram ``posts`` with an asset and a title, from members or Nexis Research) are
copied into ``pulse_discussions`` with their human comments and saves, then hidden from the Finstagram feed so they
are no longer shown next to the author's public profile. Content written by the retired generated personas is not
copied. The downgrade unhides the original posts.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-04 12:00:00
"""
from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _public_id() -> str:
    while True:
        v = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        if not v.isdigit():
            return v


def _counters() -> list[sa.Column]:
    return [sa.Column(n, sa.Integer(), nullable=False, server_default="0") for n in ("agree_count", "disagree_count", "interesting_count")]


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as b:
        b.add_column(sa.Column("role", sa.String(length=12), nullable=False, server_default="member"))
        b.add_column(sa.Column("posting_suspended_until", sa.DateTime(), nullable=True))

    op.create_table(
        "pulse_discussions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("stance", sa.String(length=10), nullable=True),
        sa.Column("primary_symbol", sa.String(length=32), nullable=True),
        sa.Column("asset_name", sa.String(length=160), nullable=True),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("what_happened", sa.Text(), nullable=True),
        sa.Column("debate", sa.Text(), nullable=True),
        sa.Column("bull_case", sa.JSON(), nullable=False),
        sa.Column("bear_case", sa.JSON(), nullable=False),
        sa.Column("neutral_case", sa.JSON(), nullable=False),
        sa.Column("open_questions", sa.JSON(), nullable=False),
        sa.Column("ai_assisted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("editorial_key", sa.String(length=80), nullable=True),
        sa.Column("evidence_hash", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="visible"),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("flags", sa.JSON(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("participant_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("follower_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("save_count", sa.Integer(), nullable=False, server_default="0"),
        *_counters(),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("content_updated_at", sa.DateTime(), nullable=True),
        sa.Column("legacy_post_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name=op.f("fk_pulse_discussions_author_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pulse_discussions")),
        sa.UniqueConstraint("public_id", name=op.f("uq_pulse_discussions_public_id")),
        sa.UniqueConstraint("editorial_key", name=op.f("uq_pulse_discussions_editorial_key")),
        sa.UniqueConstraint("legacy_post_id", name=op.f("uq_pulse_discussions_legacy_post_id")),
    )
    op.create_index("ix_pulse_discussions_author_id", "pulse_discussions", ["author_id"])
    op.create_index("ix_pulse_discussions_status_activity", "pulse_discussions", ["status", "last_activity_at"])
    op.create_index("ix_pulse_discussions_status_created", "pulse_discussions", ["status", "created_at"])
    op.create_index("ix_pulse_discussions_symbol", "pulse_discussions", ["primary_symbol"])

    op.create_table(
        "pulse_discussion_topics",
        sa.Column("discussion_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["discussion_id"], ["pulse_discussions.id"],
                                name=op.f("fk_pulse_discussion_topics_discussion_id_pulse_discussions"), ondelete="CASCADE"),  # fmt: skip
        sa.PrimaryKeyConstraint("discussion_id", "topic", name=op.f("pk_pulse_discussion_topics")),
    )
    op.create_index("ix_pulse_discussion_topics_topic", "pulse_discussion_topics", ["topic"])

    op.create_table(
        "pulse_discussion_assets",
        sa.Column("discussion_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["discussion_id"], ["pulse_discussions.id"],
                                name=op.f("fk_pulse_discussion_assets_discussion_id_pulse_discussions"), ondelete="CASCADE"),  # fmt: skip
        sa.PrimaryKeyConstraint("discussion_id", "symbol", name=op.f("pk_pulse_discussion_assets")),
    )
    op.create_index("ix_pulse_discussion_assets_symbol", "pulse_discussion_assets", ["symbol", "discussion_id"])

    with op.batch_alter_table("user_notifications", schema=None) as b:
        b.add_column(sa.Column("delivery", sa.String(length=10), nullable=False, server_default="immediate"))

    op.create_table(
        "pulse_discussion_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("discussion_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1500), nullable=False),
        sa.Column("publisher", sa.String(length=160), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["discussion_id"], ["pulse_discussions.id"],
                                name=op.f("fk_pulse_discussion_sources_discussion_id_pulse_discussions"), ondelete="CASCADE"),  # fmt: skip
        sa.ForeignKeyConstraint(["event_id"], ["source_events.id"], name=op.f("fk_pulse_discussion_sources_event_id_source_events"),
                                ondelete="SET NULL"),  # fmt: skip
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pulse_discussion_sources")),
        sa.UniqueConstraint("discussion_id", "url", name="uq_pulse_discussion_sources_discussion_url"),
    )
    op.create_index("ix_pulse_discussion_sources_discussion_id", "pulse_discussion_sources", ["discussion_id"])

    op.create_table(
        "pulse_discussion_updates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("discussion_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("headline", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["discussion_id"], ["pulse_discussions.id"],
                                name=op.f("fk_pulse_discussion_updates_discussion_id_pulse_discussions"), ondelete="CASCADE"),  # fmt: skip
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pulse_discussion_updates")),
    )
    op.create_index("ix_pulse_discussion_updates_discussion_created", "pulse_discussion_updates", ["discussion_id", "created_at"])

    op.create_table(
        "pulse_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("discussion_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("stance", sa.String(length=10), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="visible"),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("flags", sa.JSON(), nullable=False),
        *_counters(),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["discussion_id"], ["pulse_discussions.id"],
                                name=op.f("fk_pulse_comments_discussion_id_pulse_discussions"), ondelete="CASCADE"),  # fmt: skip
        sa.ForeignKeyConstraint(["parent_id"], ["pulse_comments.id"], name=op.f("fk_pulse_comments_parent_id_pulse_comments"),
                                ondelete="CASCADE"),  # fmt: skip
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name=op.f("fk_pulse_comments_author_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pulse_comments")),
    )
    op.create_index("ix_pulse_comments_discussion_created", "pulse_comments", ["discussion_id", "created_at"])
    op.create_index("ix_pulse_comments_parent_id", "pulse_comments", ["parent_id"])
    op.create_index("ix_pulse_comments_author_id", "pulse_comments", ["author_id"])

    op.create_table(
        "pulse_reactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=12), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_pulse_reactions_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pulse_reactions")),
        sa.UniqueConstraint("user_id", "target_type", "target_id", "kind", name="uq_pulse_reactions_user_target_kind"),
    )
    op.create_index("ix_pulse_reactions_user_id", "pulse_reactions", ["user_id"])
    op.create_index("ix_pulse_reactions_target", "pulse_reactions", ["target_type", "target_id"])

    for name in ("pulse_follows", "pulse_saves"):
        op.create_table(
            name,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("discussion_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f(f"fk_{name}_user_id_users"), ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["discussion_id"], ["pulse_discussions.id"], name=op.f(f"fk_{name}_discussion_id_pulse_discussions"),
                                    ondelete="CASCADE"),  # fmt: skip
            sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
            sa.UniqueConstraint("user_id", "discussion_id", name=f"uq_{name}_user_discussion"),
        )
        op.create_index(f"ix_{name}_user_id", name, ["user_id"])
        op.create_index(f"ix_{name}_discussion_id", name, ["discussion_id"])

    op.create_table(
        "content_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reporter_id", sa.Integer(), nullable=True),
        sa.Column("target_type", sa.String(length=12), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="open"),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolver_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], name=op.f("fk_content_reports_reporter_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolver_id"], ["users.id"], name=op.f("fk_content_reports_resolver_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_reports")),
        sa.UniqueConstraint("reporter_id", "target_type", "target_id", name="uq_content_reports_reporter_target"),
    )
    op.create_index("ix_content_reports_status_created", "content_reports", ["status", "created_at"])
    op.create_index("ix_content_reports_target", "content_reports", ["target_type", "target_id"])

    op.create_table(
        "moderation_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=12), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], name=op.f("fk_moderation_actions_actor_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_actions")),
    )
    op.create_index("ix_moderation_actions_target", "moderation_actions", ["target_type", "target_id"])
    op.create_index("ix_moderation_actions_created_at", "moderation_actions", ["created_at"])

    op.create_table(
        "legal_acceptances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("terms_version", sa.String(length=20), nullable=False),
        sa.Column("privacy_version", sa.String(length=20), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_legal_acceptances_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_acceptances")),
    )
    op.create_index("ix_legal_acceptances_user_accepted", "legal_acceptances", ["user_id", "accepted_at"])

    _migrate_discussions()


def _migrate_discussions() -> None:
    conn = op.get_bind()
    md = sa.MetaData()
    posts = sa.Table("posts", md, autoload_with=conn)
    comments = sa.Table("comments", md, autoload_with=conn)
    topics = sa.Table("post_topics", md, autoload_with=conn)
    saves = sa.Table("saves", md, autoload_with=conn)
    users = sa.Table("users", md, autoload_with=conn)
    events = sa.Table("source_events", md, autoload_with=conn)
    disc = sa.Table("pulse_discussions", md, autoload_with=conn)
    dtopics = sa.Table("pulse_discussion_topics", md, autoload_with=conn)
    dassets = sa.Table("pulse_discussion_assets", md, autoload_with=conn)
    dsources = sa.Table("pulse_discussion_sources", md, autoload_with=conn)
    dcomments = sa.Table("pulse_comments", md, autoload_with=conn)
    dsaves = sa.Table("pulse_saves", md, autoload_with=conn)

    persona_ids = {r[0] for r in conn.execute(sa.select(users.c.id).where(users.c.kind == "persona"))}
    rows = conn.execute(
        sa.select(posts).where(posts.c.asset.is_not(None), posts.c.title.is_not(None), posts.c.hidden.is_(False),
                               posts.c.source.in_(["nexis", "research"])).order_by(posts.c.id)  # fmt: skip
    ).mappings().all()
    now = datetime.now(UTC).replace(tzinfo=None)
    for p in rows:
        if p["user_id"] in persona_ids:
            continue
        community = p["source"] == "nexis"
        # Nexis Research seed items carry back-dated times; never claim an editorial piece is older than it is.
        published = p["created_at"] if community else now
        new_id = conn.execute(disc.insert().values(
            public_id=_public_id(), kind="community" if community else "editorial", author_id=p["user_id"] if community else None,
            title=(p["title"] or "")[:200], body=p["body"] or "", stance=p["sentiment"] if p["sentiment"] in ("bullish", "bearish", "neutral") else None,
            primary_symbol=p["asset"], asset_name=p["asset_name"], symbols=list(p["symbols"] or [p["asset"]])[:10],
            bull_case=[], bear_case=[], neutral_case=[], open_questions=[], ai_assisted=False, status="visible", flagged=False, flags=[],
            locked=False, last_activity_at=published, content_updated_at=None if community else published,
            legacy_post_id=p["id"], created_at=published,
        )).inserted_primary_key[0]  # fmt: skip
        for sym in dict.fromkeys(s for s in [p["asset"], *(p["symbols"] or [])] if s):
            conn.execute(dassets.insert().values(discussion_id=new_id, symbol=str(sym)[:32]))
        for (topic,) in conn.execute(sa.select(topics.c.topic).where(topics.c.post_id == p["id"])):
            conn.execute(dtopics.insert().values(discussion_id=new_id, topic=topic))
        if p["event_id"]:
            ev = conn.execute(sa.select(events).where(events.c.id == p["event_id"])).mappings().first()
            if ev and (ev["url"] or "").startswith("http"):
                conn.execute(dsources.insert().values(discussion_id=new_id, title=ev["title"][:500], url=ev["url"], publisher=ev["publisher"],
                                                      published_at=ev["published_at"], retrieved_at=now, event_id=ev["id"]))  # fmt: skip
        id_map: dict[int, tuple[int, int]] = {}
        authors = {p["user_id"]} if community else set()
        last = published
        count = 0
        for c in conn.execute(sa.select(comments).where(comments.c.post_id == p["id"]).order_by(comments.c.id)).mappings():
            if c["generated"] or c["user_id"] in persona_ids:
                continue
            parent = None
            depth = 0
            if c["parent_id"] is not None:
                if c["parent_id"] not in id_map:  # a reply to a generated comment: dropped with it
                    continue
                parent, pdepth = id_map[c["parent_id"]]
                depth = min(pdepth + 1, 6)
            cid = conn.execute(dcomments.insert().values(
                discussion_id=new_id, parent_id=parent, author_id=c["user_id"], body=c["body"], depth=depth, status="visible",
                flagged=False, flags=[], created_at=c["created_at"],
            )).inserted_primary_key[0]  # fmt: skip
            id_map[c["id"]] = (cid, depth)
            authors.add(c["user_id"])
            last = max(last, c["created_at"])
            count += 1
        saved = 0
        for s in conn.execute(sa.select(saves).where(saves.c.post_id == p["id"])).mappings():
            conn.execute(dsaves.insert().values(user_id=s["user_id"], discussion_id=new_id, created_at=s["created_at"]))
            saved += 1
        conn.execute(disc.update().where(disc.c.id == new_id).values(comment_count=count, participant_count=len(authors), save_count=saved,
                                                                    last_activity_at=last))  # fmt: skip
        conn.execute(posts.update().where(posts.c.id == p["id"]).values(hidden=True))


def downgrade() -> None:
    conn = op.get_bind()
    md = sa.MetaData()
    posts = sa.Table("posts", md, autoload_with=conn)
    disc = sa.Table("pulse_discussions", md, autoload_with=conn)
    legacy = [r[0] for r in conn.execute(sa.select(disc.c.legacy_post_id).where(disc.c.legacy_post_id.is_not(None)))]
    if legacy:
        conn.execute(posts.update().where(posts.c.id.in_(legacy)).values(hidden=False))
    for t in ("legal_acceptances", "moderation_actions", "content_reports", "pulse_saves", "pulse_follows", "pulse_reactions",
              "pulse_comments", "pulse_discussion_updates", "pulse_discussion_sources", "pulse_discussion_assets",
              "pulse_discussion_topics", "pulse_discussions"):  # fmt: skip
        op.drop_table(t)
    with op.batch_alter_table("user_notifications", schema=None) as b:
        b.drop_column("delivery")
    with op.batch_alter_table("users", schema=None) as b:
        b.drop_column("posting_suspended_until")
        b.drop_column("role")
