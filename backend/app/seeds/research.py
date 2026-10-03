"""Seed the Nexis Research editorial discussions into Nexis Pulse.

    python -m app.seeds.research                  # migrate if needed, then create or update
    python -m app.seeds.research --dry-run        # report what would change, write nothing
    python -m app.seeds.research --verify-assets  # also confirm every symbol with the live market-data API

Idempotent: every discussion carries ``Post.external_key = "nexis-research:<key>"``. A run creates entries
whose key is missing, rewrites entries whose text, sentiment or topics changed in ``research_content.py``,
and leaves everything else alone — running it twice never creates duplicates. Likes, comments and saves are
never created or touched, and discussions that are no longer in the file are not deleted.

The discussions belong to one official account (``nexis.research``, kind ``editorial``) and use the
``research`` source, so they are labelled "Nexis Research" and never count toward the community Pulse score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.base import utcnow
from app.models import Media, Post, PostTopic, User
from app.seeds.research_content import DISCUSSIONS
from app.services import markets, pulse, pulse_sources

USERNAME = "nexis.research"
DISPLAY_NAME = "Nexis Research"
KIND = "editorial"
BIO = "Official Nexis market research. Editorial analysis by the Nexis research team — not member opinions and not financial advice."
KEY_PREFIX = "nexis-research:"
SOURCE = pulse_sources.NEXIS_RESEARCH.key


def ensure_account(db: Session) -> User:
    """The one official account that owns Nexis Research content (no password, cannot sign in)."""
    u = db.scalars(select(User).where(User.username == USERNAME)).first()
    if u is not None and u.kind != KIND:
        raise ValidationFailed(f"the username {USERNAME!r} belongs to a member account; refusing to take it over")
    if u is None:
        u = User(username=USERNAME, display_name=DISPLAY_NAME, password_hash=None, kind=KIND, auth_provider="system", bio=BIO)
        db.add(u)
        db.flush()
    u.display_name, u.bio = DISPLAY_NAME, BIO
    if u.avatar_media_id is None:
        from app.services.newsfeed import _avatar  # the same generated brand mark the official news pages use

        m = Media(
            user_id=u.id, content_type="image/jpeg", width=256, height=256, data=_avatar({"color": (28, 92, 171), "badge": "NR"})
        )
        db.add(m)
        db.flush()
        u.avatar_media_id = m.id
    return u


def _validated(entry: dict[str, Any]) -> dict[str, Any]:
    """Run the same checks a member's discussion goes through."""
    return {
        "key": KEY_PREFIX + entry["key"],
        "asset": markets.clean_symbol(entry["symbol"]),
        "asset_name": str(entry["name"])[:160],
        "title": pulse._text(entry["title"], 8, 140, f"{entry['key']}: the title"),
        "body": pulse._text(entry["body"], 20, 5000, f"{entry['key']}: the body"),
        "sentiment": pulse._sentiment(entry["sentiment"]),
        "topics": pulse._topics(entry["topics"]) or _fail(entry, "needs at least one topic"),
        "days_ago": float(entry["days_ago"]) if float(entry["days_ago"]) >= 1 else _fail(entry, "days_ago must be at least 1"),
    }


def _fail(entry: dict[str, Any], why: str) -> Any:
    raise ValidationFailed(f"{entry['key']}: {why}")


def _created_at(now: datetime, item: dict[str, Any]) -> datetime:
    # A stable time of day per discussion, so the history doesn't line up at midnight.
    hours = int(hashlib.sha1(item["key"].encode()).hexdigest(), 16) % 9 + 1
    return now - timedelta(days=item["days_ago"], hours=hours)


def verify_assets(db: Session) -> list[str]:
    """Symbols the market-data API doesn't recognise (empty means all good)."""
    syms = sorted({markets.clean_symbol(e["symbol"]) for e in DISCUSSIONS})
    found = {q["symbol"] for q in markets.quotes(db, syms)}
    return [s for s in syms if s not in found]


def seed(db: Session, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    now = now or utcnow()
    items = [_validated(e) for e in DISCUSSIONS]
    if len({i["key"] for i in items}) != len(items):
        raise ValidationFailed("duplicate key in research_content.py")
    account = ensure_account(db)
    existing = {p.external_key: p for p in db.scalars(select(Post).where(Post.external_key.in_([i["key"] for i in items])))}
    topics_now: dict[int, set[str]] = {}
    if existing:
        for pid, t in db.execute(
            select(PostTopic.post_id, PostTopic.topic).where(PostTopic.post_id.in_([p.id for p in existing.values()]))
        ):
            topics_now.setdefault(pid, set()).add(t)
    from app.services.social import _tags

    report: dict[str, Any] = {"created": 0, "updated": 0, "unchanged": 0, "skipped": [], "account": USERNAME}
    for it in items:
        cashtags, tags = _tags(f"{it['title']}\n{it['body']}")
        symbols = list(dict.fromkeys([it["asset"], *cashtags]))[:10]
        p = existing.get(it["key"])
        if p is None:
            report["created"] += 1
            if dry_run:
                continue
            p = Post(
                user_id=account.id, body=it["body"], symbols=symbols, tags=tags, source=SOURCE, asset=it["asset"],
                asset_name=it["asset_name"], title=it["title"], sentiment=it["sentiment"], external_key=it["key"],
                created_at=_created_at(now, it),
            )  # fmt: skip
            db.add(p)
            db.flush()
            db.add_all(PostTopic(post_id=p.id, topic=t) for t in it["topics"])
            continue
        if p.user_id != account.id or p.source != SOURCE:
            report["skipped"].append(it["key"])  # never touch a row that isn't ours
            continue
        wanted = (it["title"], it["body"], it["sentiment"], it["asset"], it["asset_name"], set(it["topics"]))
        have = (p.title, p.body, p.sentiment, p.asset, p.asset_name, topics_now.get(p.id, set()))
        if wanted == have:
            report["unchanged"] += 1
            continue
        report["updated"] += 1
        if dry_run:
            continue
        if (p.title, p.body) != (it["title"], it["body"]):
            p.ai_sentiment = None  # the AI read the old text
        p.title, p.body, p.sentiment, p.asset, p.asset_name, p.symbols, p.tags = (
            it["title"], it["body"], it["sentiment"], it["asset"], it["asset_name"], symbols, tags,
        )  # fmt: skip
        if topics_now.get(p.id, set()) != set(it["topics"]):
            db.query(PostTopic).filter(PostTopic.post_id == p.id).delete(synchronize_session=False)
            db.add_all(PostTopic(post_id=p.id, topic=t) for t in it["topics"])
    if dry_run:
        db.rollback()
    else:
        db.commit()
    report["total_in_file"] = len(items)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed Nexis Research discussions into Nexis Pulse (idempotent).")
    ap.add_argument("--dry-run", action="store_true", help="report what would change without writing")
    ap.add_argument("--verify-assets", action="store_true", help="check every symbol against the live market-data API first")
    args = ap.parse_args()
    from app.db.init_db import run_migrations
    from app.db.session import SessionLocal

    run_migrations()
    with SessionLocal() as db:
        if args.verify_assets:
            missing = verify_assets(db)
            if missing:
                raise SystemExit(f"unknown symbols: {', '.join(missing)}")
        print(json.dumps(seed(db, dry_run=args.dry_run), indent=2))


if __name__ == "__main__":
    main()
