"""Nexis billing (Free, Nexis Plus, Nexis Pro), independent of the payment provider.

Routes call these functions; ``billing_stripe`` implements them for Stripe (Checkout, Billing, Customer Portal,
webhooks). Another provider would add its own module and its own ``subscriptions.provider`` value; plans
(``app/core/plans.py``), entitlements and usage stay the same.

Nothing here trusts the browser: a member can only buy the plans in ``plans.PRICES``, sold at the Stripe prices
configured on the server — and only if Stripe confirms those prices are exactly AED 29/month (Plus) and AED 69/month
(Pro). Paid access comes only from subscription state Stripe reported (``entitlements.tier_of``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core import plans
from app.core.config import get_settings
from app.core.errors import NexisError, ValidationFailed
from app.models import User
from app.services import entitlements

PLAN_KEYS = tuple(plans.PRICES)
CANCELLATION = ("Cancel any time. You keep your plan until the end of the month you've paid for, then move to Free. Before then "
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


def price_ids() -> dict[str, str | None]:
    s = get_settings()
    return {"plus_monthly": s.stripe_plus_price_id, "pro_monthly": s.stripe_pro_price_id}


def legacy_price_ids() -> set[str]:
    s = get_settings()
    return {p for p in (s.stripe_pro_monthly_price_id, s.stripe_pro_yearly_price_id) if p}


def configured() -> bool:
    """Keys and both price ids are set. Whether the Stripe prices match the plans is checked in ``catalog``."""
    s = get_settings()
    return bool(s.stripe_secret_key and s.stripe_webhook_secret and all(price_ids().values()))


def test_mode() -> bool:
    return bool((get_settings().stripe_secret_key or "").startswith(("sk_test_", "rk_test_")))


def _tester(user: User | None) -> bool:
    emails = {e.strip().lower() for e in get_settings().billing_test_emails.split(",") if e.strip()}
    return bool(user and user.email and user.email.lower() in emails)


def _sellable(db: Session, user: User | None = None) -> tuple[bool, str | None]:
    if not configured():
        return False, "Paid plans aren't available to buy yet."
    if test_mode() and get_settings().env == "production" and not _tester(user):
        return False, "Paid plans aren't available to buy yet."  # test keys on the public site: testers only
    from app.services import billing_stripe

    problems = billing_stripe.verify_prices(db)
    if problems:
        return False, "Paid plans are temporarily unavailable."  # the details are logged, never shown to members
    return True, None


def payment_methods() -> list[str]:
    wallets = [w.strip() for w in get_settings().billing_wallets.split(",") if w.strip()]
    return ["Card (Visa, Mastercard and other major cards)", *wallets]


def catalog(db: Session, user: User | None = None) -> dict[str, Any]:
    """Plans, limits and prices from ``plans.py`` — one source for the pricing page, Settings and checkout."""
    sellable, reason = _sellable(db, user)
    return {**plans.public(), "available": sellable, "unavailable_reason": reason, "test_mode": sellable and test_mode(),
            "payment_methods": payment_methods(), "billing_interval": "monthly", "cancellation": CANCELLATION}  # fmt: skip


def status(db: Session, user: User) -> dict[str, Any]:
    sub = entitlements.subscription(db, user)
    tier = entitlements.sub_tier(sub)
    paid = tier != "free"
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    issue = bool(sub and sub.status in ("past_due", "unpaid"))
    from app.services import investments

    return {
        "plan": tier,
        "plan_name": plans.NAMES[tier],
        "paid": paid,
        "pro": tier == "pro",
        "status": sub.status if sub else None,
        "billing": plans.price_label(sub.plan) if paid and sub else None,
        "billing_plan": sub.plan if paid and sub else None,
        "renews_at": iso(sub.current_period_end) if paid and sub and not sub.cancel_at_period_end else None,
        "access_until": iso(sub.current_period_end) if paid and sub and sub.cancel_at_period_end else None,
        "cancel_at_period_end": bool(paid and sub and sub.cancel_at_period_end),
        "payment_issue": issue,
        "payment_message": "Your payment needs attention. Please update your payment method." if issue else None,
        "can_manage": bool(sub and sub.customer_id),
        "can_cancel": bool(paid and sub and sub.subscription_id and not sub.cancel_at_period_end),
        "can_resume": bool(paid and sub and sub.subscription_id and sub.cancel_at_period_end),
        "can_change_plan": bool(paid and sub and sub.subscription_id and not issue),
        "investments": investments.state(db, user),
        **entitlements.get_user_entitlements(db, user),
        "billing_available": configured(),
    }


def checkout(db: Session, user: User, plan: str) -> dict[str, str]:
    if plan not in PLAN_KEYS:
        raise ValidationFailed("unknown plan")
    if entitlements.has_paid(db, user):
        raise AlreadyPro("You already have a paid Nexis plan. To switch between Plus and Pro, use Change plan in Settings.")
    sellable, reason = _sellable(db, user)
    if not sellable:
        raise BillingUnavailable(reason or "Paid plans aren't available to buy yet.")
    from app.services import billing_stripe

    return {"url": billing_stripe.checkout_url(db, user, plan)}


def change_plan(db: Session, user: User, plan: str, keep: list[str] | None = None) -> dict[str, Any]:
    """Switch a live subscription between Plus and Pro. Moving to a smaller allowance with more active investments
    than it allows needs the member's choice of which to keep first (never chosen for them)."""
    if plan not in PLAN_KEYS:
        raise ValidationFailed("unknown plan")
    sub = entitlements.subscription(db, user)
    tier = entitlements.sub_tier(sub)
    if tier == "free" or sub is None or not sub.subscription_id:
        raise ValidationFailed("You don't have a paid plan to change. Choose a plan on the pricing page.")
    if sub.status in ("past_due", "unpaid"):
        raise ValidationFailed("Please update your payment method before changing plans.")
    target = plans.PRICES[plan]["tier"]
    if target == tier and not sub.cancel_at_period_end:
        raise ValidationFailed(f"You're already on {plans.NAMES[tier]}.")
    sellable, reason = _sellable(db, user)
    if not sellable:
        raise BillingUnavailable(reason or "Plan changes aren't available right now.")
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
    billing_stripe.change_plan(db, sub, plan)
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
    if not entitlements.sub_is_paid(sub) or sub is None or not sub.subscription_id:
        raise ValidationFailed("There's no active Nexis subscription.")
    if not configured():
        raise BillingUnavailable("Billing isn't available right now.")
    from app.services import billing_stripe

    billing_stripe.set_cancel_at_period_end(db, sub, cancel)
    return status(db, user)


def confirm(db: Session, user: User, session_id: str) -> dict[str, Any]:
    """After checkout: ask Stripe (server to server) about this member's session, in case the webhook is late."""
    if configured():
        from app.services import billing_stripe

        billing_stripe.confirm_session(db, user, session_id)
        billing_stripe.reconcile_quietly(db, user.id)
    return status(db, user)


def close_account(db: Session, user: User) -> None:
    """Account deletion: stop any live subscription first, so nobody keeps being charged for a deleted account."""
    sub = entitlements.subscription(db, user)
    if sub is None or not sub.subscription_id or sub.status in ("canceled", "incomplete_expired"):
        return
    from app.services import billing_stripe

    billing_stripe.cancel_now(sub)
