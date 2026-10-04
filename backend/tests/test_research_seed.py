"""Nexis Research seed: published as Nexis editorial, idempotent, never back-dated, never with invented engagement."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.base import utcnow
from app.models import PulseComment, PulseDiscussion, PulseReaction, PulseSave
from app.seeds import research
from app.seeds.research_content import DISCUSSIONS
from app.services import pulse


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


def _seeded(db) -> list[PulseDiscussion]:  # type: ignore[no-untyped-def]
    return list(db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key.like(f"{research.KEY_PREFIX}%"))))


def test_content_file_is_valid():
    keys = [d["key"] for d in DISCUSSIONS]
    assert len(set(keys)) == len(keys) and len(DISCUSSIONS) >= 20
    for d in DISCUSSIONS:
        assert any(t in pulse.TOPICS for t in d["topics"]), d["key"]
        assert d["name"] and d["symbol"] == d["symbol"].upper()
        assert 8 <= len(d["title"]) <= 160 and len(d["body"]) >= 20


def test_seed_is_idempotent_labelled_and_not_backdated(client, db):
    before = utcnow()
    first = research.seed(db)
    assert first["created"] == len(DISCUSSIONS) and first["updated"] == 0
    assert research.seed(db) == {"created": 0, "updated": 0, "unchanged": len(DISCUSSIONS), "total_in_file": len(DISCUSSIONS)}
    rows = _seeded(db)
    assert len(rows) == len(DISCUSSIONS)
    for d in rows:
        assert d.kind == "editorial" and d.author_id is None and d.ai_assisted
        assert d.created_at >= before.replace(microsecond=0)  # published when it was actually published
        assert d.comment_count == 0 and d.agree_count == 0 and d.follower_count == 0
    ids = [d.id for d in rows]
    for model, col in ((PulseComment, PulseComment.discussion_id), (PulseSave, PulseSave.discussion_id)):
        assert db.scalar(select(func.count(model.id)).where(col.in_(ids))) == 0  # no seeded engagement
    assert db.scalar(select(func.count(PulseReaction.id)).where(PulseReaction.target_type == "discussion", PulseReaction.target_id.in_(ids))) == 0

    card = client.get(f"/api/pulse/discussions/{rows[0].public_id}").json()
    assert card["author"] == {"display_name": "Nexis", "type": "editorial", "label": "Nexis Editorial"}
    assert "AI assistance" in card["editorial"]["disclosure"]


def test_dry_run_writes_nothing(client, db):
    n = len(_seeded(db))
    out = research.seed(db, dry_run=True)
    assert len(_seeded(db)) == n and out["total_in_file"] == len(DISCUSSIONS)
