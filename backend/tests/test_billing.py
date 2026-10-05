"""Nexis Pro, as specified: prices, limits, checkout, signed idempotent webhooks, cancellation, failed payments,
downgrades, usage resets and attempts to bypass any of it. Stripe's network calls are mocked; webhook signatures
are verified for real with Stripe's own library. The numbers asserted here are the final plan, not test values."""

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

from app.core import plans
from app.core.config import get_settings
from app.db.base import utcnow
from app.models import BillingEvent, Subscription, UsageEvent, User, UserHolding, UserNotification
from app.services import advisor, entitlements
from tests.helpers import TERMS

SECRET = "whsec_test_secret"
MONTHLY, YEARLY = "price_test_monthly", "price_test_yearly"


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


@pytest.fixture(autouse=True)
def quotes(monkeypatch):  # type: ignore[no-untyped-def]
    from app.services import markets

    monkeypatch.setattr(markets, "quotes", lambda db, syms: [{"symbol": s, "name": s, "price": 10.0, "currency": "USD"} for s in syms])
    monkeypatch.setattr(markets, "details", lambda db, s: {})


def _stripe_price(pid: str, amount: int | None = None) -> dict[str, Any]:
    want = plans.PRICES["pro_monthly" if pid == MONTHLY else "pro_yearly"]
    return {"id": pid, "active": True, "unit_amount": amount if amount is not None else want["amount_cents"], "currency": want["currency"],
            "recurring": {"interval": want["interval"], "interval_count": 1},
            "livemode": (get_settings().stripe_secret_key or "").startswith("sk_live_")}  # fmt: skip


@pytest.fixture
def stripe_on(monkeypatch, db):  # type: ignore[no-untyped-def]
    s = get_settings()
    for k, v in (("stripe_secret_key", "sk_test_x"), ("stripe_webhook_secret", SECRET), ("stripe_pro_monthly_price_id", MONTHLY),
                 ("stripe_pro_yearly_price_id", YEARLY)):  # fmt: skip
        monkeypatch.setattr(s, k, v)
    from app.models import MarketCache

    for row in db.scalars(select(MarketCache).where(MarketCache.key.like("billing:%"))):
        db.delete(row)
    db.commit()
    world: dict[str, Any] = {"subs": {}, "created": [], "canceled": [], "modified": [], "sessions": {}, "prices": {}, "expired": [],
                             "invoices": {}, "refunds": []}  # fmt: skip
    monkeypatch.setattr(stripe.Customer, "create", lambda **kw: {"id": f"cus_{kw['metadata']['nexis_user_id']}"})

    def session_create(**kw):  # type: ignore[no-untyped-def]
        sid = f"cs_test_{len(world['created']) + 1}"
        world["created"].append(kw)
        world["sessions"][sid] = {"id": sid, "status": "open", "url": f"https://checkout.stripe.com/c/pay/{sid}",
                                  "client_reference_id": kw["client_reference_id"], "subscription": None}  # fmt: skip
        return world["sessions"][sid]

    def modify(sid, **kw):  # type: ignore[no-untyped-def]
        world["modified"].append((sid, kw))
        world["subs"][sid]["cancel_at_period_end"] = kw["cancel_at_period_end"]
        return world["subs"][sid]

    monkeypatch.setattr(stripe.checkout.Session, "create", session_create)
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid, **kw: world["sessions"][sid])
    monkeypatch.setattr(stripe.billing_portal.Session, "create", lambda **kw: {"url": "https://billing.stripe.com/p/session/x", "return_url": kw["return_url"]})
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid, **kw: world["subs"][sid])
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: {"data": [x for x in world["subs"].values() if x["customer"] == kw["customer"]]})
    monkeypatch.setattr(stripe.Subscription, "modify", modify)
    def cancel(sid, **kw):  # type: ignore[no-untyped-def]
        world["canceled"].append(sid)
        if sid in world["subs"]:
            world["subs"][sid]["status"] = "canceled"

    def expire(sid, **kw):  # type: ignore[no-untyped-def]
        world["expired"].append(sid)
        world["sessions"][sid]["status"] = "expired"

    monkeypatch.setattr(stripe.Subscription, "cancel", cancel)
    monkeypatch.setattr(stripe.checkout.Session, "list", lambda **kw: {"data": [x for x in world["sessions"].values() if x["status"] == kw.get("status")]})
    monkeypatch.setattr(stripe.checkout.Session, "expire", expire)
    monkeypatch.setattr(stripe.Invoice, "retrieve", lambda iid, **kw: world["invoices"][iid])
    monkeypatch.setattr(stripe.InvoicePayment, "list", lambda **kw: {"data": [{"status": "paid", "payment": {"payment_intent": f"pi_{kw['invoice']}"}}]})
    monkeypatch.setattr(stripe.Refund, "create", lambda **kw: world["refunds"].append(kw) or {"id": "re_1"})
    monkeypatch.setattr(stripe.Price, "retrieve", lambda pid, **kw: world["prices"].get(pid) or _stripe_price(pid))
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


def _go_pro(client, world, user: dict[str, Any], price: str = MONTHLY) -> str:  # type: ignore[no-untyped-def]
    sid = f"sub_{user['id']}_{random.randint(1, 10**6)}"
    world["subs"][sid] = _sub(sid, user["id"], price=price)
    r = _send(client, _event("checkout.session.completed", {"id": "cs_x", "object": "checkout.session", "mode": "subscription",
                                                           "client_reference_id": str(user["id"]), "subscription": sid}))  # fmt: skip
    assert r.status_code == 200, r.text
    return sid


# ------------------------------------------------------------------ the plan itself


def test_final_plan_values():
    assert plans.ANONYMOUS == {"advisor_daily": 3}
    assert plans.LIMITS["free"] == {"advisor": 20, "my_nexis_assets": 3, "pulse_discussions": 3, "pulse_comments": 10}
    assert plans.LIMITS["pro"] == {"advisor": 600, "my_nexis_assets": 100, "pulse_discussions": 50, "pulse_comments": 100}
    assert {k: (v["amount_cents"], v["currency"], v["interval"]) for k, v in plans.PRICES.items()} == {
        "pro_monthly": (999, "usd", "month"), "pro_yearly": (9900, "usd", "year")}
    assert plans.yearly_value() == {"per_month_cents": 825, "twelve_monthly_cents": 11988, "savings_cents": 2088, "savings_pct": 17}


def test_plans_endpoint_serves_the_single_configuration(client, stripe_on):
    p = client.get("/api/billing/plans").json()
    assert p["available"] is True and p["limits"] == plans.LIMITS and p["anonymous"] == {"advisor_daily": 3}
    assert p["prices"] == {"pro_monthly": {"amount": 9.99, "currency": "USD", "interval": "month"},
                           "pro_yearly": {"amount": 99.0, "currency": "USD", "interval": "year"}}  # fmt: skip
    assert "secret" not in json.dumps(p).lower() and MONTHLY not in json.dumps(p)  # no keys, no price ids in the browser


def test_not_sellable_without_stripe_or_with_a_wrong_price(client, stripe_on, monkeypatch):
    stripe_on["prices"][MONTHLY] = _stripe_price(MONTHLY, amount=1099)  # someone typed $10.99 in Stripe
    p = client.get("/api/billing/plans").json()
    assert p["available"] is False and p["prices"]["pro_monthly"]["amount"] == 9.99
    _signup(client, "wrongprice")
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).status_code == 503
    monkeypatch.setattr(get_settings(), "stripe_secret_key", None)
    assert client.get("/api/billing/plans").json()["available"] is False
    _out(client)


# ------------------------------------------------------------------ free entitlements and checkout


def test_free_user_entitlements(client, db, stripe_on):
    u = _signup(client, "free")
    st = client.get("/api/billing/status").json()
    assert st["plan"] == "free" and st["pro"] is False and st["billing"] is None
    assert {k: (v["used"], v["limit"]) for k, v in st["usage"].items()} == {
        "advisor": (0, 20), "my_nexis_assets": (0, 3), "pulse_discussions": (0, 3), "pulse_comments": (0, 10)}
    assert not entitlements.has_pro(db, db.get(User, u["id"]))
    _out(client)
    assert client.get("/api/billing/status").status_code == 401  # billing data is private


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
    assert "payment_method_types" not in kw and "{CHECKOUT_SESSION_ID}" in kw["success_url"] and kw["cancel_url"].endswith("/pro?checkout=canceled")
    assert client.get("/api/billing/status").json()["plan"] == "free"  # starting checkout grants nothing
    _out(client)


def test_double_clicks_reuse_the_open_checkout(client, stripe_on):
    _signup(client, "double")
    a = client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).json()["url"]
    b = client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).json()["url"]
    assert a == b and len(stripe_on["created"]) == 1
    _out(client)


def test_existing_stripe_subscription_blocks_a_second_checkout(client, stripe_on):
    u = _signup(client, "already")
    sid = f"sub_early_{u['id']}"
    stripe_on["subs"][sid] = _sub(sid, u["id"])  # paid, but the webhook hasn't arrived yet
    r = client.post("/api/billing/checkout", json={"plan": "pro_yearly"})
    assert r.status_code == 409 and r.json()["error"] == {"code": "already_pro", "message": "You're already on Nexis Pro.", "details": None}
    assert client.get("/api/billing/status").json()["plan"] == "pro" and not stripe_on["created"]
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).status_code == 409
    _out(client)


# ------------------------------------------------------------------ webhooks


def test_webhook_signature_is_required(client, stripe_on):
    ev = _event("customer.subscription.updated", _sub("sub_forged", 1))
    assert _send(client, ev, secret="whsec_wrong").status_code == 400
    assert client.post("/api/billing/webhook", content=json.dumps(ev), headers={"content-type": "application/json"}).status_code == 400


def test_checkout_completed_grants_pro_once(client, db, stripe_on):
    u = _signup(client, "pro")
    sid = f"sub_dup_{u['id']}"
    stripe_on["subs"][sid] = _sub(sid, u["id"], price=YEARLY)
    ev = _event("checkout.session.completed", {"id": "cs_1", "object": "checkout.session", "mode": "subscription",
                                               "client_reference_id": str(u["id"]), "subscription": sid, "customer": f"cus_{u['id']}"})  # fmt: skip
    assert _send(client, ev).json() == {"received": True, "handled": True}
    assert _send(client, ev).json() == {"received": True, "duplicate": True}  # Stripe retried the delivery
    assert db.scalar(select(func.count(Subscription.id)).where(Subscription.user_id == u["id"])) == 1
    be = db.scalars(select(BillingEvent).where(BillingEvent.event_id == ev["id"])).one()
    assert be.status == "processed" and be.details == {"object": "cs_1", "customer": f"cus_{u['id']}", "subscription": sid}
    st = client.get("/api/billing/status").json()
    assert st["plan"] == "pro" and st["billing"] == "$99/year" and st["renews_at"]
    assert {k: v["limit"] for k, v in st["usage"].items()} == plans.LIMITS["pro"]
    assert client.post("/api/billing/portal").json()["url"].startswith("https://billing.stripe.com/")
    _out(client)


def test_unhandled_events_are_recorded_as_ignored(client, db, stripe_on):
    ev = _event("customer.created", {"id": "cus_new", "object": "customer"})
    assert _send(client, ev).status_code == 200
    assert db.scalars(select(BillingEvent).where(BillingEvent.event_id == ev["id"])).one().status == "ignored"


def test_out_of_order_events_keep_the_latest_state(client, stripe_on):
    u = _signup(client, "order")
    sid = _go_pro(client, stripe_on, u)
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="active")
    _send(client, _event("customer.subscription.created", _sub(sid, u["id"], status="incomplete")))  # arrives late
    assert client.get("/api/billing/status").json()["status"] == "active"
    _out(client)


def test_a_second_paid_subscription_is_canceled_and_refunded(client, db, stripe_on):
    """Monthly paid in one tab and yearly in another: the member keeps the first and is never charged twice."""
    u = _signup(client, "twotabs")
    first = _go_pro(client, stripe_on, u)
    second = f"sub_second_{u['id']}"
    stripe_on["subs"][second] = _sub(second, u["id"], price=YEARLY) | {"latest_invoice": f"in_{second}"}
    stripe_on["invoices"][f"in_{second}"] = {"id": f"in_{second}", "amount_paid": 9900}
    for etype in ("customer.subscription.created", "invoice.paid", "checkout.session.completed"):  # every delivery, any order
        obj = stripe_on["subs"][second] if etype.startswith("customer.") else (
            {"id": f"in_{second}", "object": "invoice", "subscription": second} if etype == "invoice.paid" else
            {"id": "cs_2", "object": "checkout.session", "mode": "subscription", "client_reference_id": str(u["id"]), "subscription": second})
        assert _send(client, _event(etype, obj)).status_code == 200
    assert stripe_on["canceled"] == [second]
    assert [r["payment_intent"] for r in stripe_on["refunds"]] == [f"pi_in_{second}"] and stripe_on["refunds"][0]["reason"] == "duplicate"
    row = db.scalars(select(Subscription).where(Subscription.user_id == u["id"])).one()
    db.refresh(row)
    assert (row.subscription_id, row.status, row.plan) == (first, "active", "pro_monthly")
    assert db.scalars(select(UserNotification).where(UserNotification.dedupe_key == f"billing-duplicate:{second}")).one()
    st = client.get("/api/billing/status").json()
    assert st["plan"] == "pro" and st["billing"] == "$9.99/month"
    _out(client)


def test_resubscribing_while_past_due_replaces_the_failing_subscription(client, db, stripe_on):
    u = _signup(client, "replace")
    old = _go_pro(client, stripe_on, u)
    stripe_on["subs"][old]["status"] = "past_due"
    _send(client, _event("customer.subscription.updated", stripe_on["subs"][old]))
    new = f"sub_new_{u['id']}"
    stripe_on["subs"][new] = _sub(new, u["id"], price=YEARLY)
    _send(client, _event("customer.subscription.created", stripe_on["subs"][new]))
    assert stripe_on["canceled"] == [old] and not stripe_on["refunds"]
    _send(client, _event("customer.subscription.deleted", stripe_on["subs"][old]))  # the old one's end must not downgrade
    _send(client, _event("invoice.payment_failed", {"id": "in_old", "object": "invoice", "subscription": old}))
    st = client.get("/api/billing/status").json()
    assert (st["plan"], st["status"], st["billing"], st["payment_issue"]) == ("pro", "active", "$99/year", False)
    _out(client)


def test_one_members_payment_never_touches_another_account(client, db, stripe_on):
    a, b = _signup(client, "payer"), _signup(client, "bystander")
    sid = _go_pro(client, stripe_on, a)
    # a forged/mismatched checkout claiming B for A's subscription is not applied
    r = _send(client, _event("checkout.session.completed", {"id": "cs_evil", "object": "checkout.session", "mode": "subscription",
                                                           "client_reference_id": str(b["id"]), "subscription": sid}))  # fmt: skip
    assert r.status_code == 200
    assert db.scalars(select(Subscription).where(Subscription.user_id == b["id"])).first() is None
    _out(client)
    client.post("/api/auth/login", json={"identifier": b["identifier"], "password": b["password"]})
    st = client.get("/api/billing/status").json()
    assert st["plan"] == "free" and {k: v["limit"] for k, v in st["usage"].items()} == plans.LIMITS["free"]
    _out(client)
    client.post("/api/auth/login", json={"identifier": a["identifier"], "password": a["password"]})
    assert client.get("/api/billing/status").json()["plan"] == "pro"
    _out(client)


def test_switching_plans_in_checkout_expires_the_other_open_session(client, stripe_on):
    _signup(client, "switcher")
    first = client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).json()["url"]
    second = client.post("/api/billing/checkout", json={"plan": "pro_yearly"}).json()["url"]
    assert first != second and stripe_on["expired"] == ["cs_test_1"]
    assert stripe_on["sessions"]["cs_test_2"]["status"] == "open"
    _out(client)


def test_test_and_live_stripe_objects_are_never_mixed(client, stripe_on, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_live_x")  # live key, but the configured prices are test prices
    stripe_on["prices"] = {pid: _stripe_price(pid) | {"livemode": False} for pid in (MONTHLY, YEARLY)}
    _signup(client, "mixed")
    assert client.get("/api/billing/plans").json()["available"] is False
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).status_code == 503
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")  # and the reverse
    stripe_on["prices"] = {pid: _stripe_price(pid) | {"livemode": True} for pid in (MONTHLY, YEARLY)}
    assert client.get("/api/billing/plans").json()["available"] is False
    _out(client)


# ------------------------------------------------------------------ cancellation, downgrade, renewal


def test_cancel_at_period_end_then_downgrade_keeps_all_data(client, db, stripe_on):
    u = _signup(client, "cancel")
    sid = _go_pro(client, stripe_on, u)
    for sym in ("AAPL", "MSFT", "NVDA", "AMZN", "TSLA"):  # more than the Free capacity
        assert client.post("/api/me/holdings", json={"symbol": sym, "quantity": 1}).status_code == 201
    st = client.post("/api/billing/cancel").json()
    assert stripe_on["modified"][-1][0] == sid and stripe_on["modified"][-1][1]["cancel_at_period_end"] is True
    assert st["pro"] and st["cancel_at_period_end"] and st["access_until"] and st["renews_at"] is None and st["can_resume"]
    assert client.post("/api/billing/resume").json()["renews_at"]  # changed their mind
    client.post("/api/billing/cancel")
    # the period ends: Stripe ends the subscription
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="canceled", days=-1)
    _send(client, _event("customer.subscription.deleted", stripe_on["subs"][sid]))
    st = client.get("/api/billing/status").json()
    assert st["plan"] == "free" and st["usage"]["my_nexis_assets"] == st["usage"]["my_nexis_assets"] | {"used": 5, "limit": 3, "over_capacity": True}
    assert db.scalar(select(func.count(UserHolding.id)).where(UserHolding.user_id == u["id"])) == 5  # nothing deleted
    r = client.post("/api/me/holdings", json={"symbol": "META", "quantity": 1})
    assert r.status_code == 402 and r.json()["error"]["details"]["feature"] == "my_nexis_assets"
    assert client.post("/api/me/holdings", json={"symbol": "AAPL", "quantity": 2}).status_code == 201  # another lot: no new slot
    _out(client)


def test_renewal_moves_the_period_forward(client, db, stripe_on):
    u = _signup(client, "renew")
    sid = _go_pro(client, stripe_on, u)
    stripe_on["subs"][sid] = _sub(sid, u["id"], days=60)
    _send(client, _event("invoice.paid", {"id": "in_r", "object": "invoice", "parent": {"subscription_details": {"subscription": sid}}}))
    row = db.scalars(select(Subscription).where(Subscription.user_id == u["id"])).one()
    assert row.current_period_end > utcnow() + timedelta(days=59)
    _out(client)


def test_failed_payment_past_due_then_unpaid(client, db, stripe_on):
    u = _signup(client, "failed")
    sid = _go_pro(client, stripe_on, u)
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="past_due")
    _send(client, _event("invoice.payment_failed", {"id": "in_1", "object": "invoice", "subscription": sid}))
    st = client.get("/api/billing/status").json()
    assert st["pro"] is True and st["payment_issue"] is True  # kept while Stripe retries
    assert st["payment_message"] == "Your payment needs attention. Please update your payment method."
    note = db.scalars(select(UserNotification).where(UserNotification.user_id == u["id"], UserNotification.event_type == "billing")).one()
    assert "update your payment method" in note.body
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="unpaid")  # retries exhausted
    _send(client, _event("customer.subscription.updated", stripe_on["subs"][sid]))
    assert client.get("/api/billing/status").json()["plan"] == "free"
    stripe_on["subs"][sid] = _sub(sid, u["id"], status="active")  # card updated, invoice paid
    _send(client, _event("invoice.paid", {"id": "in_2", "object": "invoice", "subscription": sid}))
    st = client.get("/api/billing/status").json()
    assert st["pro"] and not st["payment_issue"]
    _out(client)


def test_expired_or_inactive_subscriptions_are_not_pro():
    assert not entitlements.sub_is_pro(Subscription(user_id=1, status="active", current_period_end=utcnow() - timedelta(days=5)))
    for status in ("incomplete", "incomplete_expired", "unpaid", "canceled", "paused"):
        assert not entitlements.sub_is_pro(Subscription(user_id=1, status=status, current_period_end=utcnow() + timedelta(days=5)))
    for status in ("active", "trialing", "past_due"):
        assert entitlements.sub_is_pro(Subscription(user_id=1, status=status, current_period_end=utcnow() + timedelta(days=5)))


# ------------------------------------------------------------------ usage limits


def _ask(client, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return client.post("/api/advisor/chat", json={"messages": [{"role": "user", "content": "Is Emaar cheap?"}]}, headers=headers or {})


def test_advisor_free_20_then_pro_600(client, db, stripe_on, monkeypatch):
    monkeypatch.setattr(advisor, "ask", lambda db, messages, language=None: {"answer": "ok"})
    monkeypatch.setattr(get_settings(), "advisor_requests_per_hour", 10_000)  # the separate per-IP burst guard
    u = _signup(client, "meter")
    assert [_ask(client).status_code for _ in range(20)] == [200] * 20
    r = _ask(client)
    err = r.json()["error"]
    assert r.status_code == 402 and err["code"] == "usage_limit" and err["message"] == "You've reached your Free AI Advisor limit."
    assert err["details"]["upgrade_text"] == "Upgrade to Nexis Pro for 600 AI Advisor messages/month."
    usage = client.get("/api/advisor/status").json()["usage"]
    assert (usage["used"], usage["limit"], usage["near_limit"], usage["at_limit"]) == (20, 20, True, True)
    _go_pro(client, stripe_on, u, price=YEARLY)
    assert _ask(client).status_code == 200
    assert client.get("/api/billing/status").json()["usage"]["advisor"]["limit"] == 600  # monthly, even on the yearly plan
    _out(client)


def test_usage_resets_each_calendar_month(client, db, stripe_on):
    u = db.get(User, _signup(client, "reset")["id"])
    start, _ = entitlements.month_bounds()
    db.add_all(UsageEvent(user_id=u.id, feature="advisor", created_at=start - timedelta(days=3)) for _ in range(20))  # last month
    db.add(UsageEvent(user_id=u.id, feature="advisor", created_at=start + timedelta(minutes=1)))
    db.commit()
    assert entitlements.used(db, u, "advisor") == 1 and entitlements.check_usage_limit(db, u, "advisor")["allowed"]
    _out(client)


def test_failed_requests_are_refunded(client, db, stripe_on, monkeypatch):
    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("model down")

    monkeypatch.setattr(advisor, "ask", boom)
    u = _signup(client, "refund")
    with pytest.raises(RuntimeError):  # the test client re-raises what is a 500 in production
        _ask(client)
    assert db.scalar(select(func.count(UsageEvent.id)).where(UsageEvent.user_id == u["id"])) == 0
    _out(client)


def test_parallel_requests_cannot_exceed_the_limit(client, db, stripe_on):
    u = db.get(User, _signup(client, "race")["id"])
    from app.db import session as db_session

    db.add_all(UsageEvent(user_id=u.id, feature="advisor", created_at=utcnow()) for _ in range(18))
    db.commit()
    results = []
    for _ in range(4):  # separate sessions, as separate tabs would be
        with db_session.SessionLocal() as s:
            try:
                entitlements.consume_usage(s, s.get(User, u.id), "advisor")
                results.append("ok")
            except entitlements.UsageLimitReached:
                results.append("limit")
    assert results == ["ok", "ok", "limit", "limit"] and entitlements.used(db, u, "advisor") == 20
    _out(client)


def test_visitors_get_3_a_day_then_a_free_account(client, monkeypatch):
    monkeypatch.setattr(advisor, "ask", lambda db, messages, language=None: {"answer": "ok"})
    _out(client)
    hdr = {"x-forwarded-for": f"198.51.100.{random.randint(1, 250)}"}
    assert [_ask(client, hdr).status_code for _ in range(3)] == [200, 200, 200]
    r = _ask(client, hdr)
    err = r.json()["error"]
    assert r.status_code == 401 and err["code"] == "sign_in_required" and err["message"] == "You've reached today's free AI Advisor limit."
    assert err["details"]["next"] == "Create a free Nexis account to continue with 20 AI Advisor messages per month."


def test_my_nexis_capacity_free_3_slots_free_up(client, db, stripe_on):
    _signup(client, "capacity")
    assert client.post("/api/me/holdings", json={"symbol": "AAPL", "quantity": 1}).status_code == 201
    assert client.put("/api/me/watchlist/MSFT").status_code == 200
    assert client.put("/api/me/watchlist/NVDA").status_code == 200
    r = client.put("/api/me/watchlist/AMZN")
    assert r.status_code == 402 and "Remove one to add another, or upgrade to Nexis Pro for 100 tracked assets." in r.json()["error"]["message"]
    assert client.post("/api/me/holdings", json={"symbol": "MSFT", "quantity": 1}).status_code == 201  # already tracked
    client.delete("/api/me/watchlist/NVDA")
    assert client.put("/api/me/watchlist/AMZN").status_code == 200  # the freed slot is reusable
    _out(client)


def test_pulse_creation_limits_free_and_pro(client, db, stripe_on):
    u = _signup(client, "poster")
    ids = []
    for i in range(3):
        r = client.post("/api/pulse/discussions", json={"title": f"A question about rates and banks number {i}"})
        assert r.status_code == 201
        ids.append(r.json()["id"])
    r = client.post("/api/pulse/discussions", json={"title": "A fourth question about rates and banks"})
    assert r.status_code == 402 and r.json()["error"]["details"]["upgrade_text"] == "Upgrade to Nexis Pro for 50 Pulse discussions/month."
    assert [client.post(f"/api/pulse/discussions/{ids[0]}/comments", json={"body": f"comment {i}"}).status_code for i in range(11)] == [201] * 10 + [402]
    assert client.get("/api/pulse/feed").status_code == 200 and client.get(f"/api/pulse/discussions/{ids[0]}").status_code == 200  # reading stays free
    _go_pro(client, stripe_on, u)
    assert client.post("/api/pulse/discussions", json={"title": "Now on Pro: another question about rates"}).status_code == 201
    assert client.post(f"/api/pulse/discussions/{ids[0]}/comments", json={"body": "comment on pro"}).status_code == 201
    st = client.get("/api/billing/status").json()["usage"]
    assert (st["pulse_discussions"]["used"], st["pulse_discussions"]["limit"], st["pulse_comments"]["used"], st["pulse_comments"]["limit"]) == (4, 50, 11, 100)
    _out(client)


def test_members_cannot_grant_themselves_pro_or_change_billing(client, db, stripe_on):
    u = _signup(client, "sneaky")
    for body in ({"plan": "pro"}, {"role": "admin"}, {"subscription_status": "active"}, {"customer_id": "cus_x"}):
        assert client.patch("/api/auth/me", json=body).status_code == 422
    assert client.post("/api/billing/cancel").status_code == 422  # nothing to cancel
    assert client.get("/api/billing/status").json()["plan"] == "free" and db.get(User, u["id"]).role == "member"
    _out(client)


def test_confirm_only_accepts_the_members_own_session(client, stripe_on):
    u = _signup(client, "confirm")
    sid = f"sub_conf_{u['id']}"
    stripe_on["subs"][sid] = _sub(sid, u["id"])
    stripe_on["sessions"]["cs_theirs_session"] = {"client_reference_id": "999999", "subscription": sid}
    stripe_on["sessions"]["cs_mine_session"] = {"client_reference_id": str(u["id"]), "subscription": sid}
    assert client.post("/api/billing/confirm", json={"session_id": "cs_theirs_session"}).status_code == 422
    assert client.get("/api/billing/status").json()["plan"] == "free"
    assert client.post("/api/billing/confirm", json={"session_id": "cs_mine_session"}).json()["plan"] == "pro"
    _out(client)


def test_deleting_an_account_cancels_its_subscription(client, stripe_on):
    u = _signup(client, "leaver")
    sid = _go_pro(client, stripe_on, u)
    assert client.post("/api/auth/me/delete", json={"password": u["password"]}).status_code == 204
    assert stripe_on["canceled"] == [sid]
    client.cookies.clear()


def test_test_keys_in_production_sell_only_to_testers(client, stripe_on, monkeypatch):
    s = get_settings()
    tester = _signup(client, "tester")  # accounts made before switching to production (Secure cookies need HTTPS)
    monkeypatch.setattr(s, "env", "production")
    assert client.get("/api/billing/plans").json()["available"] is False  # signed in, but not a named tester
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).status_code == 503
    monkeypatch.setattr(s, "billing_test_emails", f"someone@else.com, {tester['identifier'].upper()}")
    p = client.get("/api/billing/plans").json()
    assert p["available"] is True and p["test_mode"] is True
    assert client.post("/api/billing/checkout", json={"plan": "pro_monthly"}).status_code == 200
    _out(client)
    assert client.get("/api/billing/plans").json()["available"] is False  # visitors: not with test keys
    monkeypatch.setattr(s, "stripe_secret_key", "sk_live_x")
    assert client.get("/api/billing/plans").json()["available"] is True  # live keys: on sale to everyone


def test_shared_workspace_is_read_only_for_the_public(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_instance", True)
    _out(client)
    assert client.get("/api/portfolios").status_code == 200  # viewing stays open
    for method, path in (("delete", "/api/portfolios/1"), ("post", "/api/developer/api-keys"), ("post", "/api/developer/webhooks"),
                         ("post", "/api/backtests"), ("delete", "/api/experiments/1"), ("post", "/api/market-data/ingest"),
                         ("post", "/api/connections"), ("post", "/api/imports/samples/load")):  # fmt: skip
        r = client.request(method.upper(), path, json={})
        assert r.status_code == 403, (path, r.status_code)
    u = _signup(client, "member-not-admin")
    assert client.delete("/api/portfolios/1").status_code == 403  # members can't either
    db.get(User, u["id"]).role = "admin"
    db.commit()
    assert client.post("/api/developer/api-keys", json={}).status_code != 403  # the Nexis team can
    _out(client)
