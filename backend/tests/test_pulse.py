"""Nexis Pulse: discussions, the Pulse score, topics, arguments, discovery and the AI summary."""

from __future__ import annotations

import json
import random

import pytest

from app.services import llm, pulse, pulse_score


def _quotes(db, syms):  # type: ignore[no-untyped-def]
    names = {
        "TSLA": "Tesla, Inc.",
        "NFLX": "Netflix, Inc.",
        "NVDA": "NVIDIA Corporation",
        "EMIRATESNBD.AE": "Emirates NBD Bank PJSC",
    }
    return [
        {"symbol": s, "name": names[s], "price": 250.0, "change_pct": 0.01, "currency": "USD", "type": "equity"}
        for s in syms
        if s in names
    ]


@pytest.fixture
def markets_ok(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(pulse.markets, "quotes", _quotes)


def _account(client, tag: str) -> dict:  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()
    mail = f"{tag}{random.randint(10**6, 10**7)}@example.com"
    assert client.post("/api/auth/register", json={"email": mail, "password": "pulse-pass-123"}).status_code == 201
    return {"identifier": mail, "password": "pulse-pass-123", **client.get("/api/auth/me").json()["user"]}


def _login(client, who: dict) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"identifier": who["identifier"], "password": who["password"]}).status_code == 200


def _new(client, symbol="TSLA", **kw):  # type: ignore[no-untyped-def]
    body = {"symbol": symbol, "title": kw.pop("title", "Robotaxi upside is underestimated"),
            "body": kw.pop("body", "I think the market is pricing the autonomy business at almost zero, which looks wrong."), **kw}  # fmt: skip
    return client.post("/api/pulse/discussions", json=body)


def test_score_bands_and_minimum():
    assert pulse_score.score({"bullish": 2, "neutral": 0, "bearish": 0})["available"] is False  # below the minimum
    s = pulse_score.score({"bullish": 2, "neutral": 0, "bearish": 1})
    assert s == {"available": True, "value": 67, "label": "Favorable", "basis": 3, "minimum": 3}
    assert pulse_score.score({"bullish": 0, "neutral": 0, "bearish": 5})["value"] == 1
    assert pulse_score.score({"bullish": 5, "neutral": 0, "bearish": 0})["label"] == "Extremely favorable"
    assert pulse_score.score({"bullish": 1, "neutral": 1, "bearish": 1})["label"] == "Neutral / mixed"


def test_create_validates_input(client, markets_ok):
    _account(client, "val")
    assert _new(client, title="short").status_code == 422
    assert _new(client, body="too short").status_code == 422
    assert _new(client, topics=["astrology"]).status_code == 422
    assert _new(client, topics=["earnings", "valuation", "ai", "growth"]).status_code == 422
    assert _new(client, sentiment="moon").status_code == 422
    unknown = _new(client, symbol="ZZZZQ")
    assert unknown.status_code == 422 and "couldn't find" in unknown.json()["error"]["message"]
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert _new(client).status_code == 401  # signed-out people can read but not post


def test_full_pulse_flow(client, markets_ok):
    symbol = "NVDA"
    empty = client.get(f"/api/pulse/assets/{symbol}").json()
    base_total = empty["total_discussions"]

    alice = _account(client, "alice")
    a1 = _new(
        client, symbol, title="Data-centre demand still accelerating", sentiment="bullish", topics=["earnings", "ai"]
    ).json()
    assert a1["asset"] == "NVDA" and a1["asset_name"] == "NVIDIA Corporation" and a1["sentiment"] == "bullish"
    assert a1["source"] == {"key": "nexis", "label": "Nexis Community", "editorial": False} and [
        t["key"] for t in a1["topics"]
    ] == ["earnings", "ai"]
    script = "<script>alert(1)</script> margins look stretched compared with history"
    a2 = _new(
        client, symbol, title="Valuation already prices in perfection", body=script, sentiment="bearish", topics=["valuation"]
    ).json()
    assert a2["body"] == script  # stored as plain text; the page renders it as text, never as HTML

    bob = _account(client, "bob")
    b1 = _new(
        client, symbol, title="Gaming and auto segments are under-appreciated", sentiment="bullish", topics=["ai", "products"]
    ).json()
    b2 = _new(client, symbol, title="Waiting for the next quarter before deciding").json()  # no sentiment chosen
    assert b2["sentiment"] is None

    # likes and threaded comments reuse the community endpoints
    assert client.post(f"/api/social/posts/{a1['id']}/like").json() == {"liked": True, "like_count": 1}
    top = client.post(f"/api/social/posts/{a1['id']}/comments", json={"body": "Agreed on hyperscaler capex."}).json()
    reply = client.post(f"/api/social/posts/{a1['id']}/comments", json={"body": "Reply", "parent_id": top["id"]}).json()
    nested = client.post(
        f"/api/social/posts/{a1['id']}/comments", json={"body": "Reply to reply", "parent_id": reply["id"]}
    ).json()
    assert reply["parent_id"] == top["id"] and nested["parent_id"] == top["id"]  # one level deep
    wrong = client.post(f"/api/social/posts/{a2['id']}/comments", json={"body": "x", "parent_id": top["id"]})
    assert wrong.status_code == 422
    d = client.get(f"/api/pulse/discussions/{a1['id']}").json()
    assert d["like_count"] == 1 and d["comment_count"] == 3 and d["liked_by_me"] is True

    # only the author can edit or delete
    assert client.patch(f"/api/pulse/discussions/{a1['id']}", json={"title": "Hijacked title here"}).status_code == 403
    assert client.delete(f"/api/pulse/discussions/{a1['id']}").status_code == 403

    page = client.get(f"/api/pulse/assets/{symbol}").json()
    assert page["total_discussions"] == base_total + 4 and page["participants"] >= 2
    counts = page["sentiment"]["counts"]
    assert counts["bullish"] >= 2 and counts["bearish"] >= 1 and counts["unclassified"] >= 1
    sc = page["score"]
    assert sc["available"] and sc["basis"] == counts["bullish"] + counts["neutral"] + counts["bearish"]
    assert sc["value"] == pulse_score.score(counts)["value"] and "not a price prediction" in page["disclaimer"]
    assert page["volume"]["last_7_days"] >= 4
    topics = {t["key"]: t["count"] for t in page["topics"]}
    assert topics["ai"] >= 2 and topics["earnings"] >= 1
    assert a1["id"] in [x["id"] for x in page["arguments"]["bullish"]]
    assert page["arguments"]["bullish"][0]["id"] == a1["id"]  # most engagement first
    assert [x["id"] for x in page["arguments"]["bearish"]][:1] == [a2["id"]]

    # editing: Alice changes her mind; the AI reading is cleared because the text changed
    _login(client, alice)
    e = client.patch(
        f"/api/pulse/discussions/{a2['id']}",
        json={"sentiment": "neutral", "body": "On reflection the valuation is fair given growth."},
    ).json()
    assert e["sentiment"] == "neutral" and e["edited_at"] and e["ai_sentiment"] is None

    # pagination
    first = client.get("/api/pulse/discussions", params={"symbol": symbol, "limit": 2}).json()
    second = client.get("/api/pulse/discussions", params={"symbol": symbol, "limit": 2, "cursor": first["next"]}).json()
    assert (
        len(first["items"]) == 2 and first["next"] and not {x["id"] for x in first["items"]} & {x["id"] for x in second["items"]}
    )
    bulls = client.get("/api/pulse/discussions", params={"symbol": symbol, "sentiment": "bullish"}).json()["items"]
    assert {x["id"] for x in bulls} >= {a1["id"], b1["id"]} and all(x["sentiment"] == "bullish" for x in bulls)
    ai_topic = client.get("/api/pulse/discussions", params={"symbol": symbol, "topic": "ai"}).json()["items"]
    assert {x["id"] for x in ai_topic} >= {a1["id"], b1["id"]}

    # discovery and the community feed see the same discussions
    disc = client.get("/api/pulse/discover").json()
    assert disc["totals"]["discussions"] >= 4 and "NVDA" in [r["symbol"] for r in disc["trending_assets"]]
    assert a1["id"] in [x["id"] for x in disc["trending_discussions"]]
    post = client.get(f"/api/social/posts/{a1['id']}").json()
    assert post["discussion"]["title"] == "Data-centre demand still accelerating" and post["source"] == "nexis"
    prof = client.get(f"/api/pulse/users/{bob['username']}").json()
    assert prof["assets"][0]["symbol"] == "NVDA" and prof["assets"][0]["discussions"] == 2

    # the author can delete; the discussion disappears from the counts
    assert client.delete(f"/api/pulse/discussions/{a2['id']}").status_code == 204
    assert client.get(f"/api/pulse/discussions/{a2['id']}").status_code == 404
    assert client.get(f"/api/pulse/assets/{symbol}").json()["total_discussions"] == base_total + 3
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_no_score_without_enough_discussions(client, markets_ok):
    _account(client, "solo")
    _new(client, "EMIRATESNBD.AE", title="India expansion through RBL is a big bet", sentiment="bullish")
    page = client.get("/api/pulse/assets/EMIRATESNBD.AE").json()
    assert page["total_discussions"] >= 1
    if (
        page["sentiment"]["counts"]["bullish"] + page["sentiment"]["counts"]["bearish"] + page["sentiment"]["counts"]["neutral"]
        < 3
    ):
        assert page["score"] == {**page["score"], "available": False, "value": None, "label": None}
    assert page["history"] is None or len(page["history"]) == 12
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_ai_summary_cites_discussions_and_keeps_ai_sentiment_separate(client, markets_ok, monkeypatch):
    _account(client, "summ")
    one = _new(client, "NFLX", title="Margins are recovering faster than expected", sentiment="bullish").json()
    two = _new(client, "NFLX", title="Competition in China is a real threat").json()  # author chose nothing

    def unavailable(*a, **k):  # type: ignore[no-untyped-def]
        raise llm.LLMUnavailable("no key")

    monkeypatch.setattr(llm, "chat", unavailable)
    off = client.get("/api/pulse/assets/NFLX/summary").json()
    assert off["available"] is False and "unavailable" in off["reason"]

    seen = {}

    def fake_chat(messages, max_tokens=0, temperature=0):  # type: ignore[no-untyped-def]
        seen["prompt"] = messages[-1]["content"]
        n = seen["prompt"].count("\n[")  # numbered newest first: [1] is `two`, [n] is `one`
        out = {"summary": "Members are split.", "themes": [{"title": "Margins", "detail": "Recovering.", "refs": [n, 999]}],
               "bull": [{"point": "Margins recovering", "refs": [n]}], "bear": [{"point": "China competition", "refs": [n - 1]}],
               "classifications": [{"n": n - 1, "sentiment": "bearish"}, {"n": n, "sentiment": "neutral"}]}  # fmt: skip
        return {"content": "```json\n" + json.dumps(out) + "\n```", "_model": "test-model"}

    monkeypatch.setattr(llm, "chat", fake_chat)
    s = client.get("/api/pulse/assets/NFLX/summary").json()
    assert s["available"] and s["summary"] == "Members are split." and s["model"] == "test-model"
    assert s["themes"][0]["refs"] == [one["id"]]  # numbers become discussion ids; a made-up number is dropped
    assert s["bear"][0]["refs"] == [two["id"]] and s["titles"][str(two["id"])] == two["title"]
    assert "Use ONLY these discussions" in seen["prompt"] and "author marked it bullish" in seen["prompt"]
    a, b = (client.get(f"/api/pulse/discussions/{x['id']}").json() for x in (one, two))
    assert a["sentiment"] == "bullish" and a["ai_sentiment"] == "neutral"  # the author's choice is never overwritten
    assert b["sentiment"] is None and b["ai_sentiment"] == "bearish"
    client.post("/api/auth/logout")
    client.cookies.clear()
