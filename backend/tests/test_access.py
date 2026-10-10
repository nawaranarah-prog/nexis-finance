"""Accounts and access (Phase 1): visitors read; everything that writes, uses AI, saves or produces a report needs an
account — enforced by the API, not just the UI. Private reports belong to their maker. Account emails and login
lockout."""

from __future__ import annotations

import time

import pytest

from app.core.config import get_settings
from app.models import Report, User
from app.services import account, email
from tests.helpers import member

# Every endpoint a visitor must not reach, with a plausible body (the request must be refused before validation
# matters, so the body only needs to be well-formed).
RESTRICTED = [
    ("POST", "/api/advisor/chat", {"messages": [{"role": "user", "content": "hi"}]}),
    ("POST", "/api/advisor/chat/stream", {"messages": [{"role": "user", "content": "hi"}]}),
    ("POST", "/api/help/ask", {"question": "How do I cancel?"}),
    ("POST", "/api/intelligence/EMAAR.AE/brief", None),
    ("POST", "/api/assistant/ask", {"question": "What drove my volatility?"}),
    ("POST", "/api/markets/compare/report", {"symbols": ["EMAAR.AE", "FAB.AD"]}),
    ("POST", "/api/valuation/EMAAR.AE/report", {}),
    ("POST", "/api/pulse/discussions", {"title": "A visitor trying to post here"}),
    ("POST", "/api/pulse/discussions/abc123/comments", {"body": "a reply"}),
    ("POST", "/api/pulse/reactions", {"target_type": "discussion", "target_id": "abc123", "kind": "agree"}),
    ("POST", "/api/pulse/discussions/abc123/follow", {}),
    ("POST", "/api/pulse/discussions/abc123/save", {}),
    ("POST", "/api/pulse/reports", {"target_type": "discussion", "target_id": "abc123", "reason": "spam"}),
    ("POST", "/api/social/posts", {"body": "hello"}),
    ("POST", "/api/social/posts/1/like", {}),
    ("POST", "/api/social/posts/1/comments", {"body": "hi"}),
    ("POST", "/api/social/posts/1/save", {}),
    ("POST", "/api/social/users/someone/follow", {}),
    ("GET", "/api/me/portfolio", None),
    ("GET", "/api/billing/status", None),
    ("POST", "/api/auth/email/verify/send", {}),
]


def _out(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()


@pytest.mark.parametrize(("method", "path", "body"), RESTRICTED)
def test_visitors_are_refused_by_the_api(client, method, path, body):
    _out(client)
    r = client.request(method, path, json=body)
    assert r.status_code == 401, (path, r.status_code, r.text[:200])


def test_visitors_can_read_public_content(client):
    _out(client)
    for path in (
        "/api/pulse/feed",
        "/api/pulse/meta",
        "/api/help/articles",
        "/api/billing/plans",
        "/api/markets/lists",
        "/api/social/feed",
    ):
        assert client.get(path).status_code == 200, path


def test_tampered_ids_dont_bypass_identity(client):
    a = member(client, "tamper")
    # a body naming another account is ignored or rejected: identity comes from the session only
    r = client.post("/api/pulse/discussions", json={"title": "Is my user id in the body respected?", "user_id": 1})
    assert r.status_code == 422  # extra fields are rejected outright
    assert client.get("/api/auth/me").json()["user"]["id"] == a["id"]
    _out(client)


# ------------------------------------------------------------------ private reports


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


def _report(db, owner_id: int | None, title: str) -> int:  # type: ignore[no-untyped-def]
    r = Report(title=title, report_type="comparison", file_name=f"{title}.pdf", file_size=4, sha256="x" * 64, config={}, content=b"%PDF",
               owner_id=owner_id)  # fmt: skip
    db.add(r)
    db.commit()
    return r.id


def test_reports_are_private_to_their_maker(client, db):
    a = member(client, "repa")
    b = member(client, "repb")
    mine = _report(db, a["id"], f"a-{a['id']}")
    shared = _report(db, None, f"workspace-{a['id']}")
    # B (signed in) can't see or download A's report; the shared workspace report is public
    titles = [x["title"] for x in client.get("/api/reports").json()]
    assert f"a-{a['id']}" not in titles and f"workspace-{a['id']}" in titles
    assert client.get(f"/api/reports/{mine}/download").status_code == 404
    _out(client)
    assert client.get(f"/api/reports/{mine}/download").status_code == 404  # visitors neither
    assert client.get(f"/api/reports/{shared}/download").status_code == 200
    client.post("/api/auth/login", json={"identifier": a["identifier"], "password": a["password"]})
    assert client.get(f"/api/reports/{mine}/download").status_code == 200
    assert f"a-{a['id']}" in [x["title"] for x in client.get("/api/reports").json()]
    _out(client)
    assert b["id"] != a["id"]


# ------------------------------------------------------------------ account emails


@pytest.fixture
def outbox(monkeypatch):  # type: ignore[no-untyped-def]
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(email, "available", lambda: True)
    monkeypatch.setattr(email, "send", lambda to, subject, text: sent.append({"to": to, "subject": subject, "text": text}))
    return sent


def _link_token(text: str) -> str:
    return text.split("?token=")[1].split()[0]


def test_password_reset_without_email_configured_says_so(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_host", None)
    r = client.post("/api/auth/password/forgot", json={"email": "anyone@example.com"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "email_unavailable"
    assert client.get("/api/auth/providers").json()["email_delivery"] is False


def test_password_reset_works_once_and_signs_out_everywhere(client, outbox):
    m = member(client, "reset")
    _out(client)
    r1 = client.post("/api/auth/password/forgot", json={"email": m["identifier"]})
    r2 = client.post("/api/auth/password/forgot", json={"email": "nobody-here@example.com"})
    assert r1.json() == r2.json()  # never reveals whether the address has an account
    assert len(outbox) == 1 and outbox[0]["to"] == m["identifier"]
    token = _link_token(outbox[0]["text"])
    assert client.post("/api/auth/password/reset", json={"token": token, "password": "brand-new-pass-1"}).status_code == 204
    assert (
        client.post("/api/auth/password/reset", json={"token": token, "password": "another-pass-22"}).status_code == 422
    )  # single use
    assert client.post("/api/auth/login", json={"identifier": m["identifier"], "password": "member-pass-123"}).status_code == 401
    assert client.post("/api/auth/login", json={"identifier": m["identifier"], "password": "brand-new-pass-1"}).status_code == 200
    assert client.get("/api/auth/me").json()["user"]["email_verified"] is True  # reading the inbox proves the address
    _out(client)


def test_tokens_expire_and_cant_be_forged(client, db, outbox):
    m = member(client, "forge")
    u = db.get(User, m["id"])
    old = account.make_token("reset", u, 60, now=time.time() - 3600)
    assert client.post("/api/auth/password/reset", json={"token": old, "password": "brand-new-pass-1"}).status_code == 422
    good = account.make_token("reset", u, 600)
    forged = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    assert client.post("/api/auth/password/reset", json={"token": forged, "password": "brand-new-pass-1"}).status_code == 422
    verify = account.make_token("verify", u, 600)
    assert (
        client.post("/api/auth/password/reset", json={"token": verify, "password": "brand-new-pass-1"}).status_code == 422
    )  # wrong purpose
    _out(client)


def test_email_verification(client, outbox):
    member(client, "verify")
    assert client.get("/api/auth/me").json()["user"]["email_verified"] is False
    assert client.post("/api/auth/email/verify/send").json() == {"sent": True}
    token = _link_token(outbox[-1]["text"])
    _out(client)
    assert client.post("/api/auth/email/verify", json={"token": token}).json() == {"verified": True}


def test_login_locks_after_repeated_wrong_passwords(client):
    m = member(client, "lock")
    _out(client)
    for _ in range(10):
        assert (
            client.post("/api/auth/login", json={"identifier": m["identifier"], "password": "wrong-password-x"}).status_code
            == 401
        )
    r = client.post("/api/auth/login", json={"identifier": m["identifier"], "password": "member-pass-123"})
    assert r.status_code == 401 and "Too many wrong attempts" in r.json()["error"]["message"]  # even the right password waits
