"""Nexis Pulse collection and synthesis, and linking Reddit / X accounts. Every outside service is mocked."""

from __future__ import annotations

import json
import random
from urllib.parse import parse_qs, urlparse

import httpx

from app.services import llm, oauth, pulse

RSS = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/dubai_investor</name></author><category term="UAEStocks"/><id>t3_a1</id>
<link href="https://www.reddit.com/r/UAEStocks/comments/a1/emaar/"/><updated>2026-10-01T08:00:00+00:00</updated>
<title>Emaar results look strong, holding my shares</title><content type="html">&lt;p&gt;Dividend should grow.&lt;/p&gt; submitted by /u/dubai_investor</content></entry>
<entry><author><name>/u/someone</name></author><category term="cooking"/><id>t3_a2</id>
<link href="https://www.reddit.com/r/cooking/comments/a2/x/"/><updated>2026-10-01T07:00:00+00:00</updated>
<title>Best pasta recipe</title><content type="html">nothing to see</content></entry>
<entry><author><name>/u/agent_k</name></author><category term="u_agent_k"/><id>t3_a3</id>
<link href="https://www.reddit.com/user/agent_k/comments/a3/x/"/><updated>2026-10-01T06:00:00+00:00</updated>
<title>Villas by Emaar in Dubai Hills</title><content type="html">brochure</content></entry>
</feed>"""

STOCKTWITS = {
    "messages": [
        {"id": 1, "body": "$NVDA breaking out", "created_at": "2026-10-01T10:00:00Z", "user": {"username": "trader1"},
         "entities": {"sentiment": {"basic": "Bullish"}}, "likes": {"total": 3}},
        {"id": 2, "body": "$NVDA too expensive here", "created_at": "2026-10-01T09:00:00Z", "user": {"username": "trader2"},
         "entities": {"sentiment": {"basic": "Bearish"}}},
        {"id": 3, "body": "$NVDA watching", "created_at": "2026-10-01T08:00:00Z", "user": {"username": "trader3"}, "entities": {}},
    ]
}  # fmt: skip


def _fake_get(url, params=None, headers=None, timeout=None, follow_redirects=None):  # type: ignore[no-untyped-def]
    if "search.rss" in url:
        return httpx.Response(200, content=RSS.encode(), headers={"content-type": "application/atom+xml"})
    if "stocktwits.com" in url:
        if url.endswith("/NVDA.json"):
            return httpx.Response(200, json=STOCKTWITS)
        return httpx.Response(404, json={"errors": [{"message": "Symbol not found"}]})
    raise AssertionError(f"unexpected request {url}")


def _quotes(names):  # type: ignore[no-untyped-def]
    return lambda db, syms: [{"symbol": s, "name": names[s], "type": "equity"} for s in syms if s in names]


def test_subject_terms_follow_how_people_write():
    from app.db import session as db_session

    names = {"EMAAR.AE": "Emaar Properties PJSC", "FAB.AD": "First Abu Dhabi Bank", "NVDA": "NVIDIA Corporation"}
    with db_session.SessionLocal() as db:
        orig = pulse.markets.quotes
        pulse.markets.quotes = _quotes(names)
        try:
            assert pulse.subject(db, "EMAAR.AE")["terms"] == ["EMAAR", "Emaar Properties", "Emaar"]
            assert pulse.subject(db, "FAB.AD")["terms"] == ["FAB", "First Abu Dhabi Bank"]  # "First" alone is too generic
        finally:
            pulse.markets.quotes = orig
    fab = {"base": "FAB", "terms": ["FAB", "First Abu Dhabi Bank"]}
    assert not pulse._mentions("What is FAB 3.0 in the card game?", fab)
    assert pulse._mentions("Thinking of buying FAB shares before the dividend", fab)
    assert pulse._mentions("$FAB looks cheap", fab)
    assert pulse._mentions("First Abu Dhabi Bank reported earnings", fab)


def test_collect_keeps_relevant_posts_links_authors_and_reports_sources(client, monkeypatch):
    monkeypatch.setattr(pulse.httpx, "get", _fake_get)
    monkeypatch.setattr(pulse.markets, "quotes", _quotes({"EMAAR.AE": "Emaar Properties PJSC", "NVDA": "NVIDIA Corporation"}))
    d = client.get("/api/pulse/EMAAR.AE").json()
    assert d["sources"]["reddit"]["ok"] and d["sources"]["reddit"]["via"] == "Reddit public search feed"
    assert d["sources"]["x"]["ok"] is False and "X API plan" in d["sources"]["x"]["error"]
    assert d["sources"]["stocktwits"]["ok"] is False
    authors = [i["author"] for i in d["items"]]
    assert authors == ["dubai_investor", "agent_k"]  # the pasta post is dropped
    first = d["items"][0]
    assert first["author_url"] == "https://www.reddit.com/user/dubai_investor" and first["community"] == "r/UAEStocks"
    assert first["text"] == "Dividend should grow." and first["url"].startswith("https://www.reddit.com/r/UAEStocks/")
    assert d["items"][1]["community"] is None  # a post on someone's own profile isn't a community
    assert "not verified" in d["disclaimer"]

    n = client.get("/api/pulse/NVDA").json()
    st = [i for i in n["items"] if i["source"] == "stocktwits"]
    assert len(st) == 3 and st[0]["author_url"] == "https://stocktwits.com/trader1" and st[0]["url"].endswith("/message/1")
    assert n["tags"] == {"bullish": 1, "bearish": 1}  # only tags the posters chose themselves


def test_synthesis_only_cites_collected_posts(client, monkeypatch):
    monkeypatch.setattr(pulse.httpx, "get", _fake_get)
    monkeypatch.setattr(pulse.markets, "quotes", _quotes({"NVDA": "NVIDIA Corporation"}))
    seen = {}

    def fake_chat(messages, max_tokens=0, temperature=0):  # type: ignore[no-untyped-def]
        seen["prompt"] = messages[-1]["content"]
        out = {"summary": "Posters are split.", "themes": [{"title": "Breakout", "detail": "Some see a breakout.", "refs": [1, 99]}],
               "bull": [{"point": "Momentum", "refs": [1]}], "bear": [{"point": "Valuation", "refs": [2]}], "questions": [],
               "tone": "mixed", "coverage": "Only a handful of posts."}  # fmt: skip
        return {"content": "```json\n" + json.dumps(out) + "\n```", "_model": "test-model"}

    monkeypatch.setattr(llm, "chat", fake_chat)
    s = client.get("/api/pulse/NVDA/synthesis").json()
    assert s["available"] and s["summary"] == "Posters are split." and s["model"] == "test-model"
    assert s["themes"][0]["refs"] == [1]  # a reference to a post that doesn't exist is removed
    assert s["bear"][0]["refs"] == [2] and len(s["ref_ids"]) == s["posts_used"] == 3
    assert "Use ONLY these posts" in seen["prompt"] and "self-tagged Bullish" in seen["prompt"]

    def down(*a, **k):  # type: ignore[no-untyped-def]
        raise llm.LLMUnavailable("no key")

    monkeypatch.setattr(llm, "chat", down)
    monkeypatch.setattr(pulse.markets, "quotes", _quotes({"AAPL": "Apple Inc."}))
    off = client.get("/api/pulse/AAPL/synthesis").json()
    assert off["available"] is False and off["reason"]


def _configure(monkeypatch):  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    s = get_settings()
    for k, v in {"reddit_client_id": "rid", "reddit_client_secret": "rsecret", "x_client_id": "xid", "x_client_secret": "xsecret",
                 "public_url": "http://testserver"}.items():  # fmt: skip
        monkeypatch.setattr(s, k, v)


def _fake_identity(monkeypatch, reddit_user, x_id="x1", x_user="nexis_fan"):  # type: ignore[no-untyped-def]
    def post(url, data=None, auth=None, headers=None, timeout=None):  # type: ignore[no-untyped-def]
        assert data["code"] == "the-code" and auth[0] in ("rid", "xid")
        if "x.com" in url:
            assert data["code_verifier"]
        return httpx.Response(200, json={"access_token": "tok"})

    def get(url, params=None, headers=None, timeout=None):  # type: ignore[no-untyped-def]
        assert headers["Authorization"] == "Bearer tok"
        if "reddit.com" in url:
            return httpx.Response(200, json={"id": "r1", "name": reddit_user, "icon_img": "https://i.redd.it/a.png?x=1"})
        return httpx.Response(200, json={"data": {"id": x_id, "username": x_user, "name": "Nexis Fan"}})

    monkeypatch.setattr(oauth.httpx, "post", post)
    monkeypatch.setattr(oauth.httpx, "get", get)


def _callback(client, provider, **start):  # type: ignore[no-untyped-def]
    r = client.get(f"/api/auth/oauth/{provider}/start", params=start, follow_redirects=False)
    assert r.status_code == 302, r.text
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get(f"/api/auth/oauth/{provider}/callback", params={"code": "the-code", "state": state}, follow_redirects=False)


def test_link_reddit_and_x_then_sign_in_with_reddit(client, monkeypatch):
    _configure(monkeypatch)
    user = f"redditor_{random.randint(1000, 9999)}"
    _fake_identity(monkeypatch, user)
    providers = client.get("/api/auth/providers").json()
    assert providers["reddit"] and providers["x"]

    client.cookies.clear()
    anon = client.get("/api/auth/oauth/reddit/start", params={"mode": "link", "next": "/settings"}, follow_redirects=False)
    assert anon.headers["location"].startswith("/settings?error=")  # linking needs a Nexis account

    mail = f"link{random.randint(10000, 99999)}@example.com"
    assert client.post("/api/auth/register", json={"email": mail, "password": "linking-pass-1"}).status_code == 201
    r = client.get("/api/auth/oauth/reddit/start", params={"mode": "link", "next": "/settings"}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.headers["location"].startswith("https://www.reddit.com/api/v1/authorize") and q["scope"] == ["identity"]
    ok = _callback(client, "reddit", mode="link", next="/settings")
    assert ok.headers["location"] == "/settings?linked=reddit"
    x = _callback(client, "x", mode="link", next="/settings")
    assert x.headers["location"] == "/settings?linked=x"
    me = client.get("/api/auth/me").json()["user"]
    assert {(a["provider"], a["username"], a["url"]) for a in me["linked"]} == {
        ("reddit", user, f"https://www.reddit.com/user/{user}"),
        ("x", "nexis_fan", "https://x.com/nexis_fan"),
    }
    # the links show publicly on the profile too
    assert {a["provider"] for a in client.get(f"/api/social/users/{me['username']}").json()["linked"]} == {"reddit", "x"}

    # signing in with the linked Reddit account opens the same Nexis account
    client.post("/api/auth/logout")
    client.cookies.clear()
    back = _callback(client, "reddit", next="/finstagram")
    assert back.headers["location"] == "/finstagram"
    assert client.get("/api/auth/me").json()["user"]["id"] == me["id"]

    assert client.delete("/api/auth/me/linked/x").status_code == 204
    assert [a["provider"] for a in client.get("/api/auth/me").json()["user"]["linked"]] == ["reddit"]
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_new_account_from_x_cannot_unlink_its_only_sign_in(client, monkeypatch):
    _configure(monkeypatch)
    _fake_identity(monkeypatch, "unused", x_id=f"x{random.randint(10**6, 10**7)}", x_user="solo_x_user")
    client.cookies.clear()
    r = _callback(client, "x", next="/")
    assert r.headers["location"] == "/"
    me = client.get("/api/auth/me").json()["user"]
    assert me["auth_provider"] == "x" and me["linked"][0]["username"] == "solo_x_user"
    blocked = client.delete("/api/auth/me/linked/x")
    assert blocked.status_code == 422 and "set a password" in blocked.json()["error"]["message"]
    client.post("/api/auth/logout")
    client.cookies.clear()
