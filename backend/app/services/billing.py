"""Nexis billing (Free, Nexis Plus, Nexis Pro), independent of the payment provider.

Routes call these functions; ``billing_stripe`` implements them for Stripe (Checkout, Billing, Customer Portal,
webhooks). Another provider would add its own module and its own ``subscriptions.provider`` value; plans
(``app/core/plans.py``), entitlements and usage stay the same.

Nothing here trusts the browser: a member can only buy the plans in ``plans.PRICES``, sold at the Stripe prices
configured on the server — and only if Stripe confirms each price is exactly the amount, currency and interval in
``plans.PRICES`` (the single source of the prices). Paid access comes only from subscription state Stripe reported
(``entitlements.tier_of``).

* New subscriptions: a server-created Stripe Checkout Session; the plan is granted from Stripe's subscription, never
  from the browser or the success URL.
* Plan changes (tier or billing period): ``preview_change`` asks Stripe what the change costs today; the member
  confirms that exact amount; ``change_plan`` applies it as a Stripe *pending update*, so the new plan starts only
  once that payment succeeds. If Stripe needs the member (card declined, 3-D Secure), they pay on Stripe's hosted
  invoice page and nothing changes until then.
* Live payments need ``NEXIS_BILLING_LIVE_ENABLED=true`` as well as a live key.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core import plans
from app.core.config import get_settings
from app.core.errors import NexisError, ValidationFailed
from app.db.base import utcnow
from app.models import User
from app.services import entitlements

CANCELLATION = ("Cancel any time. You keep your plan until the end of the period you've paid for (the month, or the year for "
                "annual plans), then move to Free. Annual plans are paid upfront and aren't refunded when you cancel. Before then "
                "you choose which investment stays active on Free; the others are archived, never deleted, and come back if "
                "you subscribe again. Pulse and your account are unaffected.")  # fmt: skip


class BillingUnavailable(NexisError):
    status_code = 503
    code = "billing_unavailable"


class AlreadyPro(NexisError):
    status_code = 409
    code = "already_pro"


class PaymentFailed(NexisError):
    status_code = 402
    code = "payment_failed"


class ChangeNeedsReview(NexisError):
    """The amount the member confirmed is no longer what Stripe would charge (time passed, a credit was used)."""

    status_code = 409
    code = "change_needs_review"


def price_ids() -> dict[str, str | None]:
    """Stripe price id for every plan on sale (``plans.PRICES``); yearly ids only matter once a yearly plan exists."""
    s = get_settings()
    known = {"plus_monthly": s.stripe_plus_price_id, "pro_monthly": s.stripe_pro_price_id,
             "plus_yearly": s.stripe_plus_yearly_price_id, "pro_yearly": s.stripe_pro_yearly_aed_price_id}  # fmt: skip
    return {k: known.get(k) for k in plans.PRICES}


def legacy_price_ids() -> set[str]:
    s = get_settings()
    return {p for p in (s.stripe_pro_monthly_price_id, s.stripe_pro_yearly_price_id) if p}


REQUIRED = ("plus_monthly", "pro_monthly")  # the plans that must exist for anything to be sold


def configured() -> bool:
    """Keys and the monthly price ids are set. Yearly plans are optional: they're sold only once configured and verified."""
    s = get_settings()
    ids = price_ids()
    return bool(s.stripe_secret_key and s.stripe_webhook_secret and all(ids.get(k) for k in REQUIRED))


def on_sale(db: Session) -> list[str]:
    """Plan keys that can be bought right now: configured in Stripe and verified to match ``plans.PRICES``."""
    from app.services import billing_stripe

    if not configured():
        return []
    bad = billing_stripe.price_problems_by_plan(db)
    return [k for k, pid in price_ids().items() if pid and k not in bad]


def interval_of(price_key: str | None) -> str | None:
    return (plans.PRICES.get(price_key or "") or {}).get("interval")


def test_mode() -> bool:
    return bool((get_settings().stripe_secret_key or "").startswith(("sk_test_", "rk_test_")))


def live_blocked() -> bool:
    """A live key without the owner's explicit approval (NEXIS_BILLING_LIVE_ENABLED): no real-money actions."""
    return entitlements.live_key() and not get_settings().billing_live_enabled


def _tester(user: User | None) -> bool:
    return entitlements.is_billing_tester(user)


def _sellable(db: Session, user: User | None = None) -> tuple[bool, str | None]:
    if not configured():
        return False, "Paid plans aren't available to buy yet."
    if live_blocked():
        return False, "Paid plans aren't available to buy yet."  # live key deployed, live launch not approved
    if test_mode() and get_settings().env == "production" and not _tester(user):
        return False, "Paid plans aren't available to buy yet."  # test keys on the public site: testers only
    from app.services import billing_stripe

    problems = billing_stripe.price_problems_by_plan(db)
    if any(k in problems for k in REQUIRED):  # a problem with an optional (yearly) plan only takes that plan off sale
        return False, "Paid plans are temporarily unavailable."  # the details are logged, never shown to members
    return True, None


def payment_methods() -> list[str]:
    wallets = [w.strip() for w in get_settings().billing_wallets.split(",") if w.strip()]
    return ["Card (Visa, Mastercard and other major cards)", *wallets]


def catalog(db: Session, user: User | None = None) -> dict[str, Any]:
    """Plans, limits and prices from ``plans.py`` — one source for the pricing page, Settings and checkout. Only plans
    that are actually on sale are listed (a yearly plan without a verified Stripe price is left out)."""
    sellable, reason = _sellable(db, user)
    pub = plans.public()
    sale = set(on_sale(db)) if sellable else set(REQUIRED)
    for t in pub["tiers"]:
        t["prices"] = {i: p for i, p in (t.get("prices") or {}).items() if p["key"] in sale}
    pub["prices"] = {k: v for k, v in pub["prices"].items() if k in sale}
    pub["intervals"] = [i for i in plans.intervals() if all(i in t["prices"] for t in pub["tiers"] if t["id"] in plans.PAID_TIERS)]
    return {**pub, "available": sellable, "unavailable_reason": reason, "test_mode": sellable and test_mode(),
            "payment_methods": payment_methods(), "billing_interval": "monthly" if pub["intervals"] == ["month"] else "monthly or yearly",
            "cancellation": CANCELLATION}  # fmt: skip


def _iso(d: Any) -> str | None:
    return d.isoformat() + "Z" if d else None


def _pending(sub: Any, paid: bool) -> dict[str, Any] | None:
    if not (paid and sub and sub.pending_plan and sub.pending_plan in plans.PRICES):
        return None
    if sub.pending_expires_at is not None and sub.pending_expires_at < utcnow():
        return None  # Stripe discards a pending update it didn't get paid for
    tier = plans.PRICES[sub.pending_plan]["tier"]
    return {"plan": sub.pending_plan, "plan_name": plans.NAMES[tier], "billing": plans.price_label(sub.pending_plan),
            "expires_at": _iso(sub.pending_expires_at)}  # fmt: skip


def status(db: Session, user: User) -> dict[str, Any]:
    sub = entitlements.subscription(db, user)
    tier = entitlements.sub_tier(sub, user=user)
    paid = tier != "free"
    iso = _iso
    issue = bool(sub and sub.status in ("past_due", "unpaid"))
    pending = _pending(sub, paid)
    from app.services import investments

    return {
        "plan": tier,
        "plan_name": plans.NAMES[tier],
        "paid": paid,
        "pro": tier == "pro",
        "status": sub.status if sub else None,
        "billing": plans.price_label(sub.plan) if paid and sub else None,
        "price_key": sub.plan if paid and sub else None,
        "interval": interval_of(sub.plan) if paid and sub else None,
        "billing_plan": sub.plan if paid and sub else None,
        "renews_at": iso(sub.current_period_end) if paid and sub and not sub.cancel_at_period_end else None,
        "access_until": iso(sub.current_period_end) if paid and sub and sub.cancel_at_period_end else None,
        "cancel_at_period_end": bool(paid and sub and sub.cancel_at_period_end),
        "payment_issue": issue,
        "payment_message": "Your payment needs attention. Please update your payment method." if issue else None,
        "can_manage": bool(sub and sub.customer_id),
        "can_cancel": bool(paid and sub and sub.subscription_id and not sub.cancel_at_period_end),
        "can_resume": bool(paid and sub and sub.subscription_id and sub.cancel_at_period_end),
        "can_change_plan": bool(paid and sub and sub.subscription_id and not issue and not pending and not sub.cancel_at_period_end),
        "pending_change": pending,
        # A Stripe Sandbox subscription: no real money moved (shown so testers never mistake it for a real purchase).
        "test_subscription": bool(paid and sub and sub.livemode is not True),
        "investments": investments.state(db, user),
        **entitlements.get_user_entitlements(db, user),
        "billing_available": configured(),
    }


def checkout(db: Session, user: User, plan: str) -> dict[str, str]:
    if plan not in plans.PRICES:
        raise ValidationFailed("unknown plan")
    if entitlements.has_paid(db, user):
        raise AlreadyPro("You already have a paid Nexis plan. To switch between Plus and Pro, use Change plan in Settings.")
    sellable, reason = _sellable(db, user)
    if not sellable:
        raise BillingUnavailable(reason or "Paid plans aren't available to buy yet.")
    if plan not in on_sale(db):
        raise ValidationFailed("That plan isn't available right now.")
    from app.services import billing_stripe

    return {"url": billing_stripe.checkout_url(db, user, plan)}


def _changeable(db: Session, user: User, plan: str) -> Any:
    """The member's live subscription, if this plan change is allowed at all (server-side checks only)."""
    if plan not in plans.PRICES:
        raise ValidationFailed("unknown plan")
    sub = entitlements.subscription(db, user)
    tier = entitlements.sub_tier(sub, user=user)
    if tier == "free" or sub is None or not sub.subscription_id:
        raise ValidationFailed("You don't have a paid plan to change. Choose a plan on the pricing page.")
    if sub.status in ("past_due", "unpaid"):
        raise ValidationFailed("Please update your payment method before changing plans.")
    if _pending(sub, True):
        raise ValidationFailed("A plan change is waiting for payment. Complete or cancel it in Settings first.")
    if sub.cancel_at_period_end:  # never undo a cancellation as a side effect: the member keeps the subscription first
        raise ValidationFailed("Your subscription is set to end. Choose \"Keep my subscription\" first, then change your plan.")
    if plan == sub.plan:
        raise ValidationFailed(f"You're already on {plans.NAMES[tier]} ({plans.price_label(plan)}).")
    sellable, reason = _sellable(db, user)
    if not sellable:
        raise BillingUnavailable(reason or "Plan changes aren't available right now.")
    if plan not in on_sale(db):
        raise ValidationFailed("That plan isn't available right now.")
    return sub


def preview_change(db: Session, user: User, plan: str) -> dict[str, Any]:
    """What a plan change means, computed by Stripe for this subscription: the amount charged today, any credit, the
    next renewal (amount and date), and when the change takes effect. Nothing is changed."""
    sub = _changeable(db, user, plan)
    from app.services import billing_stripe

    return billing_stripe.preview_change(db, sub, plan)


def change_plan(db: Session, user: User, plan: str, keep: list[str] | None = None, expected_amount: int | None = None,
                proration_date: int | None = None) -> dict[str, Any]:  # fmt: skip
    """Apply a reviewed plan change. ``expected_amount`` is the amount due today (in fils) the member saw in the preview
    and confirmed; if Stripe would now charge anything else, nothing changes and the member reviews again. The new plan
    starts only once Stripe has the payment (a pending update). Moving to a smaller allowance with more active
    investments than it allows needs the member's choice of which to keep first (never chosen for them)."""
    if expected_amount is None or proration_date is None:
        raise ValidationFailed("Review the change first: we show what it costs before anything changes.")
    sub = _changeable(db, user, plan)
    target = plans.PRICES[plan]["tier"]
    from app.services import billing_stripe, investments

    cap = entitlements.limit("investments", target)
    active = investments.active_symbols(db, user)
    if len(active) > cap:
        if not keep:
            raise investments.SelectionRequired(
                f"{plans.NAMES[target]} includes {cap} active investments and you have {len(active)}. Choose which {cap} to keep "
                "active — the others are archived, not deleted.",
                details={"limit": cap, "symbols": active, "plan": target},
            )
        clean = list(dict.fromkeys(keep))
        if not set(clean) <= set(active) or not 1 <= len(clean) <= cap:
            raise ValidationFailed(f"Choose between 1 and {cap} of your active investments to keep")
        sub.downgrade_keep = clean
        db.commit()
    result = billing_stripe.change_plan(db, sub, plan, expected_amount, proration_date)
    return {**status(db, user), "change": result}


def pending_payment_url(db: Session, user: User) -> dict[str, str]:
    """Stripe's hosted page for the invoice a pending plan change is waiting on."""
    sub = entitlements.subscription(db, user)
    if not _pending(sub, entitlements.sub_is_paid(sub, user=user)):
        raise ValidationFailed("There's no plan change waiting for payment.")
    if live_blocked():
        raise BillingUnavailable("Billing isn't available right now.")
    from app.services import billing_stripe

    return {"url": billing_stripe.pending_invoice_url(sub)}


def cancel_pending_change(db: Session, user: User) -> dict[str, Any]:
    """Drop a plan change that's waiting for payment: the current plan simply continues and nothing is charged."""
    sub = entitlements.subscription(db, user)
    if not _pending(sub, entitlements.sub_is_paid(sub, user=user)):
        raise ValidationFailed("There's no plan change waiting for payment.")
    from app.services import billing_stripe

    billing_stripe.cancel_pending(db, sub)
    return status(db, user)


def portal(db: Session, user: User) -> dict[str, str]:
    sub = entitlements.subscription(db, user)
    if not configured():
        raise BillingUnavailable("Billing isn't available right now.")
    if sub is None or not sub.customer_id:
        raise ValidationFailed("There's no billing account to manage yet.")
    from app.services import billing_stripe

    return {"url": billing_stripe.portal_url(sub)}


def set_cancel(db: Session, user: User, cancel: bool) -> dict[str, Any]:
    """Cancel at the end of the paid period (or undo that). The plan continues until the period ends."""
    sub = entitlements.subscription(db, user)
    if not entitlements.sub_is_paid(sub, user=user) or sub is None or not sub.subscription_id:
        raise ValidationFailed("There's no active Nexis subscription.")
    if not configured():
        raise BillingUnavailable("Billing isn't available right now.")
    from app.services import billing_stripe

    billing_stripe.set_cancel_at_period_end(db, sub, cancel)
    return status(db, user)


def confirm(db: Session, user: User, session_id: str) -> dict[str, Any]:
    """After checkout: ask Stripe (server to server) about this member's session, in case the webhook is late."""
    checkout: dict[str, Any] | None = None
    if configured():
        from app.services import billing_stripe

        checkout = billing_stripe.confirm_session(db, user, session_id)
        billing_stripe.reconcile_quietly(db, user.id)
    return {**status(db, user), "checkout": checkout}


def close_account(db: Session, user: User) -> None:
    """Account deletion: stop any live subscription first, so nobody keeps being charged for a deleted account."""
    sub = entitlements.subscription(db, user)
    if sub is None or not sub.subscription_id or sub.status in ("canceled", "incomplete_expired"):
        return
    from app.services import billing_stripe

    billing_stripe.cancel_now(sub)
