"""Nexis Pro billing, independent of the payment provider.

Routes call these functions; ``billing_stripe`` implements them for Stripe (Checkout, Billing, Customer Portal,
webhooks). Another provider (for example Tabby) would add its own module with the same functions and its own value
in ``subscriptions.provider`` — plans, entitlements and usage don't change.

Nothing here trusts the browser: the plan a member may buy is checked against the configured price ids, and Pro
access comes only from subscription state the provider reported (``entitlements.has_pro``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError, ValidationFailed
from app.models import User
from app.services import entitlements

PLANS = ("pro_monthly", "pro_yearly")
PRO_FEATURES = [
    "Up to {advisor} AI Advisor questions per billing period (Free: {advisor_free} a month)",
    "Up to {report} PDF comparison and valuation reports (Free: {report_free} a month)",
    "Up to {brief} AI intelligence briefs on the assets you track (Free: {brief_free} a month)",
    "Everything in Free",
]
FREE_FEATURES = [
    "UAE and global markets: prices, charts, fundamentals and news",
    "Interactive comparisons and DCF / peer valuation",
    "Nexis Pulse: read, post, reply and follow — anonymously",
    "My Nexis: investments, watchlist, Today, alerts and notifications",
    "{advisor_free} AI Advisor questions, {report_free} PDF reports and {brief_free} AI briefs a month",
]


class BillingUnavailable(NexisError):
    status_code = 503
    code = "billing_unavailable"


def price_ids() -> dict[str, str | None]:
    s = get_settings()
    return {"pro_monthly": s.stripe_pro_monthly_price_id, "pro_yearly": s.stripe_pro_yearly_price_id}


def configured() -> bool:
    s = get_settings()
    return bool(s.stripe_secret_key and s.stripe_webhook_secret and any(price_ids().values()))


def _features() -> dict[str, list[str]]:
    lim = entitlements.limits()
    fmt = {k: v["pro"] for k, v in lim.items()} | {f"{k}_free": v["free"] for k, v in lim.items()}
    return {"free": [f.format(**fmt) for f in FREE_FEATURES], "pro": [f.format(**fmt) for f in PRO_FEATURES]}


def catalog(db: Session) -> dict[str, Any]:
    """Plans, limits and the real prices from the provider. No price is ever invented: unknown prices are null."""
    from app.services import billing_stripe

    prices = billing_stripe.prices(db) if configured() else {}
    return {
        "configured": configured(),
        "plans": [{"key": k, **prices[k]} for k in PLANS if k in prices],
        "limits": entitlements.limits(),
        "features": _features(),
        "payment_methods": ["Visa", "Mastercard", "Apple Pay", "Google Pay"],
        "cancellation": "Cancel any time from Manage subscription. You keep Nexis Pro until the end of the period you've paid for, then return to Free.",
    }


def status(db: Session, user: User) -> dict[str, Any]:
    sub = entitlements.subscription(db, user)
    pro = entitlements.sub_is_pro(sub)
    iso = (lambda d: d.isoformat() + "Z" if d else None)  # noqa: E731
    return {
        "plan": "pro" if pro else "free",
        "pro": pro,
        "status": sub.status if sub else None,
        "interval": {"pro_monthly": "month", "pro_yearly": "year"}.get(sub.plan or "") if sub else None,
        "renews_at": iso(sub.current_period_end) if pro and sub and not sub.cancel_at_period_end else None,
        "access_until": iso(sub.current_period_end) if pro and sub and sub.cancel_at_period_end else None,
        "cancel_at_period_end": bool(sub and sub.cancel_at_period_end),
        "payment_issue": bool(sub and (sub.status in ("past_due", "unpaid") or (sub.payment_failed_at and sub.status != "active"))),
        "can_manage": bool(sub and sub.customer_id),
        "usage": entitlements.usage(db, user),
        "billing_available": configured(),
    }


def checkout(db: Session, user: User, plan: str) -> dict[str, str]:
    if plan not in PLANS:
        raise ValidationFailed("unknown plan")
    if not configured() or not price_ids().get(plan):
        raise BillingUnavailable("Nexis Pro isn't available to buy yet.")
    if entitlements.has_pro(db, user):
        raise ValidationFailed("You already have Nexis Pro. Use Manage subscription to change your plan.")
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


def confirm(db: Session, user: User, session_id: str) -> dict[str, Any]:
    """After checkout: ask the provider (server to server) about this member's session, in case the webhook is late."""
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
