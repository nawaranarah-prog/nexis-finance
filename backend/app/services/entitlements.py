"""Who gets Nexis Pro, and how much of each metered feature an account may use. The only place that decides.

Plans
* **Free** — everything in Nexis, with monthly allowances for the features that cost money per use (AI).
* **Nexis Pro** — the same features with much higher, fair-use allowances per billing period.

Pro access (``has_pro``) follows the payment provider's subscription status:

=====================  ==========  ===========================================================================
status                 Pro access  why
=====================  ==========  ===========================================================================
active, trialing       yes         paid (or in a trial)
past_due               yes         a renewal failed and the provider is retrying; the member is asked to fix it
incomplete             no          the first payment never completed
incomplete_expired     no          ...and expired
unpaid, canceled       no          retries exhausted, or the subscription ended
paused                 no          no payment is being collected
=====================  ==========  ===========================================================================

A subscription the provider still calls active but whose paid period ended more than ``GRACE`` ago (a missed
webhook) is treated as ended. Cancelling "at period end" keeps Pro until ``current_period_end``.

Metered features are counted server-side in ``usage_events``: a use is *reserved* before the expensive call and
refunded if the call fails, so parallel requests can never exceed the limit and failures don't cost the member.
Free allowances reset each calendar month (UTC); Pro allowances each billing period.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError
from app.db.base import utcnow
from app.models import Subscription, UsageEvent, User

PRO_STATUSES = frozenset({"active", "trialing", "past_due"})
GRACE = timedelta(days=2)


@dataclass(frozen=True)
class Feature:
    key: str
    label: str  # how a use is counted, e.g. "AI Advisor questions"
    action: str  # what the member was doing, for the upgrade message
    value: str  # why it's worth it, in one sentence
    free_setting: str
    pro_setting: str


FEATURES: dict[str, Feature] = {f.key: f for f in (
    Feature("advisor", "AI Advisor questions", "ask the AI Advisor",
            "The Advisor checks live prices, news, analyst ratings and valuations before every answer.",
            "free_advisor_limit", "pro_advisor_limit"),
    Feature("report", "PDF research reports", "generate a PDF research report",
            "Comparison and valuation reports with charts, tables and a written analysis you can keep and share.",
            "free_report_limit", "pro_report_limit"),
    Feature("brief", "AI intelligence briefs", "get an AI brief on an asset you track",
            "A short, sourced note on what just happened to an asset you hold or watch, and why it may matter.",
            "free_brief_limit", "pro_brief_limit"),
)}  # fmt: skip


class UsageLimitReached(NexisError):
    status_code = 402
    code = "usage_limit"


class SignInRequired(NexisError):
    status_code = 401
    code = "sign_in_required"


# ------------------------------------------------------------------ plan


def subscription(db: Session, user: User | None) -> Subscription | None:
    if user is None:
        return None
    return db.scalars(select(Subscription).where(Subscription.user_id == user.id)).first()


def sub_is_pro(sub: Subscription | None, now: datetime | None = None) -> bool:
    if sub is None or sub.status not in PRO_STATUSES:
        return False
    now = now or utcnow()
    return not (sub.current_period_end is not None and sub.current_period_end + GRACE < now)


def has_pro(db: Session, user: User | None) -> bool:
    """The single entitlement check. Never trust anything the browser says about the plan."""
    return sub_is_pro(subscription(db, user))


def limit(feature: str, pro: bool) -> int:
    f = FEATURES[feature]
    return int(getattr(get_settings(), f.pro_setting if pro else f.free_setting))


def limits() -> dict[str, dict[str, int]]:
    return {k: {"free": limit(k, False), "pro": limit(k, True)} for k in FEATURES}


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def period(db: Session, user: User) -> tuple[datetime, datetime]:
    """The window usage is counted in: the billing period for Pro, the calendar month (UTC) for Free."""
    now = utcnow()
    sub = subscription(db, user)
    if sub_is_pro(sub, now) and sub and sub.current_period_start and sub.current_period_end and sub.current_period_start <= now:
        return sub.current_period_start, max(sub.current_period_end, now)
    return _month_bounds(now)


def used(db: Session, user: User, feature: str, since: datetime) -> int:
    return db.scalar(select(func.coalesce(func.sum(UsageEvent.units), 0)).where(
        UsageEvent.user_id == user.id, UsageEvent.feature == feature, UsageEvent.created_at >= since)) or 0  # fmt: skip


def usage(db: Session, user: User) -> dict[str, Any]:
    pro = has_pro(db, user)
    start, end = period(db, user)
    out = {}
    for k, f in FEATURES.items():
        n, cap = used(db, user, k, start), limit(k, pro)
        out[k] = {"label": f.label, "used": n, "limit": cap, "remaining": max(0, cap - n), "resets_at": end.isoformat() + "Z"}
    return out


# ------------------------------------------------------------------ enforcement


def _limit_error(db: Session, user: User, feature: str, pro: bool, n: int, cap: int, end: datetime) -> UsageLimitReached:
    f = FEATURES[feature]
    if pro:
        msg = f"You've used this billing period's {cap} {f.label}. Your allowance renews on {end:%d %b %Y}."
    else:
        msg = f"You've used your {cap} free {f.label} this month."
    return UsageLimitReached(msg, details={
        "feature": feature, "label": f.label, "action": f.action, "value": f.value, "used": n, "limit": cap, "plan": "pro" if pro else "free",
        "pro_limit": limit(feature, True), "resets_at": end.isoformat() + "Z", "upgrade": not pro,
    })  # fmt: skip


def reserve(db: Session, user: User, feature: str) -> int:
    """Count one use, or raise ``UsageLimitReached``. Returns the reservation id (for ``refund``).

    The row is written first and the total checked afterwards, so two requests racing each other can't both slip
    under the limit: if the total is over, this reservation is removed and the request refused.
    """
    pro = has_pro(db, user)
    start, end = period(db, user)
    cap = limit(feature, pro)
    ev = UsageEvent(user_id=user.id, feature=feature, created_at=utcnow(), units=1)
    db.add(ev)
    db.commit()
    n = used(db, user, feature, start)
    if n > cap:
        db.delete(ev)
        db.commit()
        raise _limit_error(db, user, feature, pro, n - 1, cap, end)
    return ev.id


def refund(db: Session, reservation: int | None) -> None:
    if reservation is None:
        return
    ev = db.get(UsageEvent, reservation)
    if ev is not None:
        db.delete(ev)
        db.commit()


@contextmanager
def metered(db: Session, user: User, feature: str) -> Iterator[int]:
    """Reserve one use for the duration of the block; refunded if the block fails."""
    rid = reserve(db, user, feature)
    try:
        yield rid
    except Exception:
        db.rollback()
        refund(db, rid)
        raise


def require_account(user: User | None, feature: str) -> User:
    if user is None:
        f = FEATURES[feature]
        raise SignInRequired(f"Create a free account to {f.action} — free accounts include {limit(feature, False)} {f.label} a month.",
                             details={"feature": feature, "free_limit": limit(feature, False)})  # fmt: skip
    return user


def anonymous_advisor(db: Session, request: Request) -> None:
    """Visitors can try the Advisor a few times a day before being asked to create a (free) account."""
    from app.services import ratelimit

    n = get_settings().anonymous_advisor_per_day
    try:
        ratelimit.hit(db, f"anon-advisor:{ratelimit.client_ip(request)}", n, timedelta(hours=24))
    except ratelimit.RateLimited as exc:
        raise SignInRequired(
            f"You've tried the AI Advisor {n} times today. Create a free account to keep going — it includes "
            f"{limit('advisor', False)} questions a month.", details={"feature": "advisor", "free_limit": limit("advisor", False)},
        ) from exc
