"""Finstagram news pages, engagement (save, suggest more/less, topic follows, search), email and OAuth
sign-in, and the streaming advisor — all external HTTP mocked."""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.markets import feeds
from app.services import advisor, llm, oauth, social

RSS = b"""<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>Emaar posts record sales &amp; profit</title><link>https://example.com/business/emaar-record</link>
<pubDate>Wed, 30 Sep 2026 10:00:00 +0000</pubDate><description>&lt;p&gt;Dubai developer Emaar Properties reported record quarterly sales
driven by strong demand for off-plan homes across the city.&lt;/p&gt;</description>
<media:content url="https://img.example.com/emaar.jpg" medium="image" width="1200" height="800"/></item>
<item><title>Sport story</title><link>https://example.com/sport/x</link><pubDate>Wed, 30 Sep 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""

YT = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"
xmlns:media="http://search.yahoo.com/mrss/"><entry><yt:videoId>abc123XYZ</yt:videoId><title>Markets close higher</title>
<published>2026-09-30T08:00:00+00:00</published><media:group><media:description>Stocks rallied on Tuesday as investors cheered strong earnings.</media:description></media:group></entry></feed>"""


def test_rss_and_youtube_parsing(monkeypatch):
    monkeypatch.setattr(feeds, "_get", lambda url, timeout: YT if "youtube" in url else RSS)
    items = feeds.rss("https://example.com/feed", "Example News", r"/business/")
    assert len(items) == 1  # the sport link is filtered out
    it = items[0]
    assert it["title"] == "Emaar posts record sales & profit" and it["image"] == "https://img.example.com/emaar.jpg"
    assert it["summary"].startswith("Dubai developer Emaar") and it["published_at"].startswith("2026-09-30T10:00")
    v = feeds.youtube("UCx", "Market TV")[0]
    assert (
        v["video_id"] == "abc123XYZ"
        and v["image"] == "https://i.ytimg.com/vi/abc123XYZ/hqdefault.jpg"
        and v["url"].endswith("abc123XYZ")
    )


def test_excerpt_is_short_plain_text():
    long = "<p>" + "word " * 200 + "</p>"
    ex = feeds.excerpt(long)
    assert ex is not None and len(ex) <= 281 and ex.endswith("…") and "<" not in ex


def test_news_pages_import_with_attribution(client, monkeypatch):
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db import session as db_session
    from app.models import Post, User
    from app.services import chartposts, newsfeed

    monkeypatch.setattr(get_settings(), "external_data_enabled", True)
    monkeypatch.setattr(newsfeed, "_fetch", lambda src: [
        {"title": f"Emaar headline from {src[0]} {src[1]}", "url": f"https://example.com/{src[0]}/{abs(hash(src)) % 10**6}",
         "publisher": "Example", "published_at": "2026-09-30T09:00:00+00:00", "summary": "A short excerpt of the article for the feed card.",
         "image": "https://img.example.com/a.jpg"}])  # fmt: skip
    monkeypatch.setattr(chartposts, "publish", lambda db, page, hot: 0)
    with db_session.SessionLocal() as db:
        r = newsfeed.refresh(db, force=True)
        assert r["added"] > 0
        again = newsfeed.refresh(db)
        assert again["skipped"] is True  # within the staleness window
        post = db.scalars(select(Post).where(Post.link_url.is_not(None))).first()
        page = db.get(User, post.user_id)
        assert page.kind == "page" and "Automated" in page.bio
        assert post.link_source == "Example" and "EMAAR.AE" in post.symbols and post.link_image
        n = db.scalar(select(Post.id).where(Post.link_url.is_not(None)).limit(1))
        assert n is not None


def test_engagement_save_feedback_topics_and_search(client, monkeypatch):
    monkeypatch.setattr(social.markets, "quotes", lambda db, syms: [])
    monkeypatch.setattr(
        social.markets,
        "search",
        lambda db, q: [{"symbol": "EMAAR.AE", "name": "Emaar Properties PJSC", "type": "equity", "exchange": "DFM"}],
    )
    client.cookies.clear()
    r = client.post(
        "/api/auth/register", json={"email": "Reader@Example.com", "password": "reader-pass-1", "display_name": "Reader"}
    )
    assert r.status_code == 201, r.text
    me = r.json()
    assert me["username"] == "reader" and me["email"] == "reader@example.com"
    pid = client.post("/api/social/posts", data={"body": "Watching $EMAAR.AE #dubai"}).json()["id"]

    assert client.post(f"/api/social/posts/{pid}/save").json() == {"saved": True}
    saved = client.get("/api/social/feed", params={"mode": "saved"}).json()["items"]
    assert [p["id"] for p in saved] == [pid] and saved[0]["saved_by_me"]

    assert client.post("/api/social/topics/follow", json={"kind": "symbol", "value": "emaar.ae"}).json()["following"] is True
    topic = client.get("/api/social/topics/symbol/EMAAR.AE").json()
    assert topic["following"] is True
    assert client.get("/api/social/following").json()["symbols"] == ["EMAAR.AE"]

    res = client.get("/api/social/search", params={"q": "emaar"}).json()
    assert res["instruments"][0]["symbol"] == "EMAAR.AE"
    assert client.get("/api/social/search", params={"q": "#dub"}).json()["tags"][0]["tag"] == "dubai"
    assert any(a["username"] == "reader" for a in client.get("/api/social/search", params={"q": "read"}).json()["accounts"])

    assert client.post(f"/api/social/posts/{pid}/feedback", json={"signal": "less"}).json()["hidden"] is True
    ids = [p["id"] for p in client.get("/api/social/feed").json()["items"]]
    assert pid not in ids  # "suggest less" hides the post from this user's feed

    client.post("/api/auth/logout")
    client.cookies.clear()
    assert (
        client.post("/api/auth/login", json={"identifier": "READER@example.com", "password": "reader-pass-1"}).status_code == 200
    )
    assert (
        client.post("/api/auth/register", json={"email": "reader@example.com", "password": "another-pass-9"}).status_code == 409
    )
    client.post("/api/auth/logout")
    client.cookies.clear()


def _id_token(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


def test_google_sign_in_flow(client, monkeypatch):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "secret")
    monkeypatch.setattr(s, "public_url", "http://testserver")
    assert client.get("/api/auth/providers").json()["google"] is True
    client.cookies.clear()
    r = client.get("/api/auth/oauth/google/start", params={"next": "/social"}, follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["client_id"] == ["cid.apps.googleusercontent.com"] and q["code_challenge_method"] == ["S256"]
    state, nonce = q["state"][0], q["nonce"][0]

    def token_endpoint(url, data=None, timeout=None):  # type: ignore[no-untyped-def]
        assert data["code_verifier"] and data["code"] == "the-code"
        claims = {"iss": "https://accounts.google.com", "aud": "cid.apps.googleusercontent.com", "exp": time.time() + 600,
                  "sub": "g-123", "email": "gina@example.com", "email_verified": True, "name": "Gina Trader", "nonce": nonce}  # fmt: skip
        return httpx.Response(200, json={"id_token": _id_token(claims)})

    monkeypatch.setattr(oauth.httpx, "post", token_endpoint)
    bad = client.get("/api/auth/oauth/google/callback", params={"code": "the-code", "state": "forged"}, follow_redirects=False)
    assert bad.status_code == 302 and bad.headers["location"].startswith("/login?error=")
    r = client.get("/api/auth/oauth/google/start", params={"next": "/social"}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    state, nonce = q["state"][0], q["nonce"][0]
    ok = client.get("/api/auth/oauth/google/callback", params={"code": "the-code", "state": state}, follow_redirects=False)
    assert ok.status_code == 302 and ok.headers["location"] == "/social"
    me = client.get("/api/auth/me").json()["user"]
    assert me["email"] == "gina@example.com" and me["auth_provider"] == "google" and me["display_name"] == "Gina Trader"
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_oauth_unconfigured_redirects_with_message(client):
    r = client.get("/api/auth/oauth/google/start", follow_redirects=False)
    assert r.status_code == 302 and "not%20set%20up" in r.headers["location"]


def test_advisor_stream_endpoint_emits_status_delta_done(client, monkeypatch):
    def unavailable(*a, **k):  # type: ignore[no-untyped-def]
        raise llm.LLMUnavailable("locked")

    monkeypatch.setattr(llm, "chat_stream", unavailable)
    monkeypatch.setattr(
        advisor,
        "briefing",
        lambda db, q, *rest: {
            "answer": "Here's my take.",
            "mode": "data",
            "tools": [],
            "symbols": [],
            "news": [],
            "disclaimer": "d",
        },
    )
    r = client.post("/api/advisor/chat/stream", json={"messages": [{"role": "user", "content": "hi there"}]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status" and "delta" in kinds and kinds[-1] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Here's my take."
    assert events[-1]["meta"]["ai"] == {"used": False, "reason": "locked"}


def test_follow_up_questions_keep_the_instrument(monkeypatch):
    seen = {}

    def fake_answer(db, sym, text, pos, stats, news):  # type: ignore[no-untyped-def]
        seen["sym"] = sym
        return f"About {sym}", []

    monkeypatch.setattr(advisor.voice, "instrument_answer", fake_answer)
    monkeypatch.setattr(advisor, "resolve_symbol", lambda db, text: None)
    history = [{"role": "user", "content": "tell me about emaar"}, {"role": "assistant", "content": "Here's the picture on **Emaar Properties PJSC (EMAAR.AE)**"},
               {"role": "user", "content": "what about the dividend?"}]  # fmt: skip
    out = advisor.briefing(None, "what about the dividend?", history)  # type: ignore[arg-type]
    assert seen["sym"] == "EMAAR.AE" and out["answer"] == "About EMAAR.AE"


@pytest.mark.parametrize("q", ["what is a p/e ratio", "explain dividends"])
def test_glossary_questions(q):
    out = advisor.briefing(None, q)  # type: ignore[arg-type]
    assert out["mode"] == "data" and "**" in out["answer"] and out["symbols"] == []


def test_phone_normalisation():
    from app.services import phone

    for raw in ("050 123 4567", "0501234567", "501234567", "+971 50 123 4567", "00971501234567", "971501234567"):
        assert phone.normalize(raw) == "+971501234567", raw
    assert phone.normalize("+44 7700 900123") == "+447700900123"
    with pytest.raises(Exception, match="valid mobile"):
        phone.normalize("12")


def test_phone_signup_and_login_with_password(client):
    client.cookies.clear()
    r = client.post("/api/auth/register", json={"phone": "050 765 4321", "password": "phone-pass-77", "display_name": "Mona"})
    assert r.status_code == 201, r.text
    assert r.json()["phone"] == "+971507654321" and r.json()["display_name"] == "Mona"
    assert client.post("/api/auth/register", json={"phone": "+971507654321", "password": "other-pass-88"}).status_code == 409
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"identifier": "0507654321", "password": "phone-pass-77"}).status_code == 200
    assert client.get("/api/auth/me").json()["user"]["phone"] == "+971507654321"
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_phone_otp_flow_with_mocked_twilio(client, monkeypatch):
    from app.core.config import get_settings
    from app.services import phone

    s = get_settings()
    monkeypatch.setattr(s, "twilio_account_sid", "AC1")
    monkeypatch.setattr(s, "twilio_auth_token", "tok")
    monkeypatch.setattr(s, "twilio_verify_sid", "VA1")
    sent = {}

    def fake_post(url, data=None, auth=None, timeout=None):  # type: ignore[no-untyped-def]
        if url.endswith("/Verifications"):
            sent["to"] = data["To"]
            return httpx.Response(201, json={"status": "pending"})
        return httpx.Response(200, json={"status": "approved" if data["Code"] == "123456" else "pending"})

    monkeypatch.setattr(phone.httpx, "post", fake_post)
    client.cookies.clear()
    assert client.get("/api/auth/providers").json()["phone_otp"] is True
    assert client.post("/api/auth/phone/start", json={"phone": "055 111 2222"}).json() == {"sent": True, "phone": "+971551112222"}
    assert sent["to"] == "+971551112222"
    assert client.post("/api/auth/phone/verify", json={"phone": "0551112222", "code": "000000"}).status_code == 401
    ok = client.post("/api/auth/phone/verify", json={"phone": "0551112222", "code": "123456", "display_name": "Sara"})
    assert ok.status_code == 200 and ok.json()["display_name"] == "Sara" and ok.json()["auth_provider"] == "phone"
    client.post("/api/auth/logout")
    client.cookies.clear()
    # Second sign-in with a code reuses the same account.
    again = client.post("/api/auth/phone/verify", json={"phone": "+971551112222", "code": "123456"})
    assert again.json()["id"] == ok.json()["id"]
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_account_settings_password_details_language_sessions(client):
    from fastapi.testclient import TestClient

    client.cookies.clear()
    r = client.post("/api/auth/register", json={"email": "settings@example.com", "password": "first-pass-11"})
    assert r.status_code == 201
    other = TestClient(client.app)  # a second device
    assert (
        other.post("/api/auth/login", json={"identifier": "settings@example.com", "password": "first-pass-11"}).status_code == 200
    )

    assert (
        client.post("/api/auth/me/password", json={"current_password": "wrong", "new_password": "second-pass-22"}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/me/password", json={"current_password": "first-pass-11", "new_password": "second-pass-22"}
        ).status_code
        == 204
    )
    assert other.get("/api/auth/me").json()["user"] is None  # other devices are signed out after a password change
    assert client.get("/api/auth/me").json()["user"]["email"] == "settings@example.com"  # this device stays signed in

    me = client.patch("/api/auth/me", json={"username": "settings.user", "phone": "050 222 3333", "language": "ar"}).json()
    assert (
        me["username"] == "settings.user"
        and me["phone"] == "+971502223333"
        and me["language"] == "ar"
        and me["has_password"] is True
    )
    assert client.patch("/api/auth/me", json={"email": "not-an-email"}).status_code == 422
    assert client.patch("/api/auth/me", json={"language": "fr"}).status_code == 422

    assert other.post("/api/auth/login", json={"identifier": "0502223333", "password": "second-pass-22"}).status_code == 200
    assert client.post("/api/auth/me/logout-everywhere").json()["signed_out_sessions"] >= 1
    assert other.get("/api/auth/me").json()["user"] is None
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_passwordless_accounts_can_set_a_password(client, monkeypatch):
    from app.core.config import get_settings
    from app.services import phone

    s = get_settings()
    for k, v in (("twilio_account_sid", "AC1"), ("twilio_auth_token", "t"), ("twilio_verify_sid", "VA1")):
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(
        phone.httpx, "post", lambda url, data=None, auth=None, timeout=None: httpx.Response(200, json={"status": "approved"})
    )
    client.cookies.clear()
    u = client.post("/api/auth/phone/verify", json={"phone": "0559998877", "code": "123456"}).json()
    assert u["has_password"] is False
    assert client.post("/api/auth/me/password", json={"new_password": "brand-new-pass-1"}).status_code == 204
    assert client.get("/api/auth/me").json()["user"]["has_password"] is True
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_advisor_language_reaches_the_model(client, monkeypatch):
    seen = {}

    def fake_stream(messages, tools=None, **kw):  # type: ignore[no-untyped-def]
        seen["system"] = messages[0]["content"]
        yield ("text", "مرحبا")
        yield ("message", {"role": "assistant", "content": "مرحبا"})

    monkeypatch.setattr(llm, "chat_stream", fake_stream)
    r = client.post("/api/advisor/chat", json={"messages": [{"role": "user", "content": "hello"}], "language": "ar"}).json()
    assert "Arabic" in seen["system"] and r["answer"] == "مرحبا"
