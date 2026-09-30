"""Global markets, comparisons, valuation, the advisor, accounts and InstaFin — external HTTP mocked."""

from __future__ import annotations

import io
import json
import math

import httpx
import numpy as np
import pandas as pd
import pytest
from PIL import Image

from app.core.errors import ConfigurationError
from app.markets.news import clean_company_name
from app.markets.yahoo import YahooClient
from app.services import advisor, llm, valuation
from app.services import compare as cmp


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ provider parsing


def test_yahoo_chart_and_search_parsing():
    chart = {"chart": {"result": [{"meta": {"currency": "AED", "symbol": "EMAAR.AE", "regularMarketPrice": 11.5},
             "timestamp": [1_790_000_000, 1_790_086_400, 1_790_172_800],
             "indicators": {"quote": [{"open": [11, 11.2, None], "high": [11.3, 11.4, None], "low": [10.9, 11, None],
                                       "close": [11.2, 11.3, None], "volume": [100, 200, None]}],
                            "adjclose": [{"adjclose": [11.1, 11.3, None]}]}}]}}  # fmt: skip
    search = {"quotes": [{"symbol": "EMAAR.AE", "longname": "Emaar Properties PJSC", "exchDisp": "DFM", "typeDisp": "Equity", "isYahooFinance": True},
                         {"symbol": "X", "isYahooFinance": False}],
              "news": [{"title": "Emaar results", "publisher": "Gulf News", "link": "https://example.com/a", "providerPublishTime": 1_790_000_000}]}  # fmt: skip

    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chart if "/chart/" in r.url.path else search)

    y = YahooClient(client=_client(handler))
    c = y.chart("EMAAR.AE", "1d", range_="5d")
    assert [b["close"] for b in c["bars"]] == [11.2, 11.3]  # the null bar is dropped
    assert c["bars"][0]["adj_close"] == 11.1 and c["meta"]["currency"] == "AED"
    s = y.search("emaar", news=1)
    assert [q["symbol"] for q in s["quotes"]] == ["EMAAR.AE"] and s["quotes"][0]["type"] == "equity"
    assert s["news"][0]["publisher"] == "Gulf News" and s["news"][0]["published_at"].startswith("2026")


def test_yahoo_unknown_symbol_is_a_provider_error():
    y = YahooClient(
        client=_client(lambda r: httpx.Response(200, json={"chart": {"result": None, "error": {"description": "No data found"}}}))
    )
    with pytest.raises(Exception, match="No data found"):
        y.chart("NOPE.AE", "1d", range_="5d")


def test_company_name_cleaning_for_news_queries():
    assert clean_company_name("Emaar Properties PJSC") == "Emaar Properties"
    assert clean_company_name("Dubai Islamic Bank P.J.S.C.") == "Dubai Islamic Bank"
    assert clean_company_name("Apple Inc.") == "Apple"


# ------------------------------------------------------------------ comparison maths


def _frame() -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(7)
    a = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, len(idx)))
    b = 50 * np.cumprod(1 + rng.normal(0.0, 0.02, len(idx)))
    return pd.DataFrame({"AAA": a, "BBB": b}, index=idx)


def test_metrics_match_hand_calculation():
    p = _frame()["AAA"]
    m = cmp._metrics(p, rf=0.0)
    assert m["total_return"] == pytest.approx(p.iloc[-1] / p.iloc[0] - 1)
    years = (p.index[-1] - p.index[0]).days / 365.25
    assert m["annualized_return"] == pytest.approx((1 + m["total_return"]) ** (1 / years) - 1)
    w = p / p.iloc[0]
    assert m["max_drawdown"] == pytest.approx((w / w.cummax() - 1).min())
    r = p.pct_change().dropna()
    assert m["annualized_volatility"] == pytest.approx(r.std() * math.sqrt(len(r) / years))


def test_short_windows_do_not_report_sharpe():
    p = _frame()["AAA"].iloc[:30]
    assert cmp._metrics(p, 0.0)["sharpe_ratio"] is None


def test_compare_end_to_end_with_stubbed_prices(monkeypatch):
    f = _frame()
    monkeypatch.setattr(cmp.markets, "close_frame", lambda db, syms, iv, s, e: (f[syms], {s: {"currency": "USD"} for s in syms}))
    snap = {"quote": {"price": 1.0, "week52_low": 0.5, "week52_high": 1.5}, "valuation": {"pe_trailing": 10.0, "beta": 1.0},
            "analysts": {"recommendation_mean": 2.0}, "dividends": {"yield": 0.02}, "profile": {}, "name": "x", "type": "equity",
            "exchange": "X", "currency": "USD"}  # fmt: skip
    monkeypatch.setattr(cmp.markets, "details", lambda db, s: snap)
    r = cmp.compare(None, ["AAA", "BBB"], start=f.index[0].date(), end=f.index[-1].date(), interval="1d", bucket="month")  # type: ignore[arg-type]
    assert r["symbols"] == ["AAA", "BBB"] and r["interval"] == "1d"
    assert r["chart"]["series"]["AAA"][0] == pytest.approx(100.0)
    months = r["periodic_returns"]
    compounded = np.prod([1 + row["AAA"] for row in months]) - 1
    assert compounded == pytest.approx(r["metrics"]["AAA"]["total_return"], rel=1e-9)  # buckets chain to the total
    assert r["correlation"]["AAA"]["AAA"] == pytest.approx(1.0)
    sc = r["scorecard"]
    assert sc["available"] and set(sc["ranking"]) == {"AAA", "BBB"} and sum(sc["weights"].values()) == pytest.approx(1.0)
    assert sc["category_scores"]["valuation"] == {"AAA": 50.0, "BBB": 50.0}  # identical P/E: tied, not ranked apart


def test_window_validation():
    with pytest.raises(ConfigurationError):
        cmp.markets.resolve_window("7y", None, None, None)
    with pytest.raises(ConfigurationError, match="hourly"):
        cmp.markets.resolve_window("5y", None, None, "1h")
    _, e, iv = cmp.markets.resolve_window("5d", None, None, None)
    assert iv == "1h" and e is None


# ------------------------------------------------------------------ valuation maths


def _assumptions(**kw):  # type: ignore[no-untyped-def]
    a = {"base_fcf": 100.0, "years": 5, "growth_start": 0.05, "terminal_growth": 0.02, "risk_free_rate": 0.04, "beta": 1.0,
         "equity_risk_premium": 0.05, "country_risk_premium": 0.0, "pre_tax_cost_of_debt": 0.05, "tax_rate": 0.2,
         "cash": 50.0, "debt": 150.0, "shares_outstanding": 10.0, "market_cap": 900.0}  # fmt: skip
    return {**a, **kw}


def test_dcf_matches_hand_calculation():
    a = _assumptions()
    out = valuation.dcf(a, 0.08, 0.02)
    fcf, pv = 100.0, 0.0
    for t in range(1, 6):
        g = 0.05 + (0.02 - 0.05) * (t - 1) / 4
        fcf *= 1 + g
        pv += fcf / 1.08**t
    tv = fcf * 1.02 / (0.08 - 0.02)
    ev = pv + tv / 1.08**5
    assert out["enterprise_value"] == pytest.approx(ev)
    assert out["value_per_share"] == pytest.approx((ev - 150 + 50) / 10)


def test_wacc_uses_capm_and_market_weights():
    w = valuation.wacc(_assumptions())
    assert w["cost_of_equity"] == pytest.approx(0.09)
    assert w["after_tax_cost_of_debt"] == pytest.approx(0.04)
    assert w["wacc"] == pytest.approx(900 / 1050 * 0.09 + 150 / 1050 * 0.04)  # E = 900, D = 150


def test_dcf_rejects_wacc_below_growth_and_reverse_dcf_round_trips():
    with pytest.raises(ConfigurationError):
        valuation.dcf(_assumptions(), 0.02, 0.03)
    a = _assumptions()
    price = valuation.dcf({**a, "growth_start": 0.08}, 0.08, 0.02)["value_per_share"]
    got = valuation._implied_growth(a, 0.08, 1.0, price)
    assert got["growth"] == pytest.approx(0.08, abs=1e-6)


# ------------------------------------------------------------------ advisor


def test_request_parsing():
    assert advisor.parse_request("whats up w emmar stocks i wanna buy 500 shares")["shares"] == 500
    assert advisor.parse_request("thinking of 2k shares")["shares"] == 2000
    r = advisor.parse_request("I have AED 25,000 to invest")
    assert r["amount"] == 25000 and r["amount_currency"] == "AED"
    r = advisor.parse_request("put 5k dollars into apple")
    assert r["amount"] == 5000 and r["amount_currency"] == "USD"


def test_advisor_runs_tools_then_answers(client, monkeypatch):
    calls = []

    def fake_chat(messages, tools=None, **kw):  # type: ignore[no-untyped-def]
        calls.append(messages)
        if len(calls) == 1:
            return {"content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "position_calculator",
                    "arguments": json.dumps({"symbol": "EMAAR.AE", "shares": 500})}}]}  # fmt: skip
        tool_msg = messages[-1]
        assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["cost"] == pytest.approx(5770.0)
        return {"content": "**Snapshot** 500 shares cost 5,770 AED. *Educational analysis, not personalised advice.*"}

    def fake_stream(messages, tools=None, **kw):  # type: ignore[no-untyped-def]
        msg = fake_chat(messages, tools)
        if msg.get("content"):
            yield ("text", msg["content"])
        yield ("message", {"role": "assistant", **msg})

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    monkeypatch.setattr(llm, "status", lambda: {"configured": True, "provider": "test", "model": "m", "reason": None})
    monkeypatch.setattr(advisor.markets, "details", lambda db, s: {
        "symbol": "EMAAR.AE", "name": "Emaar Properties PJSC", "currency": "AED",
        "quote": {"price": 11.54, "avg_volume": 1e7, "week52_low": 10.15, "week52_high": 17.25},
        "analysts": {"target_mean": 16.2, "target_low": 12.5}, "dividends": {"rate": 1.0}})  # fmt: skip
    r = client.post("/api/advisor/chat", json={"messages": [{"role": "user", "content": "buy 500 emaar shares?"}]}).json()
    assert (
        r["mode"] == "ai"
        and "5,770" in r["answer"]
        and r["tools"][0] == {"tool": "position_calculator", "args": {"symbol": "EMAAR.AE", "shares": 500}, "ok": True}
    )
    assert r["disclaimer"]


def test_advisor_falls_back_to_data_briefing(monkeypatch, client):
    def unavailable(*a, **k):  # type: ignore[no-untyped-def]
        raise llm.LLMUnavailable("no model here")

    monkeypatch.setattr(llm, "chat", unavailable)
    monkeypatch.setattr(llm, "chat_stream", unavailable)
    monkeypatch.setattr(
        advisor,
        "briefing",
        lambda db, q, *rest: {"answer": "data only", "mode": "data", "tools": [], "symbols": [], "news": [], "disclaimer": "d"},
    )
    r = client.post("/api/advisor/chat", json={"messages": [{"role": "user", "content": "hello"}]}).json()
    assert r["mode"] == "data" and r["ai"] == {"used": False, "reason": "no model here"}


def test_llm_not_configured_without_credentials(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    llm.set_request_token(None)
    assert llm.status()["configured"] is False
    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "hi"}])


# ------------------------------------------------------------------ accounts & InstaFin


def _png_with_exif() -> bytes:
    im = Image.new("RGB", (2000, 1000), (30, 90, 200))
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def test_password_hashing():
    from app.services.auth import hash_password, verify_password

    h = hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert verify_password("correct horse battery", h) and not verify_password("wrong", h)


def test_instafin_end_to_end(client, monkeypatch):
    from app.services import social

    monkeypatch.setattr(
        social.markets,
        "quotes",
        lambda db, syms: [{"symbol": s, "name": s, "price": 10.0, "change_pct": 0.01, "currency": "AED"} for s in syms],
    )
    client.cookies.clear()
    assert client.get("/api/auth/me").json() == {"user": None}
    assert client.post("/api/social/posts", data={"body": "hi"}).status_code == 401
    bad = client.post("/api/auth/register", json={"username": "No Spaces!", "password": "longenough1"})
    assert bad.status_code == 422
    r = client.post(
        "/api/auth/register", json={"username": "trader_one", "password": "longenough1", "display_name": "Trader One"}
    )
    assert r.status_code == 201 and r.json()["username"] == "trader_one"
    assert client.post("/api/auth/register", json={"username": "trader_one", "password": "longenough1"}).status_code == 409
    assert client.get("/api/auth/me").json()["user"]["display_name"] == "Trader One"

    p = client.post("/api/social/posts", data={"body": "Loading up on $EMAAR.AE before results #dubai #realestate"},
                    files={"image": ("chart.jpg", _png_with_exif(), "image/jpeg")})  # fmt: skip
    assert p.status_code == 201, p.text
    post = p.json()
    assert [s["symbol"] for s in post["symbols"]] == ["EMAAR.AE"] and post["symbols"][0]["price"] == 10.0
    assert post["tags"] == ["dubai", "realestate"] and post["is_mine"]
    img = client.get(post["image_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    im = Image.open(io.BytesIO(img.content))
    assert max(im.size) <= 1440 and not im.getexif()  # resized and metadata stripped
    assert client.post("/api/social/posts", files={"image": ("x.jpg", b"not an image", "image/jpeg")}).status_code == 422

    assert client.post(f"/api/social/posts/{post['id']}/like").json() == {"liked": True, "like_count": 1}
    assert client.post(f"/api/social/posts/{post['id']}/like").json() == {"liked": False, "like_count": 0}
    c = client.post(f"/api/social/posts/{post['id']}/comments", json={"body": "Nice setup"}).json()
    assert client.get(f"/api/social/posts/{post['id']}").json()["comment_count"] == 1
    assert client.get("/api/social/feed", params={"symbol": "emaar.ae"}).json()["items"][0]["id"] == post["id"]
    assert client.get("/api/social/feed", params={"tag": "dubai"}).json()["items"][0]["id"] == post["id"]

    # A second user: cannot delete someone else's post, can follow, like and report.
    client.post("/api/auth/logout")
    client.cookies.clear()
    client.post("/api/auth/register", json={"username": "investor_two", "password": "longenough2"})
    assert client.delete(f"/api/social/posts/{post['id']}").status_code == 403
    assert client.delete(f"/api/social/comments/{c['id']}").status_code == 403
    assert client.post("/api/social/users/trader_one/follow").json()["followed_by_me"] is True
    assert client.get("/api/social/feed", params={"mode": "following"}).json()["items"][0]["id"] == post["id"]
    prof = client.get("/api/social/users/trader_one").json()
    assert "followers" not in prof and prof["posts"] >= 1 and prof["followed_by_me"]  # no follower counts, only following
    assert (
        client.patch("/api/auth/me", json={"links": {"instagram": "@investor.two", "website": "http://insecure"}}).status_code
        == 422
    )
    me = client.patch("/api/auth/me", json={"bio": "Long-term investor", "links": {"instagram": "@investor.two"}}).json()
    assert me["links"] == {"instagram": "investor.two"}
    assert client.post(f"/api/social/posts/{post['id']}/report", json={"reason": "spam"}).json()["hidden"] is False
    assert client.post(f"/api/social/posts/{post['id']}/report", json={}).status_code == 409
    trending = client.get("/api/social/trending").json()
    assert "EMAAR.AE" in [s["symbol"] for s in trending["symbols"]] and "dubai" in [t["tag"] for t in trending["tags"]]

    # Wrong password is rejected; the author can delete their own post.
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "trader_one", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "TRADER_ONE", "password": "longenough1"}).status_code == 200
    assert client.delete(f"/api/social/posts/{post['id']}").status_code == 204
    assert client.get(f"/api/social/posts/{post['id']}").status_code == 404
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_quarter_labels_are_distinct():
    labels = {cmp._bucket_label(pd.Timestamp(d), "quarter") for d in ("2026-03-31", "2026-06-30", "2026-09-30", "2026-12-31")}
    assert labels == {"2026 Q1", "2026 Q2", "2026 Q3", "2026 Q4"}


def test_advisor_status_reflects_real_availability(client, monkeypatch):
    from app.db import session as db_session
    from app.models import MarketCache

    with db_session.SessionLocal() as db:
        row = db.get(MarketCache, "llm:state")
        if row is not None:
            db.delete(row)
            db.commit()
    monkeypatch.setattr(
        llm, "status", lambda: {"configured": True, "provider": "vercel-ai-gateway", "model": "m", "reason": None}
    )

    def locked(*a, **k):  # type: ignore[no-untyped-def]
        raise llm.LLMUnavailable("gateway not activated")

    monkeypatch.setattr(llm, "chat", locked)
    s = client.get("/api/advisor/status").json()
    assert s["configured"] is True and s["available"] is False and s["last_error"] == "gateway not activated"


def test_account_deletion_removes_everything(client, monkeypatch):
    from app.db import session as db_session
    from app.models import Post, User
    from app.services import social

    monkeypatch.setattr(social.markets, "quotes", lambda db, syms: [])
    client.cookies.clear()
    client.post("/api/auth/register", json={"username": "leaver", "password": "longenough9"})
    pid = client.post("/api/social/posts", data={"body": "bye $AAPL"}).json()["id"]
    client.post(f"/api/social/posts/{pid}/like")
    assert client.post("/api/auth/me/delete", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/auth/me/delete", json={"password": "longenough9"}).status_code == 204
    client.cookies.clear()
    with db_session.SessionLocal() as db:
        assert db.query(User).filter(User.username == "leaver").count() == 0
        assert db.get(Post, pid) is None
    assert client.get("/api/social/users/leaver").status_code == 404
