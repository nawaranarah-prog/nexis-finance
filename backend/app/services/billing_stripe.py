"""Stripe implementation of Nexis Pro billing: Checkout, Billing, Customer Portal and webhooks.

* Checkout collects the card (and Apple Pay / Google Pay where Stripe offers them) — Nexis never sees card data.
  Payment methods come from the Stripe Dashboard's payment method settings, not from code.
* Subscription state is written only from Stripe: verified webhooks, or a server-side fetch from Stripe's API.
  Every event re-reads the subscription from Stripe before saving, so events arriving out of order or twice
  always leave the latest state.
* Each event id is recorded in ``billing_events``; a repeated delivery is acknowledged and ignored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import stripe
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError, ValidationFailed
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import BillingEvent, MarketCache, Subscription, User, UserNotification
from app.services import billing

log = get_logger(__name__)
PRICES_KEY = "billing:stripe-prices"
HANDLED = {
    "checkout.session.completed", "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
    "invoice.paid", "invoice.payment_failed",
}  # fmt: skip


class WebhookRejected(NexisError):
    status_code = 400
    code = "webhook_rejected"


def _key() -> str:
    key = get_settings().stripe_secret_key
    if not key:
        raise billing.BillingUnavailable("Billing isn't available right now.")
    return key


def _get(obj: Any, *path: str) -> Any:
    for p in path:
        if obj is None:
            return None
        obj = obj.get(p) if isinstance(obj, dict) else getattr(obj, p, None)
    return obj


def _ts(v: Any) -> datetime | None:
    return datetime.fromtimestamp(int(v), UTC).replace(tzinfo=None) if v else None


def _site() -> str:
    return get_settings().public_site_url.rstrip("/")


# ------------------------------------------------------------------ prices (shown on the pricing page)


def prices(db: Session) -> dict[str, dict[str, Any]]:
    """Amount, currency and interval of each configured price, read from Stripe (cached for an hour)."""
    row = db.get(MarketCache, PRICES_KEY)
    if row is not None and utcnow() - row.fetched_at < timedelta(hours=1):
        return row.payload.get("v", {})
    out: dict[str, dict[str, Any]] = {}
    for plan, pid in billing.price_ids().items():
        if not pid:
            continue
        try:
            p = stripe.Price.retrieve(pid, api_key=_key(), expand=["product"])
        except stripe.StripeError as exc:
            log.warning("stripe price %s unavailable: %s", plan, exc.__class__.__name__)
            continue
        rec = _get(p, "recurring") or {}
        out[plan] = {"amount": (_get(p, "unit_amount") or 0) / 100, "currency": str(_get(p, "currency") or "").upper(),
                     "interval": _get(rec, "interval"), "interval_count": _get(rec, "interval_count") or 1,
                     "product": _get(p, "product", "name")}  # fmt: skip
    if row is None:
        db.add(MarketCache(key=PRICES_KEY, payload={"v": out}, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = {"v": out}, utcnow()
    db.commit()
    return out


# ------------------------------------------------------------------ checkout and portal


def _row(db: Session, user: User) -> Subscription:
    sub = db.scalars(select(Subscription).where(Subscription.user_id == user.id)).first()
    if sub is None:
        sub = Subscription(user_id=user.id, provider="stripe")
        db.add(sub)
        db.flush()
    return sub


def _customer(db: Session, user: User) -> str:
    sub = _row(db, user)
    if not sub.customer_id:
        c = stripe.Customer.create(api_key=_key(), email=user.email or None, metadata={"nexis_user_id": str(user.id)},
                                   idempotency_key=f"nexis-customer-{user.id}")  # fmt: skip
        sub.customer_id = c["id"]
        db.commit()
    return sub.customer_id


def checkout_url(db: Session, user: User, plan: str) -> str:
    price = billing.price_ids()[plan]
    try:
        session = stripe.checkout.Session.create(
            api_key=_key(), mode="subscription", customer=_customer(db, user), client_reference_id=str(user.id),
            line_items=[{"price": price, "quantity": 1}],
            # payment methods (cards, Apple Pay, Google Pay) follow the Dashboard settings
            subscription_data={"metadata": {"nexis_user_id": str(user.id), "nexis_plan": plan}},
            metadata={"nexis_user_id": str(user.id), "nexis_plan": plan},
            success_url=f"{_site()}/pro/welcome?session_id={{CHECKOUT_SESSION_ID}}", cancel_url=f"{_site()}/pro?checkout=canceled",
            allow_promotion_codes=False, billing_address_collection="auto",
        )  # fmt: skip
    except stripe.StripeError as exc:
        log.warning("stripe checkout failed: %s", exc.__class__.__name__)
        raise billing.BillingUnavailable("We couldn't start checkout. Please try again in a moment.") from exc
    return session["url"]


def portal_url(sub: Subscription) -> str:
    try:
        s = stripe.billing_portal.Session.create(api_key=_key(), customer=sub.customer_id, return_url=f"{_site()}/settings#plan")
    except stripe.StripeError as exc:
        log.warning("stripe portal failed: %s", exc.__class__.__name__)
        raise billing.BillingUnavailable("We couldn't open billing management. Please try again in a moment.") from exc
    return s["url"]


def cancel_now(sub: Subscription) -> None:
    try:
        stripe.Subscription.cancel(sub.subscription_id, api_key=_key())
    except stripe.InvalidRequestError:
        return  # already gone at Stripe
    except stripe.StripeError as exc:
        raise billing.BillingUnavailable("We couldn't cancel your Nexis Pro subscription, so the account wasn't deleted. "
                                         "Cancel it in Manage subscription, then try again.") from exc  # fmt: skip
    sub.status = "canceled"


# ------------------------------------------------------------------ syncing from Stripe


def _plan_for(price_id: str | None) -> str | None:
    return next((k for k, v in billing.price_ids().items() if v and v == price_id), None)


def sync(db: Session, obj: Any, user_id: int | None = None) -> Subscription | None:
    """Save a Stripe subscription object onto the member's row (found by subscription, customer, or our metadata)."""
    sid, customer = _get(obj, "id"), _get(obj, "customer")
    customer = customer if isinstance(customer, str) else _get(customer, "id")
    meta_uid = _get(obj, "metadata", "nexis_user_id")
    row = (db.scalars(select(Subscription).where(Subscription.subscription_id == sid)).first()
           or (db.scalars(select(Subscription).where(Subscription.customer_id == customer)).first() if customer else None))  # fmt: skip
    if row is None:
        uid = user_id or (int(meta_uid) if meta_uid and str(meta_uid).isdigit() else None)
        if uid is None or db.get(User, uid) is None:
            log.warning("stripe subscription %s has no Nexis account", sid)
            return None
        row = db.scalars(select(Subscription).where(Subscription.user_id == uid)).first() or Subscription(user_id=uid, provider="stripe")
        db.add(row)
    status = _get(obj, "status")
    if row.subscription_id and row.subscription_id != sid and row.status in billing_live() and status not in billing_live():
        return row  # an older, dead subscription must not overwrite the member's current one
    item = (_get(obj, "items", "data") or [None])[0]
    price = _get(item, "price", "id")
    row.provider, row.subscription_id, row.customer_id = "stripe", sid, customer or row.customer_id
    row.price_id, row.plan, row.status = price, _plan_for(price) or row.plan, status
    # Stripe moved period dates onto subscription items in newer API versions; read either.
    row.current_period_start = _ts(_get(obj, "current_period_start") or _get(item, "current_period_start"))
    row.current_period_end = _ts(_get(obj, "current_period_end") or _get(item, "current_period_end"))
    row.cancel_at_period_end = bool(_get(obj, "cancel_at_period_end")) or bool(_get(obj, "cancel_at"))
    row.canceled_at = _ts(_get(obj, "canceled_at"))
    if status == "active":
        row.payment_failed_at = None
    return row


def billing_live() -> frozenset[str]:
    from app.services.entitlements import PRO_STATUSES

    return PRO_STATUSES


def _retrieve(sub_id: str) -> Any:
    return stripe.Subscription.retrieve(sub_id, api_key=_key())


def confirm_session(db: Session, user: User, session_id: str) -> None:
    """Fetch the Checkout Session from Stripe and, if it belongs to this member, sync its subscription."""
    if not session_id.startswith("cs_"):
        raise ValidationFailed("unknown checkout session")
    try:
        s = stripe.checkout.Session.retrieve(session_id, api_key=_key())
    except stripe.StripeError as exc:
        raise ValidationFailed("unknown checkout session") from exc
    if str(_get(s, "client_reference_id")) != str(user.id):
        raise ValidationFailed("unknown checkout session")  # someone else's session looks exactly like a missing one
    sub_id = _get(s, "subscription")
    if sub_id:
        sync(db, _retrieve(sub_id if isinstance(sub_id, str) else _get(sub_id, "id")), user.id)
        db.commit()


# ------------------------------------------------------------------ webhooks


def _invoice_subscription(inv: Any) -> str | None:
    sid = _get(inv, "subscription") or _get(inv, "parent", "subscription_details", "subscription")
    return sid if isinstance(sid, str) or sid is None else _get(sid, "id")


def _notify_failed(db: Session, row: Subscription, invoice_id: str) -> None:
    try:
        with db.begin_nested():
            db.add(UserNotification(
                user_id=row.user_id, category="important", event_type="billing", severity="high",
                title="We couldn't process your latest Nexis Pro payment",
                body="Update your payment method to keep Nexis Pro. Stripe will retry the payment automatically.",
                link="/settings#plan", dedupe_key=f"billing-failed:{invoice_id}"[:120], delivery="immediate",
            ))  # fmt: skip
    except IntegrityError:
        pass


def handle_webhook(db: Session, payload: bytes, signature: str | None) -> dict[str, Any]:
    secret = get_settings().stripe_webhook_secret
    if not secret or not signature:
        raise WebhookRejected("missing signature")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise WebhookRejected("invalid signature") from exc
    eid, etype = event["id"], event["type"]
    if db.scalars(select(BillingEvent.id).where(BillingEvent.event_id == eid)).first() is not None:
        return {"received": True, "duplicate": True}
    obj = event["data"]["object"]
    row: Subscription | None = None
    if etype == "checkout.session.completed":
        uid = _get(obj, "client_reference_id")
        sub_id = _get(obj, "subscription")
        if _get(obj, "mode") == "subscription" and sub_id and uid and str(uid).isdigit():
            row = sync(db, _retrieve(sub_id if isinstance(sub_id, str) else _get(sub_id, "id")), int(uid))
    elif etype.startswith("customer.subscription."):
        try:
            latest = _retrieve(obj["id"])  # the newest state, whatever order events arrive in
        except stripe.InvalidRequestError:
            latest = obj
        row = sync(db, latest)
    elif etype in ("invoice.paid", "invoice.payment_failed"):
        sid = _invoice_subscription(obj)
        if sid:
            row = sync(db, _retrieve(sid))
            if row is not None and etype == "invoice.payment_failed":
                row.payment_failed_at = utcnow()
                db.flush()
                _notify_failed(db, row, _get(obj, "id") or eid)
            elif row is not None:
                row.payment_failed_at = None
    db.add(BillingEvent(provider="stripe", event_id=eid, event_type=etype, user_id=row.user_id if row else None, received_at=utcnow()))
    try:
        db.commit()
    except IntegrityError:  # the same event processed concurrently: its state is already saved
        db.rollback()
        return {"received": True, "duplicate": True}
    return {"received": True, "handled": etype in HANDLED}

