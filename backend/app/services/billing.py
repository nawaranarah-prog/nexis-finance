"""Nexis Pro billing, independent of the payment provider.

Routes call these functions; ``billing_stripe`` implements them for Stripe (Checkout, Billing, Customer Portal,
webhooks). Another provider (for example Tabby) would add its own module and its own ``subscriptions.provider``
value; plans (``app/core/plans.py``), entitlements and usage stay the same.

Nothing here trusts the browser: the only things a member can buy are the two plans in ``plans.PRICES``, sold at
the Stripe prices configured on the server — and only if Stripe confirms those prices are exactly $9.99/month and
$99/year. Pro access comes only from subscription state Stripe reported (``entitlements.has_pro``).
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


class BillingUnavailable(NexisError):
    status_code = 503
    code = "billing_unavailable"


class AlreadyPro(NexisError):
    status_code = 409
    code = "already_pro"


def price_ids() -> dict[str, str | None]:
    s = get_settings()
    return {"pro_monthly": s.stripe_pro_monthly_price_id, "pro_yearly": s.stripe_pro_yearly_price_id}


def configured() -> bool:
    """Keys and both price ids are set. Whether the Stripe prices match the plan is checked in ``catalog``."""
    s = get_settings()
    return bool(s.stripe_secret_key and s.stripe_webhook_secret and all(price_ids().values()))


def _sellable(db: Session) -> tuple[bool, str | None]:
    if not configured():
        return False, "Nexis Pro isn't available to buy yet."
    from app.services import billing_stripe

    problems = billing_stripe.verify_prices(db)
    if problems:
        return False, "Nexis Pro is temporarily unavailable."  # the details are logged, never shown to members
    return True, None


def catalog(db: Session) -> dict[str, Any]:
    """Plans, limits and prices from ``plans.py`` — one source for the pricing page, Settings and checkout."""
    sellable, reason = _sellable(db)
    return {**plans.public(), "available": sellable, "unavailable_reason": reason,
            "payment_methods": ["Visa", "Mastercard", "Apple Pay", "Google Pay"],
            "cancellation": "Cancel any time. You keep Nexis Pro until the end of the period you've paid for, then return to Free. "
                            "Your account, My Nexis and discussions are never deleted."}  # fmt: skip


def _price_label(plan: str | None) -> str | None:
    p = plans.PRICES.get(plan or "")
    if not p:
        return None
    amount = p["amount_cents"] / 100
    return f"${amount:,.2f}/{p['interval']}" if amount % 1 else f"${amount:,.0f}/{p['interval']}"


def status(db: Session, user: User) -> dict[str, Any]:
    sub = entitlements.subscription(db, user)
    pro = entitlements.sub_is_pro(sub)
    iso = (lambda d: d.isoformat() + "Z" if d else None)  # noqa: E731
    issue = bool(sub and sub.status in ("past_due", "unpaid"))
    return {
        "plan": "pro" if pro else "free",
        "pro": pro,
        "status": sub.status if sub else None,
        "billing": _price_label(sub.plan) if pro and sub else None,
        "billing_plan": sub.plan if pro and sub else None,
        "renews_at": iso(sub.current_period_end) if pro and sub and not sub.cancel_at_period_end else None,
        "access_until": iso(sub.current_period_end) if pro and sub and sub.cancel_at_period_end else None,
        "cancel_at_period_end": bool(pro and sub and sub.cancel_at_period_end),
        "payment_issue": issue,
        "payment_message": "Your payment needs attention. Please update your payment method." if issue else None,
        "can_manage": bool(sub and sub.customer_id),
        "can_cancel": bool(pro and sub and sub.subscription_id and not sub.cancel_at_period_end),
        "can_resume": bool(pro and sub and sub.subscription_id and sub.cancel_at_period_end),
        **entitlements.get_user_entitlements(db, user),
        "billing_available": configured(),
    }


def checkout(db: Session, user: User, plan: str) -> dict[str, str]:
    if plan not in PLAN_KEYS:
        raise ValidationFailed("unknown plan")
    if entitlements.has_pro(db, user):
        raise AlreadyPro("You're already on Nexis Pro.")
    sellable, reason = _sellable(db)
    if not sellable:
        raise BillingUnavailable(reason or "Nexis Pro isn't available to buy yet.")
    from app.services import billing_stripe

    return {"url": billing_stripe.checkout_url(db, user, plan)}


def portal(db: Session, user: User) -> dict[str, str]:
    sub = entitlements.subscription(db, user)
    if not configured():
        raise BillingUnavailable("Billing isn't available right now.")
    if sub is None or not sub.customer_id:
        raise ValidationFailed("There's no billing account to manage yet.")
    from app.services import billing_stripe

    return {"url": billing_stripe.portal_url(sub)}


def set_cancel(db: Session, user: User, cancel: bool) -> dict[str, Any]:
    """Cancel at the end of the paid period (or undo that). Pro continues until the period ends."""
    sub = entitlements.subscription(db, user)
    if not entitlements.sub_is_pro(sub) or sub is None or not sub.subscription_id:
        raise ValidationFailed("There's no active Nexis Pro subscription.")
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
    return status(db, user)


def close_account(db: Session, user: User) -> None:
    """Account deletion: stop any live subscription first, so nobody keeps being charged for a deleted account."""
    sub = entitlements.subscription(db, user)
    if sub is None or not sub.subscription_id or sub.status in ("canceled", "incomplete_expired"):
        return
    from app.services import billing_stripe

    billing_stripe.cancel_now(sub)
