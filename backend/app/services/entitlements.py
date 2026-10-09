"""Which plan an account is on, what it may use, and how much. The only place that decides.

Public functions (everything else in Nexis calls these, never checks a plan itself):

* ``tier_of(db, user)`` — "free", "plus" or "pro", from subscription state the payment provider reported.
* ``has_paid(db, user)`` — any paid plan; ``has_pro`` — exactly Nexis Pro.
* ``require(db, user, capability)`` — raise ``UpgradeRequired`` unless the plan includes a paid capability.
* ``get_user_entitlements(db, user)`` — plan, limits, capabilities and current usage, for the API and Settings.
* ``consume_usage(db, user, feature)`` — count one monthly use or refuse; ``refund`` gives it back if the work failed.
* ``check_capacity(db, user, feature, adding)`` — capacity limits (investments, watchlist): refuses going over.
* ``anonymous_advisor(db, request)`` — the daily allowance for visitors without an account.

Limits, prices and capabilities come from ``app/core/plans.py``. Paid access follows the provider's status:

=====================  ===========  ========================================================================
status                 paid access  why
=====================  ===========  ========================================================================
active, trialing       yes          paid
past_due               yes          a renewal failed and Stripe is retrying; the member is asked to fix it
canceled, unpaid       no           the subscription ended / retries were exhausted
incomplete(_expired)   no           the first payment never completed
paused                 no           no payment is being collected
=====================  ===========  ========================================================================

A subscription still marked active whose paid period ended more than ``GRACE`` ago (a missed webhook) is not paid.
Cancelling "at period end" keeps the plan until ``current_period_end``. Monthly allowances reset on the 1st of each
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

PAID_STATUSES = frozenset({"active", "trialing", "past_due"})
GRACE = timedelta(days=2)
MONTHLY = tuple(k for k, f in plans.FEATURES.items() if f["monthly"])
WARN_AT = 0.9


class UsageLimitReached(NexisError):
    status_code = 402
    code = "usage_limit"


class UpgradeRequired(NexisError):
    status_code = 402
    code = "upgrade_required"


class SignInRequired(NexisError):
    status_code = 401
    code = "sign_in_required"


# ------------------------------------------------------------------ subscription and plan


def subscription(db: Session, user: User | None) -> Subscription | None:
    if user is None:
        return None
    return db.scalars(select(Subscription).where(Subscription.user_id == user.id)).first()


def sub_is_paid(sub: Subscription | None, now: datetime | None = None) -> bool:
    if sub is None or sub.status not in PAID_STATUSES:
        return False
    now = now or utcnow()
    return not (sub.current_period_end is not None and sub.current_period_end + GRACE < now)


def sub_tier(sub: Subscription | None, now: datetime | None = None) -> str:
    if not sub_is_paid(sub, now):
        return "free"
    return plans.tier_of(sub.plan if sub else None) or "pro"  # a live subscription Nexis can't map was sold as Pro


def sub_is_pro(sub: Subscription | None, now: datetime | None = None) -> bool:
    return sub_tier(sub, now) == "pro"


def get_subscription_status(db: Session, user: User | None) -> dict[str, Any]:
    sub = subscription(db, user)
    return {"subscription": sub, "tier": sub_tier(sub), "paid": sub_is_paid(sub), "status": sub.status if sub else None}


def tier_of(db: Session, user: User | None) -> str:
    """The single plan check. Nothing the browser sends is ever consulted."""
    return sub_tier(subscription(db, user))


def plan_of(db: Session, user: User | None) -> str:
    return tier_of(db, user)


def has_paid(db: Session, user: User | None) -> bool:
    return tier_of(db, user) != "free"


def has_pro(db: Session, user: User | None) -> bool:
    return tier_of(db, user) == "pro"


def can(db: Session, user: User | None, capability: str) -> bool:
    return plans.allows(tier_of(db, user), capability)


def require(db: Session, user: User | None, capability: str) -> None:
    tier = tier_of(db, user)
    if plans.allows(tier, capability):
        return
    c = plans.CAPABILITIES[capability]
    need = c["tiers"][0]
    raise UpgradeRequired(
        f"{c['label']} is included with {plans.NAMES[need]}" + (" and Nexis Pro." if need == "plus" else "."),
        details={"capability": capability, "label": c["label"], "summary": c["summary"], "plan": tier, "required": need,
                 "upgrade": True, "upgrade_text": f"Upgrade to {plans.NAMES[need]} to unlock {c['label'].lower()}."},
    )  # fmt: skip


def limit(feature: str, plan: str) -> int:
    return plans.LIMITS[plan][feature]


def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, (start + timedelta(days=32)).replace(day=1)


# ------------------------------------------------------------------ usage


def used(db: Session, user: User, feature: str, since: datetime | None = None) -> int:
    """Monthly features: uses this calendar month. Capacity features: what the member has right now."""
    if feature in ("investments", "watchlist"):
        from app.services import portfolio

        return len(portfolio.active_symbols(db, user) if feature == "investments" else portfolio.watch_symbols(db, user))
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
    caps = {c: plans.allows(plan, c) for c in plans.CAPABILITIES}
    return {
        "plan": plan,
        "plan_name": plans.NAMES[plan],
        "limits": dict(plans.LIMITS[plan]),
        "capabilities": caps,
        "usage": usage,
    }


def next_tier(plan: str, feature: str) -> str | None:
    """The cheapest higher plan that raises this limit."""
    for t in plans.TIERS[plans.rank(plan) + 1 :]:
        if limit(feature, t) > limit(feature, plan):
            return t
    return None


def limit_error(feature: str, plan: str, n: int, cap: int) -> UsageLimitReached:
    f = plans.FEATURES[feature]
    nxt = next_tier(plan, feature)
    _, end = month_bounds()
    name = plans.NAMES[plan]
    if feature == "investments":
        msg = (f"You have {n} of {cap} active investment{'s' if cap != 1 else ''} on the {name} plan. Archive or remove one to add "
               "another" + (f", or upgrade to {plans.NAMES[nxt]} for {limit(feature, nxt)}." if nxt else "."))  # fmt: skip
    elif not f["monthly"]:
        msg = f"You're following {n} of {cap} {f['unit']}. Remove one to follow another."
    elif nxt is None:
        msg = f"You've used this month's {cap} {f['unit']} on {name}. Your allowance resets on {end.day} {end:%B %Y}."
    else:
        msg = f"You've reached your {name} {f['label']} limit for this month."
    upgrade_text = None
    if nxt:
        upgrade_text = f"Upgrade to {plans.NAMES[nxt]} for {limit(feature, nxt)} {f['unit']}" + (
            "/month." if f["monthly"] else "."
        )
    return UsageLimitReached(msg, details={
        "feature": feature, "label": f["label"], "unit": f["unit"], "action": f["action"], "value": f["value"], "used": n, "limit": cap,
        "plan": plan, "next_plan": nxt, "next_limit": limit(feature, nxt) if nxt else None, "monthly": f["monthly"],
        "resets_at": end.isoformat() + "Z" if f["monthly"] else None, "upgrade": nxt is not None, "upgrade_text": upgrade_text,
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
        raise limit_error(feature, plan, n - 1, cap)
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
    """Refuse to go over a capacity limit. Nothing is ever deleted here (see ``investments`` for downgrades)."""
    plan = plan_of(db, user)
    n, cap = used(db, user, feature), limit(feature, plan)
    if n + adding > cap:
        raise limit_error(feature, plan, n, cap)


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
        ) from exc  # fmt: skip
