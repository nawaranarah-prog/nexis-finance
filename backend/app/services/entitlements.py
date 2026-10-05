"""Who gets Nexis Pro, and how much of each limited feature an account may use. The only place that decides.

Public functions (everything else in Nexis calls these, never checks a plan itself):

* ``get_subscription_status(db, user)`` — the member's subscription row and whether it grants Pro.
* ``has_pro(db, user)`` — the single Pro check.
* ``get_user_entitlements(db, user)`` — plan, limits and current usage, for the API and Settings → Billing.
* ``check_usage_limit(db, user, feature)`` — would one more use be allowed? (no side effects)
* ``consume_usage(db, user, feature)`` — count one monthly use or refuse; ``refund`` gives it back if the work failed.
* ``check_capacity(db, user, feature, adding)`` — capacity limits (My Nexis assets): refuses going over, never deletes.
* ``anonymous_advisor(db, request)`` — the daily allowance for visitors without an account.

Limits and prices come from ``app/core/plans.py``. Pro access follows the provider's subscription status:

=====================  ==========  =========================================================================
status                 Pro access  why
=====================  ==========  =========================================================================
active, trialing       yes         paid
past_due               yes         a renewal failed and Stripe is retrying; the member is asked to fix it
canceled, unpaid       no          the subscription ended / retries were exhausted
incomplete(_expired)   no          the first payment never completed
paused                 no          no payment is being collected
=====================  ==========  =========================================================================

A subscription still marked active whose paid period ended more than ``GRACE`` ago (a missed webhook) is not Pro.
Cancelling "at period end" keeps Pro until ``current_period_end``. Monthly allowances reset on the 1st of each
calendar month (UTC) for every plan.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import plans
from app.core.errors import NexisError
from app.db.base import utcnow
from app.models import Subscription, UsageEvent, User

PRO_STATUSES = frozenset({"active", "trialing", "past_due"})
GRACE = timedelta(days=2)
MONTHLY = tuple(k for k, f in plans.FEATURES.items() if f["monthly"])
WARN_AT = 0.9


class UsageLimitReached(NexisError):
    status_code = 402
    code = "usage_limit"


class SignInRequired(NexisError):
    status_code = 401
    code = "sign_in_required"


# ------------------------------------------------------------------ subscription and plan


def subscription(db: Session, user: User | None) -> Subscription | None:
    if user is None:
        return None
    return db.scalars(select(Subscription).where(Subscription.user_id == user.id)).first()


def sub_is_pro(sub: Subscription | None, now: datetime | None = None) -> bool:
    if sub is None or sub.status not in PRO_STATUSES:
        return False
    now = now or utcnow()
    return not (sub.current_period_end is not None and sub.current_period_end + GRACE < now)


def get_subscription_status(db: Session, user: User | None) -> dict[str, Any]:
    sub = subscription(db, user)
    return {"subscription": sub, "pro": sub_is_pro(sub), "status": sub.status if sub else None}


def has_pro(db: Session, user: User | None) -> bool:
    """The single entitlement check. Nothing the browser sends is ever consulted."""
    return sub_is_pro(subscription(db, user))


def plan_of(db: Session, user: User | None) -> str:
    return "pro" if has_pro(db, user) else "free"


def limit(feature: str, plan: str) -> int:
    return plans.LIMITS[plan][feature]


def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, (start + timedelta(days=32)).replace(day=1)


# ------------------------------------------------------------------ usage


def used(db: Session, user: User, feature: str, since: datetime | None = None) -> int:
    """Monthly features: uses this calendar month. Capacity features: what the member holds right now."""
    if feature == "my_nexis_assets":
        from app.services import portfolio

        return len(portfolio.tracked(db, user))
    since = since or month_bounds()[0]
    return db.scalar(select(func.coalesce(func.sum(UsageEvent.units), 0)).where(
        UsageEvent.user_id == user.id, UsageEvent.feature == feature, UsageEvent.created_at >= since)) or 0  # fmt: skip


def _row(feature: str, n: int, cap: int, resets: datetime | None) -> dict[str, Any]:
    f = plans.FEATURES[feature]
    return {"label": f["label"], "unit": f["unit"], "monthly": f["monthly"], "used": n, "limit": cap, "remaining": max(0, cap - n),
            "near_limit": n >= cap * WARN_AT, "at_limit": n >= cap, "over_capacity": n > cap,
            "resets_at": resets.isoformat() + "Z" if resets else None}  # fmt: skip


def get_user_entitlements(db: Session, user: User) -> dict[str, Any]:
    plan = plan_of(db, user)
    _, end = month_bounds()
    usage = {k: _row(k, used(db, user, k), limit(k, plan), end if plans.FEATURES[k]["monthly"] else None) for k in plans.FEATURES}
    return {"plan": plan, "limits": dict(plans.LIMITS[plan]), "usage": usage}


def _limit_error(feature: str, plan: str, n: int, cap: int) -> UsageLimitReached:
    f = plans.FEATURES[feature]
    pro_cap = limit(feature, "pro")
    _, end = month_bounds()
    if not f["monthly"]:
        msg = (f"You're tracking {n} of {cap} assets on your {'Nexis Pro' if plan == 'pro' else 'Free'} plan. "
               "Remove one to add another" + ("." if plan == "pro" else f", or upgrade to Nexis Pro for {pro_cap} tracked assets."))  # fmt: skip
    elif plan == "pro":
        msg = f"You've used this month's {cap} {f['unit']} on Nexis Pro. Your allowance resets on {end.day} {end:%B %Y}."
    else:
        msg = f"You've reached your Free {f['label']} limit."
    return UsageLimitReached(msg, details={
        "feature": feature, "label": f["label"], "unit": f["unit"], "action": f["action"], "value": f["value"], "used": n, "limit": cap,
        "plan": plan, "pro_limit": pro_cap, "monthly": f["monthly"], "resets_at": end.isoformat() + "Z" if f["monthly"] else None,
        "upgrade": plan == "free", "upgrade_text": f"Upgrade to Nexis Pro for {pro_cap} {f['unit']}" + ("/month." if f["monthly"] else "."),
    })  # fmt: skip


def check_usage_limit(db: Session, user: User, feature: str) -> dict[str, Any]:
    plan = plan_of(db, user)
    n, cap = used(db, user, feature), limit(feature, plan)
    return {"allowed": n < cap, "used": n, "limit": cap, "plan": plan}


def consume_usage(db: Session, user: User, feature: str) -> int:
    """Count one monthly use, or raise ``UsageLimitReached``. Returns the record id (for ``refund``).

    The row is written first and the total checked afterwards, so requests racing each other (several tabs, scripts)
    can't both slip under the limit: if the total is over, this use is removed and the request refused.
    """
    if feature not in MONTHLY:
        raise ValueError(f"{feature} is not a monthly feature")
    plan = plan_of(db, user)
    cap = limit(feature, plan)
    start, _ = month_bounds()
    ev = UsageEvent(user_id=user.id, feature=feature, created_at=utcnow(), units=1)
    db.add(ev)
    db.commit()
    n = used(db, user, feature, start)
    if n > cap:
        db.delete(ev)
        db.commit()
        raise _limit_error(feature, plan, n - 1, cap)
    return ev.id


def refund(db: Session, record: int | None) -> None:
    if record is None:
        return
    ev = db.get(UsageEvent, record)
    if ev is not None:
        db.delete(ev)
        db.commit()


@contextmanager
def metered(db: Session, user: User, feature: str) -> Iterator[int]:
    """Consume one use for the block; refunded if the block fails."""
    rid = consume_usage(db, user, feature)
    try:
        yield rid
    except Exception:
        db.rollback()
        refund(db, rid)
        raise


def check_capacity(db: Session, user: User, feature: str, adding: int = 1) -> None:
    """Refuse to go over a capacity limit. Existing items are never touched (after a downgrade they all stay)."""
    plan = plan_of(db, user)
    n, cap = used(db, user, feature), limit(feature, plan)
    if n + adding > cap:
        raise _limit_error(feature, plan, n, cap)


# ------------------------------------------------------------------ visitors


def anonymous_advisor(db: Session, request: Request) -> None:
    """Visitors get a few Advisor messages a day (per IP, server-side) before being asked to create a free account."""
    from app.services import ratelimit

    n = plans.ANONYMOUS["advisor_daily"]
    try:
        ratelimit.hit(db, f"anon-advisor:{ratelimit.client_ip(request)}", n, timedelta(hours=24))
    except ratelimit.RateLimited as exc:
        free = limit("advisor", "free")
        raise SignInRequired(
            "You've reached today's free AI Advisor limit.",
            details={"feature": "advisor", "free_limit": free,
                     "next": f"Create a free Nexis account to continue with {free} AI Advisor messages per month."},
        ) from exc
