"""Personas, source events and the grounded discussion engine. The model and market data are mocked."""

from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import func, select

from app.db.base import utcnow
from app.models import Comment, Persona, Post, SourceEvent, User
from app.services import events, llm, personas, pulse, pulse_engine


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


def _event(db, title: str, assets: list[str], kind: str = "earnings", summary: str = "") -> SourceEvent:  # type: ignore[no-untyped-def]
    e = SourceEvent(kind=kind, provider="newsfeed", title=title, url="https://example.com/a", publisher="Example News",
                    published_at=utcnow(), facts={"headline": title, "summary": summary}, assets=assets,
                    topics=["earnings", "ai"], importance=3, external_key=f"test:{random.randint(1, 10**9)}")  # fmt: skip
    db.add(e)
    db.commit()
    return e


def test_personas_are_stable_varied_and_scale(db):
    first = personas.ensure(db, 30)
    again = personas.ensure(db, 30)
    assert again["created"] == 0 and again["total"] == first["total"]
    more = personas.ensure(db, 60)
    assert more["total"] == 60 and more["created"] == 60 - first["total"]
    rows = list(db.scalars(select(Persona)))
    assert len({p.archetype for p in rows}) >= 18  # a wide mix of investor types
    users = {u.id: u for u in db.scalars(select(User).where(User.kind == "persona"))}
    assert len(users) == len(rows) and all(u.password_hash is None for u in users.values())  # can never sign in
    assert len({u.username for u in users.values()}) == len(users)
    assert personas._profile(42) == personas._profile(42)  # deterministic
    voices = {p.traits["voice"]["length"] for p in rows}
    assert voices == {"short", "medium", "long"}


def _fake_model(script: list[dict]):  # type: ignore[no-untyped-def]
    calls = []

    def chat(messages, max_tokens=0, temperature=0, models=None, tools=None):  # type: ignore[no-untyped-def]
        calls.append({"prompt": messages[-1]["content"], "models": models})
        return {"content": json.dumps(script[len(calls) - 1]), "_model": "test-model"}

    return chat, calls


def _handles(prompt: str) -> list[str]:
    return [
        json.loads(line)["handle"]
        for line in prompt.split("PERSONAS\n", 1)[1].split("\n\n")[0].splitlines()
        if line.startswith("{")
    ]


def test_engine_builds_a_grounded_thread_and_filters_bad_replies(client, db, monkeypatch):
    personas.ensure(db, 60)
    monkeypatch.setattr(
        pulse_engine.markets,
        "quotes",
        lambda db, syms: [
            {"symbol": "NVDA", "name": "NVIDIA Corporation", "price": 180.0, "change_pct": 0.031, "currency": "USD"}
        ],
    )
    monkeypatch.setattr(llm, "status", lambda: {"configured": True})
    e = _event(
        db, "Nvidia reports record data-centre revenue", ["NVDA"], summary="Data-centre revenue rose 56% from a year earlier."
    )
    captured = {}

    def chat(messages, max_tokens=0, temperature=0, models=None, tools=None):  # type: ignore[no-untyped-def]
        prompt = messages[-1]["content"]
        if "PERSONAS" in prompt:
            hs = _handles(prompt)
            captured["handles"] = hs
            captured["models"] = models
            stances = ["bullish", "bearish", "neutral", "bearish", "bullish", "neutral"]
            out = {"title": "Is Nvidia's data-centre growth durable or already priced in?",
                   "perspectives": [{"handle": h, "stance": stances[i], "angle": f"angle {i}", "argument": "x", "doubt": "y"} for i, h in enumerate(hs)]}  # fmt: skip
        else:
            hs = captured["handles"]
            out = {
                "opening": {"handle": hs[0], "body": "Data-centre revenue up 56% is the headline, but what I care about is whether hyperscaler budgets keep growing next year."},
                "replies": [
                    {"id": 1, "handle": hs[1], "reply_to": 0, "body": "Not convinced. The valuation already assumes that budgets never slow down."},
                    {"id": 2, "handle": hs[2], "reply_to": 1, "body": "What would change your mind on that?"},
                    {"id": 3, "handle": hs[1], "reply_to": 2, "body": "Signs that demand is broadening beyond the biggest buyers."},
                    {"id": 4, "handle": hs[0], "reply_to": 3, "body": "Fair, though @" + hs[1] + " the 56% says something about breadth too."},
                    {"id": 5, "handle": hs[2], "reply_to": 1, "body": "Revenue will hit 400 billion by 2028."},  # invented number → dropped
                    {"id": 6, "handle": hs[0], "reply_to": 5, "body": "As an AI, I can't say."},  # stock phrasing → dropped
                    {"id": 7, "handle": "someone_else", "reply_to": 0, "body": "Unknown persona."},  # not in the cast → dropped
                    {"id": 8, "handle": hs[2], "reply_to": 6, "body": "Market is pricing perfection here."},  # parent dropped → re-attached
                    {"id": 9, "handle": hs[1], "reply_to": 1, "body": "Not convinced. The valuation already assumes that budgets never slow down."},  # duplicate
                ],
            }  # fmt: skip
        return {"content": json.dumps(out), "_model": "test-model"}

    monkeypatch.setattr(llm, "chat", chat)
    post = pulse_engine.generate_thread(db, e)
    assert post is not None and post.source == "generated" and post.event_id == e.id and post.asset == "NVDA"
    assert captured["models"] == [m.strip() for m in pulse_engine.get_settings().pulse_model.split(",")]
    author = db.get(User, post.user_id)
    assert author.kind == "persona" and post.sentiment == "bullish"
    comments = list(db.scalars(select(Comment).where(Comment.post_id == post.id).order_by(Comment.created_at)))
    bodies = [c.body for c in comments]
    assert len(comments) == 5 and all(c.generated for c in comments)
    assert not any("400" in b or "As an AI" in b for b in bodies) and bodies.count(bodies[0]) == 1
    by_body = {c.body: c for c in comments}
    # its parent (6) and grandparent (5) were dropped, so it joins the nearest surviving ancestor, reply 1
    assert by_body["Market is pricing perfection here."].parent_id == by_body[bodies[0]].id
    assert by_body["What would change your mind on that?"].parent_id == by_body[bodies[0]].id  # threads nest
    assert all(c.created_at <= utcnow() for c in comments) and post.created_at <= min(c.created_at for c in comments)
    assert db.get(SourceEvent, e.id).status == "discussed"
    p0 = db.scalars(select(Persona).where(Persona.user_id == post.user_id)).one()
    assert p0.memory["NVDA"]["stance"] == "bullish"  # persona memory updated

    # the API shows it as generated, with its source, and never as community sentiment
    d = client.get(f"/api/pulse/discussions/{post.id}").json()
    assert d["source"] == {"key": "generated", "label": "Nexis Perspectives", "editorial": False, "generated": True}
    assert d["author"]["kind"] == "persona" and d["event"]["publisher"] == "Example News" and d["event"]["url"]
    page = client.get("/api/pulse/assets/NVDA").json()
    assert page["generated"]["discussions"] >= 1
    assert sum(page["sentiment"]["counts"].values()) == page["community_discussions"]
    cs = client.get(f"/api/social/posts/{post.id}/comments").json()
    assert all(c["generated"] and c["author"]["kind"] == "persona" for c in cs)
    prof = client.get(f"/api/pulse/personas/{author.username}").json()
    assert prof["generated"] and prof["profile"]["archetype"] and prof["discussions"][0]["id"] == post.id


def test_engine_does_nothing_without_a_model_and_respects_the_daily_cap(client, db, monkeypatch):
    personas.ensure(db, 30)
    monkeypatch.setattr(llm, "status", lambda: {"configured": False})
    monkeypatch.setattr(events, "_from_moves", lambda db: 0)
    before = db.scalar(select(func.count(Post.id)).where(Post.source == "generated"))
    out = pulse_engine.tick(db, max_threads=3, force=True)
    assert out["generated"] == 0 and "no AI model" in out["note"]
    assert db.scalar(select(func.count(Post.id)).where(Post.source == "generated")) == before

    monkeypatch.setattr(llm, "status", lambda: {"configured": True})
    monkeypatch.setattr(pulse_engine.get_settings(), "pulse_daily_threads", 0)
    called = []
    monkeypatch.setattr(pulse_engine, "generate_thread", lambda db, e: called.append(e))
    pulse_engine.tick(db, max_threads=3, force=True)
    assert called == []  # cap reached → no generation
    again = pulse_engine.tick(db, max_threads=3)
    assert again["skipped"] and again["reason"] == "ran recently"  # throttled


def test_events_from_news_and_market_moves(client, db, monkeypatch):
    from app.services import newsfeed

    pages = newsfeed.ensure_pages(db)
    page = next(iter(pages.values()))
    key = f"k{random.randint(1, 10**9)}"
    db.add(Post(user_id=page.id, body="Emaar reports higher quarterly profit\n\nRevenue rose on strong sales.\n\n$EMAAR.AE #uae",
                symbols=["EMAAR.AE"], tags=["uae"], link_url="https://example.com/emaar", link_title="Emaar reports higher quarterly profit",
                link_source="Example Gulf News", external_key=key, created_at=utcnow()))  # fmt: skip
    db.commit()
    monkeypatch.setattr(events.markets, "quotes", lambda db, syms: [{"symbol": "TSLA", "name": "Tesla, Inc.", "price": 250.0, "change_pct": -0.071,
                                                                     "currency": "USD", "market_time": "2026-10-03T20:00:00+00:00"}] if "TSLA" in syms else [])  # fmt: skip
    out = events.ingest(db)
    assert out["newsfeed"] >= 1 and out["market_data"] >= 1
    news = db.scalars(select(SourceEvent).where(SourceEvent.external_key == f"news:{key}")).one()
    assert news.kind == "earnings" and news.assets == ["EMAAR.AE"] and news.url == "https://example.com/emaar"
    assert news.facts["summary"] == "Revenue rose on strong sales." and "earnings" in news.topics
    move = db.scalars(
        select(SourceEvent).where(
            SourceEvent.kind == "price_move", SourceEvent.assets.contains(["TSLA"]) if False else SourceEvent.title.like("Tesla%")
        )
    ).first()
    assert move is not None and move.facts["change_pct"] == -7.1 and "fell 7.1%" in move.facts["headline"]
    assert events.ingest(db)["newsfeed"] == 0  # deduplicated
    assert not events.PROVIDERS["reddit"].connected and not events.PROVIDERS["x"].connected


def test_feed_modes_search_and_comment_depth(client, db, monkeypatch):
    monkeypatch.setattr(pulse.markets, "quotes", lambda db, syms: [{"symbol": s, "name": s} for s in syms])
    monkeypatch.setattr(
        pulse.markets,
        "search",
        lambda db, q: [{"symbol": "AMD", "name": "Advanced Micro Devices", "type": "equity", "exchange": "NMS"}],
    )
    mail = f"feed{random.randint(10**6, 10**7)}@example.com"
    assert client.post("/api/auth/register", json={"email": mail, "password": "feed-pass-123"}).status_code == 201
    d = client.post("/api/pulse/discussions", json={"symbol": "AMD", "title": "Searchable thesis about accelerators",
                    "body": "A member discussion that the search should find by its title.", "sentiment": "neutral"}).json()  # fmt: skip
    latest = client.get("/api/pulse/feed", params={"mode": "latest"}).json()
    assert latest["items"][0]["id"] == d["id"] and "last_reply" in latest["items"][0]
    assert client.get("/api/pulse/feed", params={"mode": "following"}).json()["items"] == []
    client.put("/api/me/watchlist/AMD")
    assert d["id"] in [x["id"] for x in client.get("/api/pulse/feed", params={"mode": "following"}).json()["items"]]
    client.post(f"/api/social/posts/{d['id']}/save")
    assert [x["id"] for x in client.get("/api/pulse/feed", params={"mode": "saved"}).json()["items"]] == [d["id"]]
    found = client.get("/api/pulse/search", params={"q": "searchable thesis"}).json()
    assert d["id"] in [x["id"] for x in found["discussions"]]
    assert client.get("/api/pulse/search", params={"q": "valuation"}).json()["topics"][0]["key"] == "valuation"

    parent = None
    ids = []
    for i in range(6):
        c = client.post(
            f"/api/social/posts/{d['id']}/comments", json={"body": f"level {i + 1}", **({"parent_id": parent} if parent else {})}
        ).json()
        ids.append(c["id"])
        parent = c["id"]
    chain = {c["id"]: c["parent_id"] for c in client.get(f"/api/social/posts/{d['id']}/comments").json()}

    def depth(cid):  # type: ignore[no-untyped-def]
        n = 1
        while chain[cid]:
            cid, n = chain[cid], n + 1
        return n

    assert [depth(i) for i in ids] == [1, 2, 3, 4, 4, 4]  # nesting stops at four levels
    client.post("/api/auth/logout")
    client.cookies.clear()
