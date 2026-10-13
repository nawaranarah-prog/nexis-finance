"""Stripe implementation of Nexis Plus / Pro billing: Checkout, Billing, Customer Portal, plan changes and webhooks.

* Checkout collects the card (and Apple Pay / Google Pay where Stripe offers them) — Nexis never sees card data.
  Payment methods come from the Stripe Dashboard's payment method settings, not from code.
* Subscription state is written only from Stripe: verified webhooks, or a server-side fetch from Stripe's API.
  Every event re-reads the subscription from Stripe before saving, so events arriving out of order or twice
  always leave the latest state.
* Each event id is recorded in ``billing_events``; a repeated delivery is acknowledged and ignored.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import stripe
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import plans
from app.core.config import get_settings
from app.core.errors import NexisError, ValidationFailed
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import BillingEvent, MarketCache, Subscription, User, UserNotification
from app.services import billing

log = get_logger(__name__)
PRICES_KEY = "billing:stripe-prices"
HANDLED = {
    "checkout.session.completed", "checkout.session.expired", "checkout.session.async_payment_failed",
    "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
    "customer.subscription.pending_update_applied", "customer.subscription.pending_update_expired",
    "invoice.paid", "invoice.payment_failed", "invoice.payment_action_required",
    "charge.refunded", "charge.dispute.created", "charge.dispute.closed",
}  # fmt: skip


class WebhookRejected(NexisError):
    status_code = 400
    code = "webhook_rejected"


def _key() -> str:
    key = get_settings().stripe_secret_key
    if not key:
        raise billing.BillingUnavailable("Billing isn't available right now.")
    return key


def live_mode() -> bool:
    return _key().startswith(("sk_live_", "rk_live_"))


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


def price_problems_by_plan(db: Session) -> dict[str, list[str]]:
    """The problems found for each plan key (see ``verify_prices``); a plan with problems is not sold."""
    out: dict[str, list[str]] = {}
    for msg in verify_prices(db):
        out.setdefault(msg.split(":", 1)[0], []).append(msg)
    return out


def verify_prices(db: Session) -> list[str]:
    """Check each configured Stripe price is exactly the plan in ``plans.PRICES`` (amount, currency, interval,
    recurring and active). Returns the problems found (empty = sellable). Cached for 10 minutes."""
    row = db.get(MarketCache, PRICES_KEY)
    ids = billing.price_ids()
    if (row is not None and utcnow() - row.fetched_at < timedelta(minutes=10) and row.payload.get("ids") == ids
            and row.payload.get("live") == live_mode()):  # fmt: skip
        return list(row.payload.get("problems", []))
    problems: list[str] = []
    for plan, want in plans.PRICES.items():
        pid = ids.get(plan)
        if not pid:
            if plan in billing.REQUIRED:
                problems.append(f"{plan}: no price id configured")
            continue  # an optional (yearly) plan without an id simply isn't on sale
        try:
            p = stripe.Price.retrieve(pid, api_key=_key())
        except stripe.StripeError as exc:
            problems.append(f"{plan}: price {pid} unavailable ({exc.__class__.__name__})")
            continue
        rec = _get(p, "recurring") or {}
        got = (_get(p, "unit_amount"), str(_get(p, "currency") or "").lower(), _get(rec, "interval"), _get(rec, "interval_count") or 1)
        if got != (want["amount_cents"], want["currency"], want["interval"], 1):
            problems.append(f"{plan}: Stripe price {pid} is {got}, expected {(want['amount_cents'], want['currency'], want['interval'], 1)}")
        if not _get(p, "active"):
            problems.append(f"{plan}: Stripe price {pid} is archived")
        if bool(_get(p, "livemode")) != live_mode():
            problems.append(f"{plan}: Stripe price {pid} is a {'live' if _get(p, 'livemode') else 'test'} price but the secret key is a "
                            f"{'live' if live_mode() else 'test'} key")
    for msg in problems:
        log.error("nexis pro is not sellable: %s", msg)
    payload = {"ids": ids, "live": live_mode(), "problems": problems}
    if row is None:
        db.add(MarketCache(key=PRICES_KEY, payload=payload, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = payload, utcnow()
    db.commit()
    return problems


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
    if sub.customer_id and live_mode() and sub.livemode is not True:
        # Live payments started: Sandbox ids don't exist in live mode and a test subscription never carries over.
        log.info("account %s: starting live billing; Sandbox customer and subscription are not reused", user.id)
        sub.customer_id = sub.subscription_id = sub.price_id = sub.plan = sub.status = None
        sub.current_period_start = sub.current_period_end = sub.canceled_at = sub.payment_failed_at = None
        sub.pending_plan = sub.pending_invoice_id = sub.pending_expires_at = None
        sub.cancel_at_period_end, sub.livemode = False, None
        db.flush()
    if not sub.customer_id:
        mode = "live" if live_mode() else "test"
        c = stripe.Customer.create(api_key=_key(), email=user.email or None, metadata={"nexis_user_id": str(user.id)},
                                   idempotency_key=f"nexis-customer-{mode}-{user.id}")  # fmt: skip
        sub.customer_id = c["id"]
        db.commit()
    return sub.customer_id


def _live_stripe_subscription(customer: str) -> Any | None:
    """A subscription Stripe still considers live for this customer (guards against paying twice)."""
    subs = stripe.Subscription.list(api_key=_key(), customer=customer, status="all", limit=20)
    for s in _get(subs, "data") or []:
        if _get(s, "status") in billing_live():
            return s
    return None


def _expire_open_sessions(customer: str) -> None:
    try:
        for s in _get(stripe.checkout.Session.list(api_key=_key(), customer=customer, status="open", limit=20), "data") or []:
            with contextlib.suppress(stripe.InvalidRequestError):  # completed or expired meanwhile (a completed one is caught as a duplicate)
                stripe.checkout.Session.expire(_get(s, "id"), api_key=_key())
    except stripe.StripeError as exc:
        log.warning("could not expire open checkout sessions: %s", exc.__class__.__name__)


def checkout_url(db: Session, user: User, plan: str) -> str:
    """One Stripe Checkout Session for one of the plans on sale — never a price the browser chose.

    Duplicate protection: if Stripe already has a live subscription for this customer (for example a webhook
    hasn't arrived yet), it's synced and the member is told they're already on Pro; and a still-open session for
    the same plan created in the last hour is reused instead of opening a second checkout.
    """
    price = billing.price_ids()[plan]
    customer = _customer(db, user)
    try:
        live = _live_stripe_subscription(customer)
    except stripe.StripeError as exc:
        raise billing.BillingUnavailable("We couldn't start checkout. Please try again in a moment.") from exc
    if live is not None:
        sync(db, live, user.id)
        db.commit()
        reconcile_quietly(db, user.id)
        raise billing.AlreadyPro("You already have a paid Nexis plan. To switch between Plus and Pro, use Change plan in Settings.")
    pending_key = f"billing:checkout:{user.id}"
    pending = db.get(MarketCache, pending_key)
    if pending is not None and pending.payload.get("plan") == plan and utcnow() - pending.fetched_at < timedelta(hours=1):
        try:
            open_session = stripe.checkout.Session.retrieve(pending.payload["id"], api_key=_key())
            if _get(open_session, "status") == "open" and _get(open_session, "url"):
                return _get(open_session, "url")
        except stripe.StripeError:
            pass
    _expire_open_sessions(customer)  # e.g. monthly open in one tab, yearly in another: only the newest can be paid
    try:
        session = stripe.checkout.Session.create(
            api_key=_key(), mode="subscription", customer=customer, client_reference_id=str(user.id),
            line_items=[{"price": price, "quantity": 1}],
            # payment methods (cards, Apple Pay, Google Pay) follow the Dashboard settings
            subscription_data={"metadata": {"nexis_user_id": str(user.id), "nexis_plan": plan}},
            metadata={"nexis_user_id": str(user.id), "nexis_plan": plan},
            success_url=f"{_site()}/pro/welcome?session_id={{CHECKOUT_SESSION_ID}}", cancel_url=f"{_site()}/pro?checkout=canceled",
            # Checkout Studio configuration (hosted page; stripe-python 16 / API 2026-09-30 names it "hosted_page")
            ui_mode="hosted_page", billing_address_collection="auto", phone_number_collection={"enabled": False},
            automatic_tax={"enabled": False}, allow_promotion_codes=False, payment_method_collection="always",
            submit_type="auto", integration_identifier="hosted_web_0001", origin_context="web",
        )  # fmt: skip
    except stripe.StripeError as exc:
        log.warning("stripe checkout failed: %s", exc.__class__.__name__)
        raise billing.BillingUnavailable("We couldn't start checkout. Please try again in a moment.") from exc
    if pending is None:
        db.add(MarketCache(key=pending_key, payload={"id": session["id"], "plan": plan}, fetched_at=utcnow()))
    else:
        pending.payload, pending.fetched_at = {"id": session["id"], "plan": plan}, utcnow()
    db.commit()
    return session["url"]


def set_cancel_at_period_end(db: Session, sub: Subscription, cancel: bool) -> None:
    """Cancel when the paid period ends (Pro continues until then), or undo it. Stripe's answer is what's saved."""
    try:
        updated = stripe.Subscription.modify(sub.subscription_id, api_key=_key(), cancel_at_period_end=cancel)
    except stripe.StripeError as exc:
        log.warning("stripe cancel_at_period_end failed: %s", exc.__class__.__name__)
        raise billing.BillingUnavailable("We couldn't update your subscription. Please try again, or use Manage billing.") from exc
    sync(db, updated, sub.user_id)
    db.commit()
    if cancel:
        from app.services import investments

        user = db.get(User, sub.user_id)
        if user is not None:
            investments.remind_before_downgrade(db, user)


def _money(minor: int) -> str:
    return plans.money_text(minor / 100)


def _preview_invoice(sub: Subscription, item_id: str, price: str, proration_date: int) -> Any:
    """Stripe's own calculation of what the change invoices today (the same parameters the change itself uses)."""
    return stripe.Invoice.create_preview(
        api_key=_key(), customer=sub.customer_id, subscription=sub.subscription_id,
        subscription_details={"items": [{"id": item_id, "price": price}], "proration_behavior": "always_invoice",
                              "proration_date": proration_date},
    )  # fmt: skip


def _item(sub: Subscription) -> Any:
    current = _retrieve(sub.subscription_id)
    item = (_get(current, "items", "data") or [None])[0]
    if item is None:
        raise billing.BillingUnavailable("We couldn't read your subscription. Please try again.")
    return current, item


def _describe(sub: Subscription, plan: str, inv: Any, proration_date: int) -> dict[str, Any]:
    want = plans.PRICES[plan]
    due, total = int(_get(inv, "amount_due") or 0), int(_get(inv, "total") or 0)
    credit = max(0, -total)
    ends = [int(e) for e in (_get(ln, "period", "end") for ln in _get(inv, "lines", "data") or []) if e]
    renews = _ts(max(ends)) if ends else sub.current_period_end
    cur_tier, new_tier = plans.tier_of(sub.plan) or "pro", want["tier"]
    interval_change = want["interval"] != billing.interval_of(sub.plan)
    kind = "upgrade" if plans.rank(new_tier) > plans.rank(cur_tier) else "downgrade" if plans.rank(new_tier) < plans.rank(cur_tier) else "billing_period"
    name = plans.NAMES[new_tier]
    when = f"{renews.day} {renews:%B %Y}" if renews else None
    if due > 0:
        summary = (f"You'll pay {_money(due)} now. {name} ({plans.price_label(plan)}) starts as soon as that payment succeeds; "
                   "until then your current plan stays exactly as it is.")
    else:
        summary = f"Nothing to pay today. {name} ({plans.price_label(plan)}) starts immediately."
    if credit:
        summary += f" The unused part of your current plan becomes a credit of {_money(credit)}, used on your next invoices (not refunded)."
    summary += f" Then {plans.price_label(plan)}, renewing on {when}." if when else ""
    if interval_change:
        summary += f" Your billing period changes to {'yearly (one upfront payment for 12 months)' if want['interval'] == 'year' else 'monthly'}."
    return {
        "plan": plan, "plan_name": name, "billing": plans.price_label(plan), "from_billing": plans.price_label(sub.plan),
        "kind": kind, "interval_change": interval_change, "currency": want["currency"].upper(),
        "amount_due": due / 100, "amount_due_minor": due, "credit": credit / 100,
        "renewal_amount": want["amount_cents"] / 100, "renews_at": renews.isoformat() + "Z" if renews else None,
        "effective": "after_payment" if due > 0 else "immediately",
        "proration_date": proration_date, "summary": summary,
    }  # fmt: skip


def preview_change(db: Session, sub: Subscription, plan: str) -> dict[str, Any]:
    """What this plan change costs and means, as Stripe calculates it. Changes nothing."""
    price = billing.price_ids()[plan]
    pd = int(utcnow().replace(tzinfo=UTC).timestamp())
    try:
        _, item = _item(sub)
        inv = _preview_invoice(sub, _get(item, "id"), price, pd)
    except stripe.StripeError as exc:
        log.warning("stripe change preview failed: %s", exc.__class__.__name__)
        raise billing.BillingUnavailable("We couldn't work out the cost of this change. Please try again in a moment.") from exc
    return _describe(sub, plan, inv, pd)


def change_plan(db: Session, sub: Subscription, plan: str, expected_amount: int, proration_date: int) -> dict[str, Any]:
    """Apply a plan change the member reviewed, as a Stripe *pending update*.

    The amount due today is recalculated by Stripe with the same proration date; if it differs from what the member
    confirmed, nothing changes. ``payment_behavior="pending_if_incomplete"`` means Stripe switches the price only once
    the invoice is paid: a declined card or a 3-D Secure check leaves the current plan untouched, and the member pays
    on Stripe's hosted invoice page. Nothing here grants anything: the plan is read back from Stripe (``sync``).
    """
    price = billing.price_ids()[plan]
    now = int(utcnow().replace(tzinfo=UTC).timestamp())
    if not 0 <= now - int(proration_date) <= 30 * 60:
        raise billing.ChangeNeedsReview("This quote has expired. Please review the change again.")
    try:
        _, item = _item(sub)
        fresh = _describe(sub, plan, _preview_invoice(sub, _get(item, "id"), price, int(proration_date)), int(proration_date))
    except stripe.StripeError as exc:
        log.warning("stripe change preview failed: %s", exc.__class__.__name__)
        raise billing.BillingUnavailable("We couldn't change your plan. Please try again in a moment.") from exc
    if fresh["amount_due_minor"] != int(expected_amount):
        raise billing.ChangeNeedsReview("The amount for this change has changed. Please review it again.", details={"preview": fresh})
    try:
        updated = stripe.Subscription.modify(
            sub.subscription_id, api_key=_key(), items=[{"id": _get(item, "id"), "price": price}],
            proration_behavior="always_invoice", proration_date=int(proration_date), payment_behavior="pending_if_incomplete",
            expand=["latest_invoice"], idempotency_key=f"nexis-plan-{sub.subscription_id}-{plan}-{int(proration_date)}",
        )  # fmt: skip
    except stripe.CardError as exc:
        raise billing.PaymentFailed("Your card was declined, so your plan wasn't changed. Update your payment method and try again.") from exc
    except stripe.StripeError as exc:
        log.warning("stripe plan change failed: %s", exc.__class__.__name__)
        raise billing.BillingUnavailable("We couldn't change your plan. Please try again in a moment.") from exc
    if _get(updated, "pending_update"):
        inv = _get(updated, "latest_invoice")
        url = _get(inv, "hosted_invoice_url") if not isinstance(inv, str) else None
        sync(db, updated, sub.user_id)
        db.commit()
        return {"result": "payment_required", "pay_url": url,
                "message": f"Your payment of {_money(fresh['amount_due_minor'])} needs your attention. {fresh['plan_name']} starts once it's "
                           "paid; until then your current plan stays as it is."}  # fmt: skip
    try:
        updated = stripe.Subscription.modify(sub.subscription_id, api_key=_key(), metadata={"nexis_user_id": str(sub.user_id), "nexis_plan": plan})
    except stripe.StripeError as exc:  # the plan already changed and was paid; only the bookkeeping failed
        log.error("plan changed but follow-up update failed for %s: %s", sub.subscription_id, exc.__class__.__name__)
    sync(db, updated, sub.user_id)
    db.commit()
    reconcile_quietly(db, sub.user_id)
    return {"result": "changed", "message": f"You're now on {fresh['plan_name']} ({fresh['billing']})."}


def pending_invoice_url(sub: Subscription) -> str:
    """Stripe's hosted page for the invoice a pending plan change waits on."""
    try:
        inv = stripe.Invoice.retrieve(sub.pending_invoice_id, api_key=_key())
    except stripe.StripeError as exc:
        raise billing.BillingUnavailable("We couldn't open the payment page. Please try again in a moment.") from exc
    if _get(inv, "status") != "open" or not _get(inv, "hosted_invoice_url"):
        raise ValidationFailed("This payment is no longer open. Refresh the page to see your current plan.")
    return _get(inv, "hosted_invoice_url")


def cancel_pending(db: Session, sub: Subscription) -> None:
    """Void the unpaid invoice of a pending plan change, so it can never be paid and the change never applies. The
    current plan continues unchanged; nothing is charged."""
    inv_id = sub.pending_invoice_id
    try:
        stripe.Invoice.void_invoice(inv_id, api_key=_key())
    except stripe.InvalidRequestError:
        inv = stripe.Invoice.retrieve(inv_id, api_key=_key())
        if _get(inv, "status") == "paid":
            raise ValidationFailed("That payment already went through, so the change has been applied.") from None
    except stripe.StripeError as exc:
        raise billing.BillingUnavailable("We couldn't cancel the change. Please try again in a moment.") from exc
    sub.pending_plan, sub.pending_expires_at, sub.pending_invoice_id = None, None, f"void:{inv_id}"
    db.commit()


def reconcile_quietly(db: Session, user_id: int | None) -> None:
    """Apply the plan the provider confirmed to the member's investments. Never fails the caller."""
    if user_id is None:
        return
    from app.services import investments

    try:
        user = db.get(User, user_id)
        if user is not None:
            investments.reconcile(db, user)
    except Exception as exc:
        db.rollback()
        log.error("investment reconcile failed for account %s: %s", user_id, exc.__class__.__name__)


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
        raise billing.BillingUnavailable("We couldn't cancel your Nexis subscription, so the account wasn't deleted. "
                                         "Cancel it in Manage subscription, then try again.") from exc  # fmt: skip
    sub.status = "canceled"


# ------------------------------------------------------------------ syncing from Stripe


def _plan_for(price_id: str | None) -> str | None:
    hit = next((k for k, v in billing.price_ids().items() if v and v == price_id), None)
    if hit:
        return hit
    return "pro_legacy" if price_id and price_id in billing.legacy_price_ids() else None


def _existing_terms(price_id: str | None, current: str | None, obj: Any) -> str | None:
    """A subscription on a price Nexis no longer sells (e.g. an earlier annual amount) keeps its tier and its price —
    it's never repriced — and is labelled as an existing price rather than shown with today's amount."""
    if not price_id:
        return current
    tier = plans.tier_of(current) or plans.tier_of(_get(obj, "metadata", "nexis_plan"))
    return f"{tier}_legacy" if tier else current


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
    elif user_id is not None and row.user_id != user_id:
        # The checkout says one account, the Stripe customer/subscription belongs to another: never grant Pro on a guess.
        log.error("stripe subscription %s: checkout account %s but customer belongs to account %s — not applied", sid, user_id, row.user_id)
        return None
    status = _get(obj, "status")
    if row.subscription_id and row.subscription_id != sid and row.status in billing_live() and status not in billing_live():
        return row  # an older, dead subscription must not overwrite the member's current one
    if row.subscription_id and row.subscription_id != sid and status in billing_live() and row.status in ("active", "trialing"):
        # A second live subscription for an account that already has a working one (e.g. two checkouts paid in two
        # tabs). Keep the first, cancel the second and refund it, so nobody pays twice for the same thing.
        _cancel_duplicate(db, row, obj)
        return row
    if row.subscription_id and row.subscription_id != sid and status in billing_live() and row.status in billing_live():
        # The member re-subscribed while the old one was failing (past_due): the new one replaces it; stop the old one
        # so Stripe doesn't keep retrying a card for a subscription they no longer use.
        _cancel_quietly(row.subscription_id)
    item = (_get(obj, "items", "data") or [None])[0]
    price = _get(item, "price", "id")
    row.provider, row.subscription_id, row.customer_id = "stripe", sid, customer or row.customer_id
    row.price_id, row.plan, row.status = price, _plan_for(price) or _existing_terms(price, row.plan, obj), status
    # Stripe moved period dates onto subscription items in newer API versions; read either.
    row.current_period_start = _ts(_get(obj, "current_period_start") or _get(item, "current_period_start"))
    row.current_period_end = _ts(_get(obj, "current_period_end") or _get(item, "current_period_end"))
    row.cancel_at_period_end = bool(_get(obj, "cancel_at_period_end")) or bool(_get(obj, "cancel_at"))
    row.canceled_at = _ts(_get(obj, "canceled_at"))
    if _get(obj, "livemode") is not None:
        row.livemode = bool(_get(obj, "livemode"))
    # A plan change waiting for payment: recorded so the member can pay or cancel it; it grants nothing (the price
    # above is still the current one until Stripe applies the update).
    pu = _get(obj, "pending_update")
    if pu:
        pitem = (_get(pu, "subscription_items") or [None])[0]
        pprice = _get(pitem, "price")
        pprice = pprice if isinstance(pprice, str) else _get(pprice, "id")
        inv = _get(obj, "latest_invoice")
        inv_id = inv if isinstance(inv, str) or inv is None else _get(inv, "id")
        if row.pending_invoice_id != f"void:{inv_id}":  # a change the member canceled stays canceled
            row.pending_plan, row.pending_invoice_id, row.pending_expires_at = _plan_for(pprice), inv_id, _ts(_get(pu, "expires_at"))
    else:
        row.pending_plan, row.pending_expires_at = None, None
        if not (row.pending_invoice_id or "").startswith("void:"):
            row.pending_invoice_id = None
    if status == "active":
        row.payment_failed_at = None
    return row


def _cancel_quietly(sub_id: str) -> None:
    try:
        stripe.Subscription.cancel(sub_id, api_key=_key())
    except stripe.StripeError as exc:
        log.error("could not cancel superseded subscription %s: %s", sub_id, exc.__class__.__name__)


def _refund_latest_payment(obj: Any) -> bool:
    """Refund what the subscription's latest invoice collected. Works with old and new Stripe API shapes."""
    inv_id = _get(obj, "latest_invoice")
    inv_id = inv_id if isinstance(inv_id, str) or inv_id is None else _get(inv_id, "id")
    if not inv_id:
        return False
    inv = stripe.Invoice.retrieve(inv_id, api_key=_key())
    if not _get(inv, "amount_paid"):
        return True  # nothing was collected
    pi = _get(inv, "payment_intent")
    if not pi:
        payments = stripe.InvoicePayment.list(api_key=_key(), invoice=inv_id, limit=5)
        pi = next((_get(p, "payment", "payment_intent") for p in _get(payments, "data") or [] if _get(p, "status") == "paid"), None)
    pi = pi if isinstance(pi, str) or pi is None else _get(pi, "id")
    if not pi:
        return False
    stripe.Refund.create(api_key=_key(), payment_intent=pi, reason="duplicate", idempotency_key=f"nexis-dup-{inv_id}")
    return True


def _cancel_duplicate(db: Session, row: Subscription, obj: Any) -> None:
    sid = _get(obj, "id")
    refunded = False
    try:
        try:
            stripe.Subscription.cancel(sid, api_key=_key())
        except stripe.InvalidRequestError:
            if _get(_retrieve(sid), "status") != "canceled":  # already canceled by a concurrent delivery is fine
                raise
        refunded = _refund_latest_payment(obj)
    except stripe.StripeError as exc:
        log.error("duplicate subscription %s for account %s: cancel/refund failed: %s", sid, row.user_id, exc.__class__.__name__)
        raise  # the webhook fails and Stripe retries, rather than silently leaving a double charge
    log.warning("duplicate subscription %s for account %s canceled (refunded=%s); kept %s", sid, row.user_id, refunded, row.subscription_id)
    if not refunded:
        log.error("duplicate subscription %s: payment NOT refunded automatically — refund it in the Stripe Dashboard", sid)
    try:
        with db.begin_nested():
            db.add(UserNotification(
                user_id=row.user_id, category="important", event_type="billing", severity="notable",
                title="You were only charged once",
                body="A second Nexis checkout was completed for your account. We canceled it" + (" and refunded it" if refunded else "")
                     + ". Your existing subscription is unchanged.",
                link="/settings#plan", dedupe_key=f"billing-duplicate:{sid}"[:120], delivery="immediate",
            ))  # fmt: skip
    except IntegrityError:
        pass


def billing_live() -> frozenset[str]:
    from app.services.entitlements import PAID_STATUSES

    return PAID_STATUSES


def _retrieve(sub_id: str) -> Any:
    return stripe.Subscription.retrieve(sub_id, api_key=_key())


def confirm_session(db: Session, user: User, session_id: str) -> dict[str, Any]:
    """Fetch the Checkout Session from Stripe and, if it belongs to this member, sync its subscription. Returns what
    Stripe says about the checkout, so the page can tell "paid", "processing" and "not completed" apart."""
    if not session_id.startswith("cs_"):
        raise ValidationFailed("unknown checkout session")
    try:
        s = stripe.checkout.Session.retrieve(session_id, api_key=_key())
    except stripe.StripeError as exc:
        raise ValidationFailed("unknown checkout session") from exc
    if str(_get(s, "client_reference_id")) != str(user.id):
        raise ValidationFailed("unknown checkout session")  # someone else's session looks exactly like a missing one
    sub_id = _get(s, "subscription")
    sub_status = None
    if sub_id:
        latest = _retrieve(sub_id if isinstance(sub_id, str) else _get(sub_id, "id"))
        sub_status = _get(latest, "status")
        sync(db, latest, user.id)
        db.commit()
    return {"status": _get(s, "status"), "payment_status": _get(s, "payment_status"), "subscription_status": sub_status}


# ------------------------------------------------------------------ webhooks


def _invoice_subscription(inv: Any) -> str | None:
    sid = _get(inv, "subscription") or _get(inv, "parent", "subscription_details", "subscription")
    return sid if isinstance(sid, str) or sid is None else _get(sid, "id")


def _notify_failed(db: Session, row: Subscription, invoice_id: str) -> None:
    try:
        with db.begin_nested():
            db.add(UserNotification(
                user_id=row.user_id, category="important", event_type="billing", severity="high",
                title="Your payment needs attention",
                body="Your payment needs attention. Please update your payment method to keep your Nexis plan.",
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
    extra: dict[str, Any] = {}
    if bool(_get(event, "livemode")) != live_mode():
        # A test event at a live endpoint (or the reverse): it never touches a member's plan.
        log.error("stripe %s event %s ignored: its mode doesn't match the API key", "live" if _get(event, "livemode") else "test", eid)
        db.add(BillingEvent(provider="stripe", event_id=eid, event_type=etype, received_at=utcnow(), status="ignored",
                            details={"object": _get(obj, "id"), "reason": "mode_mismatch"}))  # fmt: skip
        with contextlib.suppress(IntegrityError):
            db.commit()
        return {"received": True, "handled": False}
    if etype in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        uid = _get(obj, "client_reference_id")
        if uid and str(uid).isdigit():
            row = db.scalars(select(Subscription).where(Subscription.user_id == int(uid))).first()
            pending = db.get(MarketCache, f"billing:checkout:{uid}")
            if pending is not None and pending.payload.get("id") == _get(obj, "id"):
                db.delete(pending)  # an abandoned checkout is never reused; nothing was granted
    elif etype.startswith("charge."):
        # Refunds and disputes don't change a plan by themselves: access follows the subscription. The owner decides
        # in Stripe (cancelling the subscription there moves the member to Free through customer.subscription.deleted).
        charge = obj if etype == "charge.refunded" else None
        cust = _get(obj, "customer")
        if cust is None and charge is None and _get(obj, "charge"):
            with contextlib.suppress(stripe.StripeError):
                cust = _get(stripe.Charge.retrieve(_get(obj, "charge"), api_key=_key()), "customer")
        cust = cust if isinstance(cust, str) else _get(cust, "id")
        row = db.scalars(select(Subscription).where(Subscription.customer_id == cust)).first() if cust else None
        extra = {"amount": _get(obj, "amount"), "amount_refunded": _get(obj, "amount_refunded"), "status": _get(obj, "status")}
        log.warning("stripe %s for account %s (customer %s): review it in the Stripe Dashboard", etype, row.user_id if row else None, cust)
    elif etype == "checkout.session.completed":
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
    elif etype in ("invoice.paid", "invoice.payment_failed", "invoice.payment_action_required"):
        sid = _invoice_subscription(obj)
        if sid:
            row = sync(db, _retrieve(sid))
            if row is not None and row.subscription_id != sid:
                pass  # an invoice of a superseded or duplicate subscription says nothing about the member's current one
            elif row is not None and etype == "invoice.payment_action_required":
                _notify_failed(db, row, _get(obj, "id") or eid)
            elif row is not None and _get(obj, "billing_reason") == "subscription_update" and etype == "invoice.payment_failed":
                pass  # a plan change's payment failed: the change simply doesn't happen (pending update); the plan is fine
            elif row is not None and etype == "invoice.payment_failed":
                row.payment_failed_at = utcnow()
                db.flush()
                _notify_failed(db, row, _get(obj, "id") or eid)
            elif row is not None:
                row.payment_failed_at = None
    details = {"object": _get(obj, "id"), "customer": _get(obj, "customer") if isinstance(_get(obj, "customer"), str) else None,
               "subscription": row.subscription_id if row else None, **{k: v for k, v in extra.items() if v is not None}}  # fmt: skip
    db.add(BillingEvent(provider="stripe", event_id=eid, event_type=etype, user_id=row.user_id if row else None, received_at=utcnow(),
                        status="processed" if (etype in HANDLED and row is not None) else "ignored", details=details))  # fmt: skip
    try:
        db.commit()
    except IntegrityError:  # the same event processed concurrently: its state is already saved
        db.rollback()
        return {"received": True, "duplicate": True}
    if row is not None:
        reconcile_quietly(db, row.user_id)
    return {"received": True, "handled": etype in HANDLED}

