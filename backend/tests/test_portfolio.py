"""Personal portfolio, watchlist, privacy, intelligence, alerts and digests. Market data and the model are mocked."""

from __future__ import annotations

import random

import pytest
from sqlalchemy import select

from app.db.base import utcnow
from app.models import SourceEvent, UserNotification
from app.services import alerts, llm, portfolio

QUOTES = {
    "AAPL": {"symbol": "AAPL", "name": "Apple Inc.", "price": 200.0, "change": 2.0, "change_pct": 0.01, "currency": "USD", "type": "equity"},
    "EMAAR.AE": {"symbol": "EMAAR.AE", "name": "Emaar Properties PJSC", "price": 10.0, "change": -0.2, "change_pct": -0.0196, "currency": "AED", "type": "equity"},
    "BTC-USD": {"symbol": "BTC-USD", "name": "Bitcoin USD", "price": None, "currency": "USD", "type": "cryptocurrency"},
    "AEDUSD=X": {"symbol": "AEDUSD=X", "price": 0.2723},
}  # fmt: skip


@pytest.fixture
def market(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(portfolio.markets, "quotes", lambda db, syms: [QUOTES[s] for s in syms if s in QUOTES])
    monkeypatch.setattr(
        portfolio.markets, "details", lambda db, s: {"next_earnings": "2026-10-30", "dividends": {"ex_date": None}}
    )


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


def _signup(client, tag: str) -> dict:  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()
    mail = f"{tag}{random.randint(10**6, 10**7)}@example.com"
    assert client.post("/api/auth/register", json={"email": mail, "password": "folio-pass-123"}).status_code == 201
    return {"identifier": mail, "password": "folio-pass-123", **client.get("/api/auth/me").json()["user"]}


def test_portfolio_values_privacy_and_validation(client, market):
    assert client.get("/api/me/portfolio").status_code == 401  # private: needs a session
    _signup(client, "folio")
    a = client.post(
        "/api/me/holdings", json={"symbol": "aapl", "quantity": 10, "purchase_price": 150, "purchase_date": "2025-01-15"}
    )
    assert (
        a.status_code == 201
        and a.json()["symbol"] == "AAPL"
        and a.json()["asset_type"] == "stock"
        and a.json()["name"] == "Apple Inc."
    )
    e = client.post(
        "/api/me/holdings", json={"symbol": "EMAAR.AE", "quantity": 100, "purchase_price": 8, "portfolio": "UAE"}
    ).json()
    b = client.post("/api/me/holdings", json={"symbol": "BTC-USD", "quantity": 0.5}).json()
    assert b["asset_type"] == "crypto"
    assert client.post("/api/me/holdings", json={"symbol": "AAPL", "quantity": -1}).status_code == 422
    assert (
        client.post("/api/me/holdings", json={"symbol": "AAPL", "quantity": 1, "purchase_date": "2999-01-01"}).status_code == 422
    )

    s = client.get("/api/me/portfolio").json()
    rows = {h["symbol"]: h for h in s["holdings"]}
    assert rows["AAPL"]["value"] == 2000 and rows["AAPL"]["gain"] == 500 and rows["AAPL"]["gain_pct"] == pytest.approx(1 / 3)
    assert rows["EMAAR.AE"]["value"] == 1000 and rows["EMAAR.AE"]["value_usd"] == pytest.approx(272.3)  # real FX quote
    assert rows["BTC-USD"]["price_status"] == "unavailable" and rows["BTC-USD"]["value"] is None  # never invented
    assert s["totals"]["value_usd"] == pytest.approx(2272.3) and s["totals"]["excluded"] == ["BTC-USD"]
    assert sorted(s["portfolios"]) == ["Main", "UAE"] and abs(sum(x["weight"] for x in s["allocation"]) - 1) < 1e-9

    upd = client.patch(f"/api/me/holdings/{e['id']}", json={"quantity": 50})
    assert upd.status_code == 200 and upd.json()["quantity"] == 50

    # another member can neither see nor change these holdings
    _signup(client, "other")
    assert client.get("/api/me/portfolio").json()["holdings"] == []
    assert client.patch(f"/api/me/holdings/{e['id']}", json={"quantity": 1}).status_code == 404
    assert client.delete(f"/api/me/holdings/{e['id']}").status_code == 404
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_watchlist_intelligence_alerts_and_digest(client, db, market, monkeypatch):
    me = _signup(client, "intel")
    client.post("/api/me/holdings", json={"symbol": "AAPL", "quantity": 5, "purchase_price": 180})
    assert client.put("/api/me/watchlist/EMAAR.AE").json() == {"symbol": "EMAAR.AE", "watching": True}
    assert [w["symbol"] for w in client.get("/api/me/watchlist").json()["items"]] == ["EMAAR.AE"]
    assert client.get("/api/me/track/AAPL").json() == {"signed_in": True, "holding": True, "watching": False}

    # route existing events past the dispatcher, then add new ones
    alerts.dispatch(db)
    stamp = random.randint(1, 10**9)
    evs = [
        SourceEvent(kind="earnings", provider="newsfeed", title="Apple reports quarterly results", url="https://example.com/aapl", publisher="Example",
                    published_at=utcnow(), facts={"headline": "Apple reports quarterly results", "summary": "Services revenue grew."}, assets=["AAPL"],
                    topics=["earnings"], importance=3, external_key=f"t:{stamp}:1"),
        SourceEvent(kind="price_move", provider="market_data", title="Emaar fell 2.0%", publisher="Market data (delayed)", published_at=utcnow(),
                    facts={"headline": "Emaar fell 2.0%", "change_pct": -2.0}, assets=["EMAAR.AE"], topics=["risk"], importance=2, external_key=f"t:{stamp}:2"),
        SourceEvent(kind="news", provider="newsfeed", title="Unrelated company news", url="https://example.com/x", publisher="Example",
                    published_at=utcnow(), facts={"headline": "Unrelated"}, assets=["XOM"], topics=[], importance=1, external_key=f"t:{stamp}:3"),
        SourceEvent(kind="price_move", provider="market_data", title="Emaar fell 9.0%", publisher="Market data (delayed)", published_at=utcnow(),
                    facts={"headline": "Emaar fell 9.0%", "change_pct": -9.0}, assets=["EMAAR.AE"], topics=["risk"], importance=3, external_key=f"t:{stamp}:4"),
    ]  # fmt: skip
    db.add_all(evs)
    db.commit()
    out = alerts.dispatch(db)
    assert out["events"] == 4
    mine = list(db.scalars(select(UserNotification).where(UserNotification.user_id == me["id"]).order_by(UserNotification.id)))
    titles = [n.title for n in mine]
    assert "Apple reports quarterly results" in titles and "Emaar fell 9.0%" in titles
    assert "Emaar fell 2.0%" not in titles  # below the 5% default threshold
    assert "Unrelated company news" not in titles  # not tracked
    apple = next(n for n in mine if n.symbol == "AAPL")
    assert apple.severity == "high" and apple.category == "important" and apple.source_url == "https://example.com/aapl"
    assert alerts.dispatch(db)["notifications"] == 0  # nothing twice

    lst = client.get("/api/me/notifications").json()
    assert lst["unread"] >= 2 and lst["items"][0]["source_name"]
    assert client.post("/api/me/notifications/read", json={"ids": [lst["items"][0]["id"]]}).json()["marked"] == 1
    assert client.get("/api/me/notifications").json()["unread"] == lst["unread"] - 1

    # preferences: switching a category off stops it
    prefs = client.put("/api/me/notification-preferences", json={"channels": {"important": "off"}, "price_move_pct": 1.5}).json()
    assert prefs["channels"]["important"] == "off" and prefs["price_move_pct"] == 1.5 and prefs["email_available"] is False
    assert client.put("/api/me/notification-preferences", json={"channels": {"important": "hourly"}}).status_code == 422
    rule = client.put("/api/me/alert-rules", json={"symbol": "EMAAR.AE", "event_type": "price_move", "enabled": False}).json()
    assert rule["rules"][0]["enabled"] is False

    # intelligence groups real developments by tracked asset
    intel = client.get("/api/me/intelligence").json()
    sym = {a["symbol"]: a for a in intel["assets"]}
    assert (
        intel["tracked"] == 2
        and sym["AAPL"]["relation"] == "holding"
        and sym["AAPL"]["developments"][0]["url"] == "https://example.com/aapl"
    )
    detail = client.get("/api/intelligence/AAPL").json()
    assert detail["upcoming"][0] == {"kind": "earnings", "date": "2026-10-30", "source": "Market data (company calendar)"}
    assert detail["position"]["quantity"] == 5

    def unavailable(*a, **k):  # type: ignore[no-untyped-def]
        raise llm.LLMUnavailable("no key")

    monkeypatch.setattr(llm, "chat", unavailable)
    off = client.post("/api/intelligence/AAPL/brief").json()
    assert off["available"] is False and "unavailable" in off["reason"]
    monkeypatch.setattr(llm, "chat", lambda *a, **k: {"content": '{"what_happened": "Apple reported results.", "why_it_may_matter": "It may update expectations.", '
                        '"perspectives": [{"view": "Services matter", "from": "members"}], "uncertainty": "Guidance unknown.", "cited_events": [1]}', "_model": "m"})  # fmt: skip
    ok = client.post("/api/intelligence/AAPL/brief").json()
    assert (
        ok["available"] and ok["sources"][0]["url"] == "https://example.com/aapl" and "Not financial advice" in ok["disclaimer"]
    )

    # digests are prepared, never claimed as sent without an email provider
    preview = client.get("/api/me/digest/preview").json()
    assert preview["subject"].startswith("Nexis Daily") and any(a["symbol"] == "AAPL" for a in preview["assets"])
    sent = client.post("/api/me/digest").json()
    assert sent["status"] == "prepared" and "nothing was sent" in sent["detail"]
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_reply_notifications(client, monkeypatch):
    monkeypatch.setattr("app.services.pulse.markets.quotes", lambda db, syms: [{"symbol": s, "name": s} for s in syms])
    author = _signup(client, "author")
    d = client.post("/api/pulse/discussions", json={"symbol": "MSFT", "title": "Azure growth versus capex spending",
                    "body": "Capex is rising faster than revenue right now, which worries me a little."}).json()  # fmt: skip
    _signup(client, "replier")
    client.post(f"/api/social/posts/{d['id']}/comments", json={"body": "The payback period is the real question."})
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert (
        client.post("/api/auth/login", json={"identifier": author["identifier"], "password": author["password"]}).status_code
        == 200
    )
    items = client.get("/api/me/notifications").json()["items"]
    assert (
        items[0]["category"] == "pulse"
        and "replied to your discussion" in items[0]["title"]
        and items[0]["link"].startswith(f"/pulse/discussion/{d['id']}")
    )
    client.post("/api/auth/logout")
    client.cookies.clear()
