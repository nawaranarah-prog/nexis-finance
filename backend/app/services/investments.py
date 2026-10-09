"""Investment allowances across plan changes: archive, restore, and the member's downgrade choice.

An *investment* is an asset held in My Nexis (every lot of the same symbol is one investment). Each plan allows a
number of *active* investments (``plans.LIMITS[tier]["investments"]``). Nothing here ever deletes a holding:

* **Archived** holdings stay in the account, read-only, and don't count toward the allowance. A member can archive
  and restore their own (``archive`` / ``restore``), within the allowance.
* **Downgrades** (cancellation reaching its end, a move from Pro to Plus, a failed renewal that ends the
  subscription): before the change the member picks which investments to keep (``choose_keep``). The choice is
  applied by ``reconcile`` only once the plan has actually dropped — i.e. the payment provider's subscription state
  says so. Nexis never picks for them: with no choice on file, nothing is archived; the holdings stay visible and
  portfolio changes (adding or editing investments) wait until the member chooses (``selection_required``).
* **Upgrades / resubscribing**: investments archived because of an earlier downgrade (reason "plan") are restored
  automatically, oldest first, up to the new allowance. Ones the member archived themselves stay archived.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import plans
from app.core.errors import NexisError, ValidationFailed
from app.db.base import utcnow
from app.models import Subscription, User, UserHolding, UserNotification
from app.services import entitlements, markets


class SelectionRequired(NexisError):
    status_code = 409
    code = "investment_selection_required"


def _holdings(db: Session, user: User, active: bool | None = None) -> list[UserHolding]:
    q = select(UserHolding).where(UserHolding.user_id == user.id)
    if active is True:
        q = q.where(UserHolding.archived_at.is_(None))
    elif active is False:
        q = q.where(UserHolding.archived_at.is_not(None))
    return list(db.scalars(q.order_by(UserHolding.id)))


def active_symbols(db: Session, user: User) -> list[str]:
    """Distinct active symbols, in the order they were first added."""
    return list(dict.fromkeys(h.symbol for h in _holdings(db, user, active=True)))


def archived_symbols(db: Session, user: User) -> list[str]:
    act = set(active_symbols(db, user))
    return [s for s in dict.fromkeys(h.symbol for h in _holdings(db, user, active=False)) if s not in act]


def _sub(db: Session, user: User) -> Subscription | None:
    return entitlements.subscription(db, user)


def target_tier(db: Session, user: User) -> str:
    """The plan the account is heading to: Free once a scheduled cancellation ends, otherwise the current plan."""
    sub = _sub(db, user)
    tier = entitlements.sub_tier(sub)
    if sub is not None and tier != "free" and sub.cancel_at_period_end:
        return "free"
    return tier


def state(db: Session, user: User) -> dict[str, Any]:
    """Everything the UI and the Help Agent need about the member's allowance."""
    tier = entitlements.tier_of(db, user)
    cap = entitlements.limit("investments", tier)
    act = active_symbols(db, user)
    target = target_tier(db, user)
    target_cap = entitlements.limit("investments", target)
    sub = _sub(db, user)
    keep = [s for s in (sub.downgrade_keep or []) if s in act] if sub else []
    return {
        "plan": tier, "plan_name": plans.NAMES[tier], "active": len(act), "limit": cap, "symbols": act,
        "archived": archived_symbols(db, user),
        "over_limit": len(act) > cap,
        "selection_required": len(act) > cap,  # the plan already dropped and no choice was applied
        "upcoming": {"plan": target, "plan_name": plans.NAMES[target], "limit": target_cap,
                     "ends_at": sub.current_period_end.isoformat() + "Z" if sub and sub.current_period_end and target != tier else None,
                     "choice_needed": target != tier and len(act) > target_cap and not keep} if target != tier else None,
        "keep": keep,
    }  # fmt: skip


def ensure_editable(db: Session, user: User) -> None:
    """Portfolio changes wait while the account holds more active investments than its plan allows."""
    st = state(db, user)
    if st["selection_required"]:
        raise SelectionRequired(
            f"Your {st['plan_name']} plan includes {st['limit']} active investment{'s' if st['limit'] != 1 else ''}. "
            f"Choose which to keep active — the others are archived, not deleted.",
            details={"limit": st["limit"], "symbols": st["symbols"], "plan": st["plan"]},
        )


def _clean(symbols: list[str]) -> list[str]:
    return list(dict.fromkeys(markets.clean_symbol(s) for s in symbols if isinstance(s, str) and s.strip()))


def choose_keep(db: Session, user: User, symbols: list[str]) -> dict[str, Any]:
    """Record which investments stay active after the allowance shrinks; applied now if the plan already dropped."""
    keep = _clean(symbols)
    act = active_symbols(db, user)
    unknown = [s for s in keep if s not in act]
    if unknown:
        raise ValidationFailed(f"{', '.join(unknown)} isn't one of your active investments")
    tier = entitlements.tier_of(db, user)
    target = target_tier(db, user)
    cap = min(entitlements.limit("investments", tier), entitlements.limit("investments", target))
    if not keep or len(keep) > cap:
        raise ValidationFailed(f"Choose between 1 and {cap} investment{'s' if cap != 1 else ''} to keep active")
    sub = _sub(db, user)
    if sub is None:
        sub = Subscription(user_id=user.id, provider="stripe")
        db.add(sub)
    sub.downgrade_keep = keep
    db.commit()
    reconcile(db, user)
    return state(db, user)


def _archive(rows: list[UserHolding], reason: str) -> None:
    now = utcnow()
    for h in rows:
        h.archived_at, h.archived_reason = now, reason


def archive(db: Session, user: User, symbol: str) -> dict[str, Any]:
    sym = markets.clean_symbol(symbol)
    rows = [h for h in _holdings(db, user, active=True) if h.symbol == sym]
    if not rows:
        raise ValidationFailed("that investment isn't active")
    _archive(rows, "member")
    db.commit()  # no reconcile here: the freed slot is the member's to use, not for an automatic restore
    return state(db, user)


def restore(db: Session, user: User, symbol: str) -> dict[str, Any]:
    sym = markets.clean_symbol(symbol)
    rows = [h for h in _holdings(db, user, active=False) if h.symbol == sym]
    if not rows:
        raise ValidationFailed("that investment isn't archived")
    ensure_editable(db, user)
    entitlements.check_capacity(db, user, "investments")
    for h in rows:
        h.archived_at = h.archived_reason = None
    db.commit()
    return state(db, user)


def reconcile(db: Session, user: User) -> dict[str, Any]:
    """Bring active investments in line with the plan the provider confirmed. Idempotent; safe to call any time.

    Over the allowance: apply the member's saved choice (archive the rest), or — with no valid choice — change
    nothing and ask them to choose. Under the allowance: restore investments archived by an earlier downgrade.
    """
    tier = entitlements.tier_of(db, user)
    cap = entitlements.limit("investments", tier)
    act = active_symbols(db, user)
    sub = _sub(db, user)
    out: dict[str, Any] = {"archived": [], "restored": [], "selection_required": False}
    if len(act) > cap:
        keep = [s for s in (sub.downgrade_keep or []) if s in act][:cap] if sub else []
        if keep:
            gone = [h for h in _holdings(db, user, active=True) if h.symbol not in keep]
            _archive(gone, "plan")
            out["archived"] = list(dict.fromkeys(h.symbol for h in gone))
            sub.downgrade_keep = None  # type: ignore[union-attr]
            db.commit()
        else:
            out["selection_required"] = True
            _remind(db, user, tier, cap)
        return out
    room = cap - len(act)
    if room > 0:
        waiting = [h for h in _holdings(db, user, active=False) if h.archived_reason == "plan"]
        for sym in list(dict.fromkeys(h.symbol for h in waiting))[:room]:
            for h in waiting:
                if h.symbol == sym:
                    h.archived_at = h.archived_reason = None
            out["restored"].append(sym)
        if out["restored"]:
            db.commit()
    return out


def _remind(db: Session, user: User, tier: str, cap: int) -> None:
    from sqlalchemy.exc import IntegrityError

    try:
        with db.begin_nested():
            db.add(UserNotification(
                user_id=user.id, category="important", event_type="billing", severity="notable",
                title="Choose which investments to keep active",
                body=f"Your {plans.NAMES[tier]} plan includes {cap} active investment{'s' if cap != 1 else ''}. Choose which to keep; "
                     "the others are archived, not deleted, and come back if you upgrade.",
                link="/my-nexis#plan-allowance", dedupe_key=f"investments-select:{tier}:{utcnow():%Y-%m}", delivery="immediate",
            ))  # fmt: skip
        db.commit()
    except IntegrityError:
        db.rollback()


def remind_before_downgrade(db: Session, user: User) -> None:
    """Called when a cancellation or a move to a smaller plan is scheduled: ask early, so nothing is a surprise."""
    st = state(db, user)
    up = st["upcoming"]
    if not up or not up["choice_needed"]:
        return
    from sqlalchemy.exc import IntegrityError

    try:
        with db.begin_nested():
            db.add(UserNotification(
                user_id=user.id, category="important", event_type="billing", severity="notable",
                title="Choose which investments stay active",
                body=f"From {up['ends_at'][:10] if up['ends_at'] else 'the end of your billing period'} your plan includes "
                     f"{up['limit']} active investment{'s' if up['limit'] != 1 else ''}. Choose which to keep — the others will be "
                     "archived, not deleted.",
                link="/my-nexis#plan-allowance", dedupe_key=f"investments-upcoming:{up['plan']}:{up['ends_at']}"[:120], delivery="immediate",
            ))  # fmt: skip
        db.commit()
    except IntegrityError:
        db.rollback()
