"""Nexis Pulse: anonymity end to end, Terms acceptance, editorial updates, moderation and abuse protection.

The most important property is checked over and over: no public response ever contains who wrote something.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core import legal
from app.models import (
    ContentReport,
    LegalAcceptance,
    ModerationAction,
    PulseComment,
    PulseDiscussion,
    PulseDiscussionUpdate,
    User,
    UserNotification,
)
from app.services import pulse, pulse_editorial
from tests.helpers import TERMS

IDENTITY_KEYS = {"user_id", "author_id", "email", "username", "phone", "ip", "password", "password_hash", "reporter_id", "actor_id",
                 "resolver_id", "avatar_url", "linked"}  # fmt: skip
# Public responses also never carry internal moderation state or internal keys.
FORBIDDEN_KEYS = IDENTITY_KEYS | {"flags", "flagged", "legacy_post_id", "editorial_key"}


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


@pytest.fixture(autouse=True)
def quotes(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(pulse.markets, "quotes", lambda db, syms: [{"symbol": s, "name": f"{s} Corp", "price": 10.0} for s in syms])


def _out(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()


def _signup(client, tag: str, accept: bool = True) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    _out(client)
    mail = f"{tag}{random.randint(10**6, 10**7)}@example.com"
    body = {**TERMS, "email": mail, "password": "pulse-pass-123"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    me = client.get("/api/auth/me").json()["user"]
    return {"identifier": mail, "password": "pulse-pass-123", **me}


def _login(client, u: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    _out(client)
    assert client.post("/api/auth/login", json={"identifier": u["identifier"], "password": u["password"]}).status_code == 200


def _scan(obj: Any, people: list[dict[str, Any]], path: str = "$", keys: set[str] = FORBIDDEN_KEYS) -> None:
    """Fail if a response carries an identity key, or any identifying value of the given accounts."""
    secrets = {str(v) for p in people for v in (p["identifier"], p["username"], p["email"]) if v}
    names = {p["display_name"] for p in people if p.get("display_name") and len(p["display_name"]) > 4}
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k not in keys, f"{path}.{k} must not be public"
            _scan(v, people, f"{path}.{k}", keys)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan(v, people, f"{path}[{i}]", keys)
    elif isinstance(obj, str):
        for s in secrets | names:
            assert s.lower() not in obj.lower(), f"{path} leaks {s!r}"


def _discussion(client, **kw: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    body = {"title": "Is NVIDIA's valuation still justified after earnings?", "body": "Growth is strong but expectations are high.",
            "symbol": "NVDA", **kw}  # fmt: skip
    r = client.post("/api/pulse/discussions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ 1-2. signup and Terms acceptance


def test_signup_requires_explicit_terms_acceptance(client, db):
    _out(client)
    mail = f"noterms{random.randint(10**6, 10**7)}@example.com"
    r = client.post("/api/auth/register", json={"email": mail, "password": "pulse-pass-123"})
    assert r.status_code == 422 and "Terms" in r.json()["error"]["message"]
    r = client.post("/api/auth/register", json={**TERMS, "accept_terms": False, "email": mail, "password": "pulse-pass-123"})
    assert r.status_code == 422
    old = {**TERMS, "terms_version": "2000-01-01"}
    assert client.post("/api/auth/register", json={**old, "email": mail, "password": "pulse-pass-123"}).status_code == 422
    assert db.scalar(select(func.count(User.id)).where(User.email == mail)) == 0  # nothing half-created

    u = _signup(client, "terms")
    row = db.scalars(select(LegalAcceptance).where(LegalAcceptance.user_id == u["id"])).one()
    assert (row.terms_version, row.privacy_version, row.method) == (legal.TERMS_VERSION, legal.PRIVACY_VERSION, "signup")
    assert row.accepted_at is not None
    assert client.get("/api/auth/me").json()["user"]["legal"]["accepted"] is True
    _out(client)


def test_accounts_without_acceptance_can_read_but_not_post(client, db):
    u = _signup(client, "oauthlike")
    db.query(LegalAcceptance).filter(LegalAcceptance.user_id == u["id"]).delete()  # e.g. created through Google sign-in
    db.commit()
    assert client.get("/api/pulse/feed").status_code == 200
    r = client.post("/api/pulse/discussions", json={"title": "A question about bank margins", "body": ""})
    assert r.status_code == 403 and r.json()["error"]["code"] == "terms_required"
    assert client.post("/api/legal/accept", json={"terms_version": "1999", "privacy_version": legal.PRIVACY_VERSION}).status_code == 422
    ok = client.post("/api/legal/accept", json={"terms_version": legal.TERMS_VERSION, "privacy_version": legal.PRIVACY_VERSION}).json()
    assert ok["accepted"] is True
    assert db.scalars(select(LegalAcceptance).where(LegalAcceptance.user_id == u["id"])).one().method == "prompt"
    assert client.post("/api/pulse/discussions", json={"title": "A question about bank margins", "body": ""}).status_code == 201
    _out(client)


# ------------------------------------------------------------------ 3-7. anonymous posts, comments, replies, no leaks


def test_posts_comments_and_replies_are_anonymous_everywhere(client, db):
    a = _signup(client, "pulseauthor")
    d = _discussion(client, stance="question", topics=["ai", "valuation"])
    assert d["author"] == {"display_name": "Anonymous", "type": "community"} and d["viewer"]["is_mine"] is True
    assert d["kind"] == "community" and [t["key"] for t in d["topics"]] == ["ai", "valuation"]

    b = _signup(client, "pulsebull")
    c1 = client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "I think the growth numbers justify it."}).json()
    _login(client, a)
    c_op = client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "Fair, but margins could compress.", "parent_id": c1["id"]}).json()
    c = _signup(client, "pulsebear")
    c2 = client.post(f"/api/pulse/discussions/{d['id']}/comments",
                     json={"body": "I disagree. The expectations are already priced in.", "parent_id": c1["id"], "stance": "disagree"}).json()  # fmt: skip
    assert c2["depth"] == 1 and c2["author"] == {"display_name": "Anonymous", "type": "community", "is_op": False}

    _out(client)  # a logged-out visitor
    people = [a, b, c]
    full = client.get(f"/api/pulse/discussions/{d['id']}").json()
    thread = client.get(f"/api/pulse/discussions/{d['id']}/comments").json()
    feed = client.get("/api/pulse/feed", params={"sort": "latest"}).json()
    for payload in (full, thread, feed, client.get("/api/pulse/overview").json(), client.get("/api/pulse/search", params={"q": "NVIDIA"}).json(),
                    client.get("/api/pulse/assets/NVDA").json(), client.get(f"/api/pulse/discussions/{d['id']}/related").json()):  # fmt: skip
        _scan(payload, people)
    assert full["counts"]["comments"] == 3 and full["counts"]["participants"] == 3
    items = {x["id"]: x for x in thread["items"]}
    assert [x["id"] for x in thread["items"]] == [c1["id"], c_op["id"], c2["id"]]  # replies follow their parent
    assert items[c_op["id"]]["author"]["is_op"] is True and items[c1["id"]]["author"]["is_op"] is False
    assert all(x["author"]["display_name"] == "Anonymous" and not x["viewer"]["is_mine"] for x in thread["items"])

    # each account sees only its own posts marked as its own
    _login(client, b)
    mine = client.get(f"/api/pulse/discussions/{d['id']}/comments").json()["items"]
    assert [x["id"] for x in mine if x["viewer"]["is_mine"]] == [c1["id"]]
    _out(client)


def test_internal_record_keeps_the_author_for_moderation(client, db):
    a = _signup(client, "internal")
    d = _discussion(client, title="Will UAE banks keep their margins this year?", symbol=None)
    row = db.scalars(select(PulseDiscussion).where(PulseDiscussion.public_id == d["id"])).one()
    assert row.author_id == a["id"] and len(d["id"]) == 10 and not d["id"].isdigit()  # random public id, not the row id
    assert "uae_markets" in [t["key"] for t in d["topics"]] or "banks" in [t["key"] for t in d["topics"]]
    _out(client)


# ------------------------------------------------------------------ reactions, follows, saves


def test_reactions_are_real_and_exclusive(client, db):
    a = _signup(client, "reactor-a")
    d = _discussion(client)
    assert client.post("/api/pulse/reactions", json={"target_type": "discussion", "target_id": d["id"], "kind": "agree"}).status_code == 422  # own post
    _signup(client, "reactor-b")
    r = client.post("/api/pulse/reactions", json={"target_type": "discussion", "target_id": d["id"], "kind": "agree"}).json()
    assert r == {"counts": {"agree": 1, "disagree": 0, "interesting": 0}, "reactions": ["agree"]}
    r = client.post("/api/pulse/reactions", json={"target_type": "discussion", "target_id": d["id"], "kind": "disagree"}).json()
    assert r["counts"] == {"agree": 0, "disagree": 1, "interesting": 0} and r["reactions"] == ["disagree"]
    r = client.post("/api/pulse/reactions", json={"target_type": "discussion", "target_id": d["id"], "kind": "disagree"}).json()
    assert r["counts"]["disagree"] == 0 and r["reactions"] == []  # toggled off
    assert client.post("/api/pulse/reactions", json={"target_type": "discussion", "target_id": d["id"], "kind": "love"}).status_code == 422
    _login(client, a)
    assert client.get(f"/api/pulse/discussions/{d['id']}").json()["counts"]["agree"] == 0
    _out(client)


def test_empty_discussion_shows_zero_and_trending_never_invents_activity(client, db):
    _signup(client, "quiet")
    d = _discussion(client, title="A quiet question about bond yields nobody answered", symbol=None)
    full = client.get(f"/api/pulse/discussions/{d['id']}").json()
    assert full["counts"] == {"comments": 0, "participants": 1, "followers": 0, "agree": 0, "disagree": 0, "interesting": 0}
    assert client.get(f"/api/pulse/discussions/{d['id']}/comments").json()["items"] == []
    trending = client.get("/api/pulse/feed", params={"sort": "trending"}).json()
    ids = [x["id"] for x in trending["items"]]
    if trending.get("fallback") != "latest":  # other tests created real activity; quiet posts still never rank
        assert d["id"] not in ids
    _out(client)


# ------------------------------------------------------------------ 8-9. investments stay private


def test_investments_are_private_and_never_in_pulse(client, db):
    a = _signup(client, "investor")
    h = client.post("/api/me/holdings", json={"symbol": "NVDA", "quantity": 12, "purchase_price": 100}).json()
    d = _discussion(client)
    _out(client)
    pub = client.get(f"/api/pulse/discussions/{d['id']}").json()
    assert "12" not in str(pub["counts"]) and "holding" not in str(pub).lower()
    assert client.get("/api/me/portfolio").status_code == 401
    _signup(client, "snoop")
    assert client.get("/api/me/portfolio").json()["holdings"] == []
    assert client.patch(f"/api/me/holdings/{h['id']}", json={"quantity": 1}).status_code == 404
    assert client.delete(f"/api/me/holdings/{h['id']}").status_code == 404
    assert client.get("/api/me/notifications").json()["items"] == []
    _login(client, a)
    assert [x["symbol"] for x in client.get("/api/me/portfolio").json()["holdings"]] == ["NVDA"]
    _out(client)


# ------------------------------------------------------------------ 10-12. notifications, follows, editorial updates


def test_follow_editorial_updates_and_history(client, db):
    srcs = [{"key": "e1", "title": "Chipmaker beats revenue estimates", "url": "https://example.com/1", "publisher": "Example Wire"},
            {"key": "e2", "title": "Shares fall despite the beat", "url": "https://example.com/2", "publisher": "Market Daily"}]  # fmt: skip
    d, ups = pulse_editorial.publish(
        db, "asset:TESTCHIP", title="Why did the shares fall after an earnings beat?", what_happened="Revenue beat expectations.",
        debate="Whether guidance matters more than the quarter.", bull=[{"point": "Revenue growth remains strong.", "refs": ["e1"]}],
        bear=[{"point": "Valuation assumes years of growth.", "refs": ["e2"]}], open_questions=["Can margins hold as competition rises?"],
        sources=srcs[:1], symbols=["TESTCHIP"], asset_name="Test Chip Corp",
    )  # fmt: skip
    assert [u.kind for u in ups] == ["opened"] and d.kind == "editorial"
    u = _signup(client, "follower")
    assert client.post(f"/api/pulse/discussions/{d.public_id}/follow", json={}).json() == {"following": True}
    c = client.post(f"/api/pulse/discussions/{d.public_id}/comments", json={"body": "The guidance was the real story here."}).json()

    # new information arrives: the editorial content evolves, with a timestamped history
    d, ups = pulse_editorial.publish(
        db, "asset:TESTCHIP", title="Why did the shares fall after an earnings beat?", what_happened="Revenue beat, then shares fell.",
        debate="Whether guidance matters more than the quarter.", bull=[{"point": "Revenue growth remains strong.", "refs": ["e1"]}],
        bear=[{"point": "Valuation assumes years of growth.", "refs": ["e2"]}, {"point": "Guidance implies slower growth.", "refs": ["e2"]}],
        open_questions=["Can margins hold as competition rises?"], sources=srcs, symbols=["TESTCHIP"], what_changed="Shares fell despite the beat.",
    )  # fmt: skip
    kinds = [x.kind for x in ups]
    assert "development" in kinds and "new_bear" in kinds and "what_changed" in kinds
    _, again = pulse_editorial.publish(
        db, "asset:TESTCHIP", title="Why did the shares fall after an earnings beat?", what_happened="Revenue beat, then shares fell.",
        debate="Whether guidance matters more than the quarter.", bull=[{"point": "Revenue growth remains strong.", "refs": ["e1"]}],
        bear=[{"point": "Valuation assumes years of growth.", "refs": ["e2"]}, {"point": "Guidance implies slower growth.", "refs": ["e2"]}],
        open_questions=["Can margins hold as competition rises?"], sources=srcs, symbols=["TESTCHIP"],
    )  # fmt: skip
    assert again == []  # nothing material changed, nothing recorded

    full = client.get(f"/api/pulse/discussions/{d.public_id}").json()
    assert full["author"]["display_name"] == "Nexis" and full["editorial"]["bear_case"][1]["source_ids"]
    assert len(full["sources"]) == 2 and all(s["url"].startswith("https://") and s["retrieved_at"] for s in full["sources"])
    assert [x["kind"] for x in full["updates"]][-1] == "opened" and all(x["created_at"] for x in full["updates"])
    # the community comment underneath is untouched by editorial updates
    assert db.get(PulseComment, c["id"]).body == "The guidance was the real story here."
    note = db.scalars(select(UserNotification).where(UserNotification.user_id == u["id"], UserNotification.category == "discussions")).first()
    assert note is not None and note.title.startswith("Update:") and note.link.endswith("#updates")
    assert db.scalar(select(func.count(PulseDiscussionUpdate.id)).where(PulseDiscussionUpdate.discussion_id == d.id)) == len(full["updates"])
    _out(client)


def test_tracked_assets_get_new_discussion_alerts_and_for_you(client, db):
    w = _signup(client, "watcher")
    assert client.put("/api/me/watchlist/AMD").status_code == 200
    before = client.get("/api/pulse/feed", params={"sort": "for_you"}).json()["items"]
    assert all("AMD" not in x["symbols"] for x in before)  # related AI / semiconductor debates may appear; nothing about AMD yet
    _signup(client, "poster")
    d = _discussion(client, title="AMD data centre share: real or hype?", symbol="AMD")
    _login(client, w)
    n = client.get("/api/me/notifications").json()["items"]
    assert any(x["category"] == "pulse" and "AMD" in x["title"] and x["link"] == d["url"] for x in n)
    assert any(x["delivery"] == "digest" for x in n)  # Pulse alerts default to the daily digest, not the bell
    fy = client.get("/api/pulse/feed", params={"sort": "for_you"}).json()
    assert fy["items"][0]["id"] == d["id"] and "AMD" in fy["tracked"]
    prefs = client.put("/api/me/notification-preferences", json={"channels": {"discussions": "off", "pulse": "immediate"}}).json()
    assert prefs["channels"]["discussions"] == "off" and {c["key"] for c in prefs["categories"]} >= {"pulse", "discussions", "portfolio"}
    _out(client)


# ------------------------------------------------------------------ 13-15. reports, moderation, unauthorized access


def test_reports_moderation_and_access_control(client, db):
    a = _signup(client, "reported")
    d = _discussion(client, title="Thoughts on TSLA delivery numbers this quarter", symbol="TSLA")
    cm = client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "Deliveries look soft to me."}).json()
    assert client.post("/api/pulse/reports", json={"target_type": "discussion", "target_id": d["id"], "reason": "spam"}).status_code == 422  # own
    r = _signup(client, "reporter")
    assert client.post("/api/pulse/reports", json={"target_type": "comment", "target_id": str(cm["id"]), "reason": "nonsense"}).status_code == 422
    first = client.post("/api/pulse/reports", json={"target_type": "comment", "target_id": str(cm["id"]), "reason": "harassment", "detail": "rude"})
    assert first.status_code == 201 and first.json() == {"reported": True, "already": False}
    assert client.post("/api/pulse/reports", json={"target_type": "comment", "target_id": str(cm["id"]), "reason": "spam"}).json()["already"]
    assert db.scalar(select(func.count(ContentReport.id)).where(ContentReport.target_type == "comment", ContentReport.target_id == cm["id"])) == 1

    # members can't moderate, delete others' posts, or see the queue
    assert client.get("/api/moderation/queue").status_code == 403
    assert client.post("/api/moderation/actions", json={"target_type": "comment", "target_id": cm["id"], "action": "remove"}).status_code == 403
    assert client.delete(f"/api/pulse/comments/{cm['id']}").status_code == 404
    assert client.delete(f"/api/pulse/discussions/{d['id']}").status_code == 403
    _out(client)
    assert client.post("/api/pulse/discussions", json={"title": "Anonymous without an account?"}).status_code == 401
    assert client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "drive-by"}).status_code == 401
    assert client.post("/api/pulse/reports", json={"target_type": "comment", "target_id": "1", "reason": "spam"}).status_code == 401

    # a moderator reviews it without ever seeing who wrote or reported it
    m = _signup(client, "mod")
    db.get(User, m["id"]).role = "moderator"
    db.commit()
    q = client.get("/api/moderation/queue").json()
    entry = next(i for i in q["items"] if i["target_type"] == "comment" and i["target_id"] == cm["id"])
    assert entry["reports"][0]["reason"] == "harassment" and entry["author"]["display_name"] == "Anonymous"
    _scan(q, [a, r], keys=IDENTITY_KEYS)  # moderators see flags and reasons, never people
    out = client.post("/api/moderation/actions", json={"target_type": "comment", "target_id": cm["id"], "action": "remove", "reason": "abuse"}).json()
    assert out["status"] == "removed"
    assert db.scalars(select(ContentReport).where(ContentReport.target_type == "comment", ContentReport.target_id == cm["id"])).one().status == "actioned"
    assert db.scalars(select(ModerationAction).where(ModerationAction.target_id == cm["id"], ModerationAction.action == "remove")).one().actor_id == m["id"]
    _out(client)
    assert client.get(f"/api/pulse/discussions/{d['id']}").json()["counts"]["comments"] == 0
    assert all(x["id"] != cm["id"] for x in client.get(f"/api/pulse/discussions/{d['id']}/comments").json()["items"])


def test_financial_safety_flags_hold_scams_but_allow_strong_opinions(client, db):
    a = _signup(client, "scammer")
    held = _discussion(client, title="Guaranteed 40% returns every month", body="DM me on telegram to join my VIP signals group.", symbol=None)
    assert held["viewer"]["pending_review"] is True
    row = db.scalars(select(PulseDiscussion).where(PulseDiscussion.public_id == held["id"])).one()
    assert row.status == "held" and {f["key"] for f in row.flags} >= {"guaranteed_returns", "solicitation"}
    bearish = _discussion(client, title="I think this stock is wildly overvalued and will fall hard", body="The valuation makes no sense to me.")
    assert bearish["viewer"]["pending_review"] is False  # a strong negative opinion is not a violation
    assert client.get(f"/api/pulse/discussions/{held['id']}").status_code == 200  # the author can still see it
    _out(client)
    assert client.get(f"/api/pulse/discussions/{held['id']}").status_code == 404  # nobody else can
    assert held["id"] not in [x["id"] for x in client.get("/api/pulse/feed").json()["items"]]
    m = _signup(client, "mod2")
    db.get(User, m["id"]).role = "moderator"
    db.commit()
    assert any(i["target_id"] == row.id and i["status"] == "held" for i in client.get("/api/moderation/queue").json()["items"])
    client.post("/api/moderation/actions", json={"target_type": "discussion", "target_id": row.id, "action": "suspend_author", "days": 3})
    _login(client, a)
    r = client.post("/api/pulse/discussions", json={"title": "Trying again after the pause"})
    assert r.status_code == 403 and "paused" in r.json()["error"]["message"]
    _out(client)


def test_cross_site_writes_are_blocked(client):
    _signup(client, "csrf")
    r = client.post("/api/pulse/discussions", json={"title": "Posted from another website"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"
    ok = client.post("/api/pulse/discussions", json={"title": "Posted from Nexis itself, fine"}, headers={"origin": "https://nexis-finance-five.vercel.app"})
    assert ok.status_code == 201
    _out(client)


def test_rate_limits_apply_per_account(client, db, monkeypatch):
    monkeypatch.setattr(pulse.get_settings(), "pulse_comments_per_hour", 2)
    _signup(client, "limited")
    d = _discussion(client, title="Rate limit check on a reasonable question", symbol=None)
    for i in range(2):
        assert client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": f"reply number {i}"}).status_code == 201
    assert client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "one too many"}).status_code == 429
    _out(client)


# ------------------------------------------------------------------ account deletion and SEO


def test_deleting_an_account_erases_its_pulse_text(client, db):
    a = _signup(client, "leaving")
    d = _discussion(client, title="Leaving soon: what about oil prices?", symbol=None)
    _signup(client, "staying")
    keep = client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "Oil depends on OPEC discipline."}).json()
    _login(client, a)
    gone = client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "My own reply that should vanish."}).json()
    assert client.post("/api/auth/me/delete", json={"password": a["password"]}).status_code == 204
    client.cookies.clear()
    assert client.get(f"/api/pulse/discussions/{d['id']}").status_code == 404
    assert db.get(PulseComment, gone["id"]).body == "" and db.get(PulseComment, gone["id"]).author_id is None
    assert db.get(PulseComment, keep["id"]).body == "Oil depends on OPEC discipline."


def test_seo_render_and_sitemap(client, db, monkeypatch):
    from app.api.routes import seo

    shell = ('<html><head><title>Nexis</title><meta name="description" content="x" /><meta name="robots" content="index" />'
             '<link rel="canonical" href="https://x/" /><meta property="og:title" content="x" /></head><body><div id="root"></div></body></html>')  # fmt: skip
    monkeypatch.setattr(seo, "_shell", lambda: shell)
    a = _signup(client, "seo")
    d = _discussion(client, title="Apple services growth: durable or slowing?", symbol="AAPL", body="Services keep growing <script>alert(1)</script>")
    _out(client)
    html = client.get(f"/api/seo/render{d['url']}").text
    assert "<title>Apple services growth: durable or slowing? (AAPL) · Nexis Pulse</title>" in html
    assert '"@type": "DiscussionForumPosting"' in html and '"name": "Anonymous"' in html and f"{seo.SITE}{d['url']}" in html
    assert "<script>alert(1)</script>" not in html and a["username"] not in html
    assert client.get("/api/seo/render/pulse/d/doesnotexist").status_code == 404
    sm = client.get("/api/seo/sitemap.xml").text
    assert d["url"] in sm and "/my-nexis" not in sm and "/notifications" not in sm


# ------------------------------------------------------------------ the legacy migration


def test_legacy_pulse_posts_migrate_without_personas(tmp_path):
    """0011 → 0012 copies member and Nexis Research discussions, drops generated content and hides the originals."""
    import sqlalchemy as sa

    from alembic import command
    from app.db.init_db import alembic_config

    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0011")
    eng = sa.create_engine(url)
    now = "2026-10-01 10:00:00"
    with eng.begin() as c:
        for uid, name, kind in ((1, "member", "person"), (2, "nexis.research", "editorial"), (3, "fake.persona", "persona"), (4, "replier", "person")):
            c.execute(sa.text("insert into users (id, username, display_name, kind, auth_provider, language, is_disabled, created_at) "
                              "values (:i, :u, :u, :k, 'password', 'en', 0, :t)"), {"i": uid, "u": name, "k": kind, "t": now})  # fmt: skip
        for pid, uid, src, title in ((10, 1, "nexis", "Member view on NVDA"), (11, 2, "research", "Research note on NVDA"),
                                     (12, 3, "generated", "Generated persona post")):  # fmt: skip
            c.execute(sa.text("insert into posts (id, user_id, body, symbols, tags, like_count, comment_count, hidden, source, asset, asset_name, "
                              "title, created_at) values (:p, :u, 'body text', '[\"NVDA\"]', '[]', 3, 2, 0, :s, 'NVDA', 'NVIDIA', :t, :n)"),
                      {"p": pid, "u": uid, "s": src, "t": title, "n": now})  # fmt: skip
        c.execute(sa.text("insert into comments (id, post_id, user_id, body, generated, created_at) values "
                          "(100, 10, 4, 'real reply', 0, :n), (101, 10, 3, 'fake reply', 1, :n), (102, 10, 4, 'reply to fake', 0, :n)"), {"n": now})
        c.execute(sa.text("update comments set parent_id = 101 where id = 102"))
    command.upgrade(cfg, "0012")
    with eng.begin() as c:
        rows = c.execute(sa.text("select kind, title, author_id, comment_count, legacy_post_id, created_at from pulse_discussions order by legacy_post_id")).all()
        assert [(r[0], r[1], r[2], r[3], r[4]) for r in rows] == [("community", "Member view on NVDA", 1, 1, 10),
                                                                   ("editorial", "Research note on NVDA", None, 0, 11)]  # fmt: skip
        assert str(rows[1][5]) > now  # the back-dated research note gets its real (migration) time
        assert [r[0] for r in c.execute(sa.text("select body from pulse_comments")).all()] == ["real reply"]
        assert {r[0]: r[1] for r in c.execute(sa.text("select id, hidden from posts")).all()} == {10: 1, 11: 1, 12: 0}
        assert c.execute(sa.text("select count(*) from pulse_discussion_assets where symbol = 'NVDA'")).scalar() == 2
    command.downgrade(cfg, "0011")
    with eng.begin() as c:
        assert {r[0]: r[1] for r in c.execute(sa.text("select id, hidden from posts")).all()} == {10: 0, 11: 0, 12: 0}


def test_author_deletes_own_comment_and_thread_keeps_its_shape(client, db):
    a = _signup(client, "deleter")
    d = _discussion(client, title="Do semiconductor margins peak this cycle?", symbol=None)
    _signup(client, "other-deleter")
    parent = client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "Margins peak when supply catches up."}).json()
    _login(client, a)
    reply = client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "Supply is years away.", "parent_id": parent["id"]}).json()
    lone = client.post(f"/api/pulse/discussions/{d['id']}/comments", json={"body": "A second thought of mine."}).json()
    assert client.get(f"/api/pulse/discussions/{d['id']}").json()["counts"]["comments"] == 3
    assert client.delete(f"/api/pulse/comments/{lone['id']}").status_code == 204
    assert client.delete(f"/api/pulse/comments/{lone['id']}").status_code == 404  # already gone
    full = client.get(f"/api/pulse/discussions/{d['id']}").json()
    assert full["counts"]["comments"] == 2 and full["counts"]["participants"] == 2
    _signup(client, "third")  # someone else can't delete it, and it looks just like a missing comment
    assert client.delete(f"/api/pulse/comments/{reply['id']}").status_code == 404
    ids = [x["id"] for x in client.get(f"/api/pulse/discussions/{d['id']}/comments").json()["items"]]
    assert ids == [parent["id"], reply["id"]] and lone["id"] not in ids
    _out(client)
