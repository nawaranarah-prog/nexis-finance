"""Nexis Pro: entitlements, checkout, signed webhooks (idempotent, out-of-order safe), cancellation, failed payments,
usage limits and attempts to bypass them. Stripe's network calls are mocked; signatures are verified for real."""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
from datetime import timedelta
from typing import Any

import pytest
import stripe
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import BillingEvent, Subscription, UsageEvent, User, UserNotification
from app.services import advisor, entitlements, market_reports
from tests.helpers import TERMS

SECRET = "whsec_test_secret"
MONTHLY, YEARLY = "price_test_monthly", "price_test_yearly"


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


@pytest.fixture
def stripe_on(monkeypatch):  # type: ignore[no-untyped-def]
    s = get_settings()
    for k, v in (("stripe_secret_key", "sk_test_x"), ("stripe_webhook_secret", SECRET), ("stripe_pro_monthly_price_id", MONTHLY),
                 ("stripe_pro_yearly_price_id", YEARLY), ("free_advisor_limit", 2), ("pro_advisor_limit", 5), ("free_report_limit", 1)):  # fmt: skip
        monkeypatch.setattr(s, k, v)
    world: dict[str, Any] = {"subs": {}, "created": [], "canceled": []}
    monkeypatch.setattr(stripe.Customer, "create", lambda **kw: {"id": f"cus_{kw['metadata']['nexis_user_id']}"})

    def session_create(**kw):  # type: ignore[no-untyped-def]
        world["created"].append(kw)
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    monkeypatch.setattr(stripe.checkout.Session, "create", session_create)
    monkeypatch.setattr(stripe.billing_portal.Session, "create", lambda **kw: {"url": "https://billing.stripe.com/p/session/x"})
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid, **kw: world["subs"][sid])
    monkeypatch.setattr(stripe.Subscription, "cancel", lambda sid, **kw: world["canceled"].append(sid))
    monkeypatch.setattr(stripe.Price, "retrieve", lambda pid, **kw: {"unit_amount": 4900 if pid == MONTHLY else 49000, "currency": "aed",
                                                                     "recurring": {"interval": "month" if pid == MONTHLY else "year"},
                                                                     "product": {"name": "Nexis Pro"}})  # fmt: skip
    return world


def _out(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()


def _signup(client, tag: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    _out(client)
    mail = f"{tag}{random.randint(10**6, 10**7)}@example.com"
    assert client.post("/api/auth/register", json={**TERMS, "email": mail, "password": "billing-pass-1"}).status_code == 201
    return client.get("/api/auth/me").json()["user"] | {"password": "billing-pass-1", "identifier": mail}


def _sub(sid: str, uid: int, status: str = "active", days: int = 30, cancel: bool = False, price: str = MONTHLY) -> dict[str, Any]:
    now = int(time.time())
    return {"id": sid, "object": "subscription", "customer": f"cus_{uid}", "status": status, "cancel_at_period_end": cancel,
            "metadata": {"nexis_user_id": str(uid)}, "canceled_at": now if status == "canceled" else None,
            "items": {"data": [{"price": {"id": price}, "current_period_start": now - 86400, "current_period_end": now + days * 86400}]}}  # fmt: skip


def _send(client, event: dict[str, Any], secret: str = SECRET):  # type: ignore[no-untyped-def]
    payload = json.dumps(event)
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return client.post("/api/billing/webhook", content=payload, headers={"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"})


def _event(etype: str, obj: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"evt_{random.randint(10**9, 10**10)}", "object": "event", "type": etype, "data": {"object": obj}}


def _go_pro(client, world, user: dict[str, Any], sid: str | None = None) -> str:  # type: ignore[no-untyped-def]
    sid = sid or f"sub_{user['id']}_{random.randint(1, 10**6)}"
    world["subs"][sid] = _sub(sid, user["id"])
    r = _send(client, _event("checkout.session.completed", {"id": "cs_x", "object": "checkout.session", "mode": "subscription",
                                                           "client_reference_id": str(user["id"]), "subscription": sid}))  # fmt: skip
    assert r.status_code == 200, r.text
    return sid


# ------------------------------------------------------------------ plans and entitlement


def test_free_user_entitlement_and_plans(client, db, stripe_on):
    u = _signup(client, "free")
    st = client.get("/api/billing/status").json()
    assert st["plan"] == "free" and st["pro"] is False and st["usage"]["advisor"]["limit"] == 2
    plans = client.get("/api/billing/plans").json()
    assert plans["configured"] is True
    assert {p["key"]: (p["amount"], p["currency"], p["interval"]) for p in plans["plans"]} == {
        "pro_monthly": (49.0, "AED", "month"), "pro_yearly": (490.0, "AED", "year")}  # prices come from Stripe, never the code
    assert not entitlements.has_pro(db, db.get(User, u["id"]))
    _out(client)


def test_plans_without_stripe_show_no_invented_prices(client):
    p = client.get("/api/billing/plans").json()
    assert p["configured"] is False and p["plans"] == [] and p["limits"]["advisor"]["free"] == get_settings().free_advisor_limit


def test_checkout_requires_account_valid_plan_and_server_prices(client, stripe_on):
    _out(client)
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).status_code == 401
    u = _signup(client, "buyer")
    assert client.post("/api/billing/checkout", json={"plan": "pro_lifetime"}).status_code == 422
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly", "price": "price_attacker"}).status_code == 422
    r = client.post("/api/billing/checkout", json={"plan": "pro_yearly"}).json()
    assert r["url"].startswith("https://checkout.stripe.com/")
    kw = stripe_on["created"][-1]
    assert kw["line_items"] == [{"price": YEARLY, "quantity": 1}] and kw["client_reference_id"] == str(u["id"]) and kw["mode"] == "subscription"
    assert "payment_method_types" not in kw  # cards, Apple Pay and Google Pay come from the Stripe Dashboard
    assert "{CHECKOUT_SESSION_ID}" in kw["success_url"]
    # returning to the success URL alone grants nothing
    assert client.get("/api/billing/status").json()["plan"] == "free"
    _out(client)


def test_checkout_unavailable_when_not_configured(client):
    _signup(client, "early")
    r = client.post("/api/billing/checkout", json={"plan": "pro_monthly"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "billing_unavailable"
    _out(client)


# ------------------------------------------------------------------ webhooks


def test_webhook_signature_is_required(client, stripe_on):
    ev = _event("customer.subscription.updated", _sub("sub_forged", 1))
    assert _send(client, ev, secret="whsec_wrong").status_code == 400
    assert client.post("/api/billing/webhook", content=json.dumps(ev), headers={"content-type": "application/json"}).status_code == 400


def test_checkout_completed_grants_pro_and_duplicates_are_ignored(client, db, stripe_on):
    u = _signup(client, "pro")
    sid = f"sub_dup_{u['id']}"
    stripe_on["subs"][sid] = _sub(sid, u["id"])
    ev = _event("checkout.session.completed", {"id": "cs_1", "object": "checkout.session", "mode": "subscription",
                                               "client_reference_id": str(u["id"]), "subscription": sid})  # fmt: skip
    assert _send(client, ev).json() == {"received": True, "handled": True}
    assert _send(client, ev).json() == {"received": True, "duplicate": True}
    assert db.scalar(select(func.count(Subscription.id)).where(Subscription.user_id == u["id"])) == 1
    assert db.scalar(select(func.count(BillingEvent.id)).where(BillingEvent.event_id == ev["id"])) == 1
    st = client.get("/api/billing/status").json()
    assert st["plan"] == "pro" and st["status"] == "active" and st["renews_at"] and st["interval"] == "month"
    assert st["usage"]["advisor"]["limit"] == 5
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).status_code == 422  # no double subscription
    assert client.post("/api/billing/portal").json()["url"].startswith("https://billing.stripe.com/")
    _out(client)


def test_cancel_at_period_end_keeps_access_until_it_ends(client, db, stripe_on):
    u = _signup(client, "cancel")
    sid = _go_pro(client, stripe_on, u)
    stripe_on["subs"][sid] = _sub(sid, u["id"], cancel=True, days=10)
    _send(client, _event("customer.subscription.updated", stripe_on["subs"][sid]))
    st = client.get("/api/billing/status").json()
    assert st["pro"] and st["cancel_at_period_end"] and st["access_until"] and st["renews_at"] is None
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="canceled", days=-1)
    _send(client, _event("customer.subscription.deleted", stripe_on["subs"][sid]))
    assert client.get("/api/billing/status").json()["plan"] == "free"
    _out(client)


def test_out_of_order_events_keep_the_latest_state(client, db, stripe_on):
    u = _signup(client, "order")
    sid = _go_pro(client, stripe_on, u)
    stale = _sub(sid, u["id"], status="incomplete")
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="active")  # what Stripe says now
    _send(client, _event("customer.subscription.created", stale))  # arrives late
    assert client.get("/api/billing/status").json()["status"] == "active"
    _out(client)


def test_failed_payment_warns_then_follows_stripe(client, db, stripe_on):
    u = _signup(client, "failed")
    sid = _go_pro(client, stripe_on, u)
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="past_due")
    _send(client, _event("invoice.payment_failed", {"id": "in_1", "object": "invoice", "subscription": sid}))
    st = client.get("/api/billing/status").json()
    assert st["pro"] is True and st["payment_issue"] is True  # Stripe is retrying: access continues, with a notice
    note = db.scalars(select(UserNotification).where(UserNotification.user_id == u["id"], UserNotification.event_type == "billing")).one()
    assert "payment method" in note.body and "stripe" not in note.title.lower().replace("nexis pro", "")
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="unpaid")
    _send(client, _event("customer.subscription.updated", stripe_on["subs"][sid]))
    assert client.get("/api/billing/status").json()["plan"] == "free"
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="active")
    _send(client, _event("invoice.paid", {"id": "in_2", "object": "invoice", "parent": {"subscription_details": {"subscription": sid}}}))
    st = client.get("/api/billing/status").json()
    assert st["pro"] and not st["payment_issue"]
    _out(client)


def test_expired_subscription_is_not_pro_even_if_marked_active(client, db):
    sub = Subscription(user_id=1, status="active", current_period_end=utcnow() - timedelta(days=5))
    assert not entitlements.sub_is_pro(sub)
    for status in ("incomplete", "incomplete_expired", "unpaid", "canceled", "paused"):
        assert not entitlements.sub_is_pro(Subscription(user_id=1, status=status, current_period_end=utcnow() + timedelta(days=5)))
    for status in ("active", "trialing", "past_due"):
        assert entitlements.sub_is_pro(Subscription(user_id=1, status=status, current_period_end=utcnow() + timedelta(days=5)))


# ------------------------------------------------------------------ usage limits


def test_usage_limits_are_enforced_server_side(client, db, stripe_on, monkeypatch):
    monkeypatch.setattr(advisor, "ask", lambda db, messages, language=None: {"answer": "ok"})
    u = _signup(client, "meter")
    q = {"messages": [{"role": "user", "content": "Is Emaar cheap?"}]}
    assert [client.post("/api/advisor/chat", json=q).status_code for _ in range(2)] == [200, 200]
    r = client.post("/api/advisor/chat", json=q)
    assert r.status_code == 402
    err = r.json()["error"]
    assert err["code"] == "usage_limit" and err["details"]["upgrade"] is True and err["details"]["limit"] == 2 and err["details"]["pro_limit"] == 5
    assert client.get("/api/advisor/status").json()["usage"]["remaining"] == 0
    # Pro raises the allowance immediately, counted over the billing period
    _go_pro(client, stripe_on, u)
    assert client.post("/api/advisor/chat", json=q).status_code == 200
    _out(client)


def test_failed_requests_are_refunded(client, db, stripe_on, monkeypatch):
    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("model down")

    monkeypatch.setattr(advisor, "ask", boom)
    u = _signup(client, "refund")
    with pytest.raises(RuntimeError):  # the test client re-raises what would be a 500 in production
        client.post("/api/advisor/chat", json={"messages": [{"role": "user", "content": "hi there"}]})
    assert db.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.user_id == u["id"])) == 0
    _out(client)


def test_parallel_requests_cannot_exceed_the_limit(client, db, stripe_on):
    u = db.get(User, _signup(client, "race")["id"])
    from app.db import session as db_session

    results = []
    for _ in range(4):  # separate sessions, as separate tabs would be
        with db_session.SessionLocal() as s:
            try:
                entitlements.reserve(s, s.get(User, u.id), "advisor")
                results.append("ok")
            except entitlements.UsageLimitReached:
                results.append("limit")
    assert results == ["ok", "ok", "limit", "limit"]
    assert entitlements.used(db, u, "advisor", entitlements.period(db, u)[0]) == 2
    _out(client)


def test_visitors_get_a_taste_then_are_asked_to_sign_up(client, monkeypatch, stripe_on):
    monkeypatch.setattr(advisor, "ask", lambda db, messages, language=None: {"answer": "ok"})
    monkeypatch.setattr(get_settings(), "anonymous_advisor_per_day", 1)
    _out(client)
    hdr = {"x-forwarded-for": f"203.0.113.{random.randint(1, 250)}"}
    q = {"messages": [{"role": "user", "content": "What moved the DFM today?"}]}
    assert client.post("/api/advisor/chat", json=q, headers=hdr).status_code == 200
    r = client.post("/api/advisor/chat", json=q, headers=hdr)
    assert r.status_code == 401 and r.json()["error"]["code"] == "sign_in_required"  # an account, not a payment
    rep = client.post("/api/markets/compare/report", json={"symbols": ["AAPL", "MSFT"]})
    assert rep.status_code == 401 and rep.json()["error"]["code"] == "sign_in_required"


def test_reports_are_metered(client, db, stripe_on, monkeypatch):
    monkeypatch.setattr(market_reports, "generate_comparison", lambda *a, **k: {"id": 1, "title": "x"})
    _signup(client, "reports")
    body = {"symbols": ["AAPL", "MSFT"]}
    assert client.post("/api/markets/compare/report", json=body).status_code == 201
    r = client.post("/api/markets/compare/report", json=body)
    assert r.status_code == 402 and r.json()["error"]["details"]["feature"] == "report"
    _out(client)


def test_members_cannot_grant_themselves_pro(client, db, stripe_on):
    u = _signup(client, "sneaky")
    for body in ({"plan": "pro"}, {"role": "admin"}, {"subscription_status": "active"}):
        assert client.patch("/api/auth/me", json=body).status_code == 422
    assert client.post("/api/billing/confirm", json={"session_id": "cs_someone_elses"}).status_code in (422, 400)
    assert client.get("/api/billing/status").json()["plan"] == "free"
    assert db.get(User, u["id"]).role == "member"
    _out(client)


def test_confirm_only_accepts_the_members_own_session(client, db, stripe_on, monkeypatch):
    u = _signup(client, "confirm")
    sid = f"sub_conf_{u['id']}"
    stripe_on["subs"][sid] = _sub(sid, u["id"])
    sessions = {"cs_mine_session": {"client_reference_id": str(u["id"]), "subscription": sid},
                "cs_theirs_session": {"client_reference_id": "999999", "subscription": sid}}  # fmt: skip
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda s, **kw: sessions[s])
    assert client.post("/api/billing/confirm", json={"session_id": "cs_theirs_session"}).status_code == 422
    assert client.get("/api/billing/status").json()["plan"] == "free"
    assert client.post("/api/billing/confirm", json={"session_id": "cs_mine_session"}).json()["plan"] == "pro"
    _out(client)


def test_deleting_an_account_cancels_its_subscription(client, db, stripe_on):
    u = _signup(client, "leaver")
    sid = _go_pro(client, stripe_on, u)
    assert client.post("/api/auth/me/delete", json={"password": u["password"]}).status_code == 204
    assert stripe_on["canceled"] == [sid]
    client.cookies.clear()
