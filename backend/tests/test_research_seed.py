"""Nexis Research editorial seed: idempotency, attribution, validity, and keeping community Pulse semantics."""

from __future__ import annotations

import random

import pytest
from sqlalchemy import func, select

from app.models import Comment, Like, Post, PostTopic, Save, User
from app.seeds import research
from app.seeds.research_content import DISCUSSIONS
from app.services import pulse, pulse_score

PREFIX = research.KEY_PREFIX


def _seeded(db) -> list[Post]:  # type: ignore[no-untyped-def]
    return list(db.scalars(select(Post).where(Post.external_key.like(f"{PREFIX}%"))))


def _quotes(db, syms):  # type: ignore[no-untyped-def]
    return [{"symbol": s, "name": s, "price": 1.0, "currency": "USD"} for s in syms]


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


def test_content_file_is_valid():
    keys = [d["key"] for d in DISCUSSIONS]
    assert 40 <= len(DISCUSSIONS) <= 60 and len(set(keys)) == len(keys)
    assert {d["sentiment"] for d in DISCUSSIONS} == set(pulse_score.SENTIMENTS)
    for d in DISCUSSIONS:
        assert d["sentiment"] in pulse_score.SENTIMENTS
        assert 1 <= len(d["topics"]) <= pulse.MAX_TOPICS and all(t in pulse.TOPICS for t in d["topics"])
        assert d["days_ago"] >= 1 and d["days_ago"] <= 90
        assert 120 <= len(d["body"].split()) <= 320, d["key"]
        assert d["name"] and d["symbol"] == d["symbol"].upper()
    assert len({d["symbol"] for d in DISCUSSIONS}) >= 12


def test_seed_is_idempotent_and_correctly_attributed(client, db, monkeypatch):
    monkeypatch.setattr(pulse.markets, "quotes", _quotes)
    # a genuine member discussion created before seeding
    mail = f"member{random.randint(10**6, 10**7)}@example.com"
    assert client.post("/api/auth/register", json={"email": mail, "password": "member-pass-1"}).status_code == 201
    mine = client.post("/api/pulse/discussions", json={"symbol": "AMD", "title": "Member view on AMD before the seed",
                       "body": "Writing this as a member to check the seed leaves my discussion alone.", "sentiment": "bullish",
                       "topics": ["ai"]}).json()  # fmt: skip

    first = research.seed(db)
    assert first["created"] == len(DISCUSSIONS) and first["updated"] == 0 and not first["skipped"]
    second = research.seed(db)
    third = research.seed(db)
    assert second == {**second, "created": 0, "updated": 0, "unchanged": len(DISCUSSIONS)} and third["created"] == 0
    rows = _seeded(db)
    assert len(rows) == len(DISCUSSIONS)  # no duplicates after three runs

    account = db.scalars(select(User).where(User.username == research.USERNAME)).one()
    assert account.kind == "editorial" and account.display_name == "Nexis Research" and account.password_hash is None
    assert db.scalar(select(func.count(User.id)).where(User.kind == "editorial")) == 1  # one official account, no fake people
    ids = [p.id for p in rows]
    for p in rows:
        assert p.user_id == account.id and p.source == "research" and p.sentiment in pulse_score.SENTIMENTS
        assert p.asset == p.asset.upper() and p.asset_name and p.like_count == 0 and p.comment_count == 0
    assert db.scalar(select(func.count(Like.id)).where(Like.post_id.in_(ids))) == 0  # no seeded engagement
    assert db.scalar(select(func.count(Comment.id)).where(Comment.post_id.in_(ids))) == 0
    assert db.scalar(select(func.count(Save.id)).where(Save.post_id.in_(ids))) == 0
    topics = db.execute(select(PostTopic.topic).where(PostTopic.post_id.in_(ids))).scalars().all()
    assert topics and all(t in pulse.TOPICS for t in topics)
    newest = max(p.created_at for p in rows)
    assert newest < pulse.utcnow() and min(p.created_at for p in rows) > pulse.utcnow() - pulse.SCORE_WINDOW

    # the member's discussion is untouched and still a community discussion
    again = client.get(f"/api/pulse/discussions/{mine['id']}").json()
    assert again == {**again, "title": mine["title"], "body": mine["body"], "sentiment": "bullish"}
    assert again["source"] == {"key": "nexis", "label": "Nexis Community", "editorial": False}

    seeded = client.get(f"/api/pulse/discussions/{rows[0].id}").json()
    assert seeded["source"] == {"key": "research", "label": "Nexis Research", "editorial": True}
    assert seeded["author"]["username"] == "nexis.research" and seeded["author"]["display_name"] == "Nexis Research"
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_research_never_becomes_community_sentiment(client, db, monkeypatch):
    monkeypatch.setattr(pulse.markets, "quotes", _quotes)
    research.seed(db)
    sym = "ADNOCGAS.AD"
    page = client.get(f"/api/pulse/assets/{sym}").json()
    n_research = sum(1 for d in DISCUSSIONS if d["symbol"] == sym)
    assert page["research_discussions"] == n_research and page["research"]["discussions"] == n_research
    assert sum(page["research"]["sentiment"]["counts"][s] for s in pulse_score.SENTIMENTS) == n_research
    community = page["sentiment"]["counts"]
    assert sum(community.values()) == page["community_discussions"]  # research is not in the community split
    assert page["score"]["basis"] == sum(community[s] for s in pulse_score.SENTIMENTS)

    # three member discussions give a community score; the research view stays separate and unchanged
    before_view = page["research"]["view"]
    mail = f"bulls{random.randint(10**6, 10**7)}@example.com"
    assert client.post("/api/auth/register", json={"email": mail, "password": "member-pass-1"}).status_code == 201
    for i in range(3):
        r = client.post("/api/pulse/discussions", json={"symbol": sym, "title": f"Member bullish take number {i + 1}",
                        "body": "A genuine member discussion written for this test of the Pulse score.", "sentiment": "bullish"})  # fmt: skip
        assert r.status_code == 201 and r.json()["source"]["key"] == "nexis"
    page = client.get(f"/api/pulse/assets/{sym}").json()
    assert page["score"]["available"] and page["score"]["value"] == 100 and page["score"]["label"] == "Extremely favorable"
    assert page["research"]["view"] == before_view
    assert page["participants"] >= 1  # members only; the editorial account isn't a participant

    # listing can separate the two, discovery and the history see both
    mine = client.get("/api/pulse/discussions", params={"symbol": sym, "source": "community"}).json()["items"]
    theirs = client.get("/api/pulse/discussions", params={"symbol": sym, "source": "research"}).json()["items"]
    assert mine and all(x["source"]["key"] == "nexis" for x in mine)
    assert len(theirs) == n_research and all(x["source"]["key"] == "research" for x in theirs)
    disc = client.get("/api/pulse/discover").json()
    assert disc["totals"]["research_discussions"] >= len(DISCUSSIONS) and disc["totals"]["community_discussions"] >= 3
    assert any(x["source"]["key"] == "research" for x in disc["recent"]) or disc["recent"]
    assert {"research_view", "score"} <= set(disc["most_discussed"][0])
    hist = page["history"]
    assert hist and sum(h["research"] for h in hist) <= n_research and sum(sum(h["counts"].values()) for h in hist) >= 3
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_editing_seed_content_updates_in_place(db, monkeypatch):
    research.seed(db)
    target = DISCUSSIONS[0]
    changed = [{**target, "title": target["title"] + " (revised)"}, *DISCUSSIONS[1:]]
    monkeypatch.setattr(research, "DISCUSSIONS", changed)
    out = research.seed(db)
    assert out["created"] == 0 and out["updated"] == 1
    p = db.scalars(select(Post).where(Post.external_key == PREFIX + target["key"])).one()
    assert p.title.endswith("(revised)") and len(_seeded(db)) == len(DISCUSSIONS)
    monkeypatch.setattr(research, "DISCUSSIONS", DISCUSSIONS)
    assert research.seed(db)["updated"] == 1  # and back again


def test_seed_refuses_to_take_over_a_member_username(db, monkeypatch):
    u = db.scalars(select(User).where(User.username == research.USERNAME)).first()
    if u is not None:  # already seeded by an earlier test: simulate a member owning the name
        monkeypatch.setattr(research, "USERNAME", "taken.by.member")
    db.add(User(username=research.USERNAME, display_name="Someone", kind="person", password_hash=None))
    db.commit()
    with pytest.raises(Exception, match="belongs to a member"):
        research.ensure_account(db)


def test_newest_is_by_publication_time_and_pages_cleanly(client, db):
    research.seed(db)
    seen, cursor = [], None
    while True:
        params = {"source": "research", "limit": 7, **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/pulse/discussions", params=params).json()
        seen += page["items"]
        cursor = page["next"]
        if not cursor:
            break
    stamps = [x["created_at"] for x in seen]
    assert len(seen) == len(DISCUSSIONS) and len({x["id"] for x in seen}) == len(DISCUSSIONS)  # nothing skipped or repeated
    assert stamps == sorted(stamps, reverse=True)  # back-dated items still come out newest first
    recent = client.get("/api/pulse/discover").json()["recent"]
    assert [x["created_at"] for x in recent] == sorted((x["created_at"] for x in recent), reverse=True)
