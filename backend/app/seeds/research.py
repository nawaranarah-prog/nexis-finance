"""Publish the Nexis Research editorial pieces into Nexis Pulse as Nexis editorial discussions.

    python -m app.seeds.research                  # migrate if needed, then create or update
    python -m app.seeds.research --dry-run        # report what would change, write nothing
    python -m app.seeds.research --verify-assets  # also confirm every symbol with the live market-data API

Idempotent: every piece carries ``editorial_key = "research:<key>"`` (pieces that were migrated from the old
Finstagram-based Pulse are matched through their original post and adopt the key). A run creates missing pieces,
rewrites pieces whose text changed in ``research_content.py`` and leaves everything else alone.

Honesty rules:
* pieces are labelled Nexis (editorial), never shown as community opinion, and marked AI-assisted because they were
  drafted with AI assistance;
* they are published with the time they are actually published — never back-dated;
* no comments, reactions, follows or saves are ever created.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.base import utcnow
from app.models import Post, PulseDiscussion
from app.seeds.research_content import DISCUSSIONS
from app.services import markets, pulse

KEY_PREFIX = "research:"
LEGACY_PREFIX = "nexis-research:"


def _fail(entry: dict[str, Any], why: str) -> Any:
    raise ValidationFailed(f"{entry['key']}: {why}")


def _validated(entry: dict[str, Any]) -> dict[str, Any]:
    topics = [t for t in entry["topics"] if t in pulse.TOPICS][: pulse.MAX_TOPICS] or _fail(entry, "needs at least one known topic")
    return {
        "key": entry["key"],
        "symbol": markets.clean_symbol(entry["symbol"]),
        "asset_name": str(entry["name"])[:160],
        "title": pulse._text(entry["title"], 8, 160, f"{entry['key']}: the title"),
        "body": pulse._text(entry["body"], 20, 6000, f"{entry['key']}: the body"),
        "topics": topics,
    }


def verify_assets(db: Session) -> list[str]:
    """Symbols the market-data API doesn't recognise (empty means all good)."""
    syms = sorted({markets.clean_symbol(e["symbol"]) for e in DISCUSSIONS})
    found = {q["symbol"] for q in markets.quotes(db, syms)}
    return [s for s in syms if s not in found]


def seed(db: Session, dry_run: bool = False) -> dict[str, Any]:
    items = [_validated(e) for e in DISCUSSIONS]
    if len({i["key"] for i in items}) != len(items):
        raise ValidationFailed("duplicate key in research_content.py")
    keys = [KEY_PREFIX + i["key"] for i in items]
    existing = {d.editorial_key: d for d in db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key.in_(keys)))}
    # Pieces migrated from the old post-based Pulse: adopt them instead of publishing a duplicate.
    legacy = dict(db.execute(select(Post.external_key, Post.id).where(Post.external_key.in_([LEGACY_PREFIX + i["key"] for i in items]))).all())
    report: dict[str, Any] = {"created": 0, "updated": 0, "unchanged": 0}
    now = utcnow()
    for it in items:
        key = KEY_PREFIX + it["key"]
        d = existing.get(key)
        if d is None and (pid := legacy.get(LEGACY_PREFIX + it["key"])) is not None:
            d = db.scalars(select(PulseDiscussion).where(PulseDiscussion.legacy_post_id == pid)).first()
            if d is not None and not dry_run:
                d.editorial_key = key
        if d is None:
            report["created"] += 1
            if dry_run:
                continue
            d = PulseDiscussion(public_id=pulse.new_public_id(db), kind="editorial", editorial_key=key, title=it["title"], body=it["body"],
                                primary_symbol=it["symbol"], asset_name=it["asset_name"], symbols=[it["symbol"]], ai_assisted=True,
                                status="visible", created_at=now, last_activity_at=now, content_updated_at=now)  # fmt: skip
            db.add(d)
            db.flush()
            pulse._set_assets(db, d, [it["symbol"]])
            pulse._set_topics(db, d, it["topics"])
            continue
        if d.kind != "editorial":
            raise ValidationFailed(f"{key} points at a community discussion; refusing to touch it")
        if (d.title, d.body, d.primary_symbol, d.asset_name) == (it["title"], it["body"], it["symbol"], it["asset_name"]):
            report["unchanged"] += 1
            continue
        report["updated"] += 1
        if dry_run:
            continue
        d.title, d.body, d.primary_symbol, d.asset_name, d.symbols = it["title"], it["body"], it["symbol"], it["asset_name"], [it["symbol"]]
        d.ai_assisted, d.content_updated_at = True, now
        pulse._set_assets(db, d, [it["symbol"]])
        pulse._set_topics(db, d, it["topics"])
    if dry_run:
        db.rollback()
    else:
        db.commit()
    report["total_in_file"] = len(items)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish Nexis Research pieces as Nexis editorial discussions (idempotent).")
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
