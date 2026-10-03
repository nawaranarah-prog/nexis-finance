"""Personal alerts and notifications about the assets a member holds or follows.

``dispatch`` turns new ``SourceEvent`` rows into ``UserNotification`` rows for the members tracking the assets
involved, respecting each member's preferences and alert rules. Replies to a member's discussion or comment create
Pulse-activity notifications. Nothing is ever created for an asset the member doesn't track (except macro and
rate events, which are opt-in).

Delivery: everything appears in-app. Email digests are prepared by ``digest`` and only sent when an email provider
is configured; otherwise the digest is stored with status ``prepared`` and the interface says nothing was sent.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailed
from app.db.base import utcnow
from app.models import (
    AlertRule,
    Comment,
    DigestRecord,
    MarketCache,
    NotificationPreference,
    Post,
    SourceEvent,
    TopicFollow,
    User,
    UserHolding,
    UserNotification,
)

CATEGORIES = ("important", "earnings", "portfolio", "watchlist", "news", "pulse")
MODES = ("immediate", "daily", "weekly", "off")
DEFAULTS = {
    "important": "immediate",
    "earnings": "immediate",
    "portfolio": "immediate",
    "watchlist": "daily",
    "news": "daily",
    "pulse": "immediate",
}
EVENT_TYPES = ("news", "earnings", "dividend", "price_move", "filing", "regulation", "rates", "macro", "bond", "pulse")
# Event types that are opt-in because they aren't tied to an asset the member tracks.
OPT_IN = ("rates", "macro")
_KIND_TO_TYPE = {"earnings": "earnings", "dividend": "dividend", "price_move": "price_move", "rates": "rates", "macro": "macro",
                 "regulation": "regulation", "filing": "filing", "deal": "news", "news": "news", "research": "news"}  # fmt: skip
STATE = "alerts:last_event_id"


# ------------------------------------------------------------------ preferences and rules


def preferences(db: Session, user: User) -> NotificationPreference:
    p = db.get(NotificationPreference, user.id)
    if p is None:
        p = NotificationPreference(user_id=user.id, channels=dict(DEFAULTS), email_enabled=False, price_move_pct=5.0)
        db.add(p)
        db.commit()
    return p


def serialize_preferences(db: Session, user: User) -> dict[str, Any]:
    p = preferences(db, user)
    rules = list(db.scalars(select(AlertRule).where(AlertRule.user_id == user.id).order_by(AlertRule.id)))
    return {
        "channels": {c: p.channels.get(c, DEFAULTS[c]) for c in CATEGORIES},
        "email_enabled": p.email_enabled,
        "email_available": bool(get_settings().email_provider),
        "price_move_pct": p.price_move_pct,
        "rules": [
            {"id": r.id, "symbol": r.symbol, "event_type": r.event_type, "enabled": r.enabled, "threshold": r.threshold}
            for r in rules
        ],
        "event_types": list(EVENT_TYPES),
        "opt_in": list(OPT_IN),
    }


def update_preferences(
    db: Session, user: User, channels: dict[str, str] | None, email_enabled: bool | None, price_move_pct: float | None
) -> dict[str, Any]:
    p = preferences(db, user)
    if channels:
        bad = [k for k, v in channels.items() if k not in CATEGORIES or v not in MODES]
        if bad:
            raise ValidationFailed(f"unknown category or mode: {bad[0]}")
        p.channels = {**p.channels, **channels}
    if email_enabled is not None:
        p.email_enabled = email_enabled
    if price_move_pct is not None:
        if not 1 <= price_move_pct <= 50:
            raise ValidationFailed("price-move alerts must be between 1% and 50%")
        p.price_move_pct = price_move_pct
    db.commit()
    return serialize_preferences(db, user)


def set_rule(
    db: Session, user: User, symbol: str | None, event_type: str, enabled: bool, threshold: float | None
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise ValidationFailed("unknown event type")
    sym = symbol.strip().upper() if symbol else None
    r = db.scalars(select(AlertRule).where(AlertRule.user_id == user.id, AlertRule.event_type == event_type,
                                           AlertRule.symbol.is_(None) if sym is None else AlertRule.symbol == sym)).first()  # fmt: skip
    if r is None:
        r = AlertRule(user_id=user.id, symbol=sym, event_type=event_type)
        db.add(r)
    r.enabled, r.threshold = enabled, threshold
    db.commit()
    return serialize_preferences(db, user)


# ------------------------------------------------------------------ creating notifications


def _create(db: Session, **kw: Any) -> bool:
    try:
        with db.begin_nested():
            db.add(UserNotification(**kw))
        return True
    except IntegrityError:
        return False  # already notified about this


def _rule_allows(rules: dict[tuple[str | None, str], AlertRule], symbol: str | None, etype: str) -> tuple[bool, float | None]:
    for key in ((symbol, etype), (None, etype)):
        r = rules.get(key)
        if r is not None:
            return r.enabled, r.threshold
    return etype not in OPT_IN, None


def dispatch(db: Session) -> dict[str, int]:
    """Create notifications for events added since the last run."""
    row = db.get(MarketCache, STATE)
    last = int(row.payload.get("v", 0)) if row else 0
    new = list(db.scalars(select(SourceEvent).where(SourceEvent.id > last).order_by(SourceEvent.id).limit(500)))
    if not new:
        return {"events": 0, "notifications": 0}
    owners: dict[str, set[int]] = {}
    for uid, sym in db.execute(select(UserHolding.user_id, UserHolding.symbol)):
        owners.setdefault(sym, set()).add(uid)
    watchers: dict[str, set[int]] = {}
    for uid, sym in db.execute(select(TopicFollow.user_id, TopicFollow.value).where(TopicFollow.kind == "symbol")):
        watchers.setdefault(sym, set()).add(uid)
    involved = set().union(*owners.values(), *watchers.values()) if (owners or watchers) else set()
    opt_in_users = set(db.scalars(select(AlertRule.user_id).where(AlertRule.event_type.in_(OPT_IN), AlertRule.enabled.is_(True))))
    prefs = {
        p.user_id: p
        for p in db.scalars(select(NotificationPreference).where(NotificationPreference.user_id.in_(involved | opt_in_users)))
    }
    rules: dict[int, dict[tuple[str | None, str], AlertRule]] = {}
    for r in db.scalars(select(AlertRule).where(AlertRule.user_id.in_(involved | opt_in_users))):
        rules.setdefault(r.user_id, {})[(r.symbol, r.event_type)] = r

    made = 0
    for e in new:
        etype = _KIND_TO_TYPE.get(e.kind, "news")
        facts = e.facts or {}
        targets: list[tuple[int, str | None, bool]] = []
        for sym in e.assets or []:
            for uid in owners.get(sym, set()):
                targets.append((uid, sym, True))
            for uid in watchers.get(sym, set()) - owners.get(sym, set()):
                targets.append((uid, sym, False))
        if not e.assets and etype in OPT_IN:
            targets = [(uid, None, False) for uid in opt_in_users]
        done: set[int] = set()
        for uid, sym, owned in targets:
            if uid in done:
                continue
            allowed, threshold = _rule_allows(rules.get(uid, {}), sym, etype)
            if not allowed:
                continue
            pref = prefs.get(uid)
            move = abs(float(facts.get("change_pct") or 0))
            if etype == "price_move" and move < (threshold or (pref.price_move_pct if pref else 5.0)):
                continue
            severity = (
                "high"
                if (etype == "price_move" and move >= 8) or (owned and etype in ("earnings", "dividend"))
                else ("notable" if etype in ("earnings", "dividend", "price_move", "rates", "regulation") else "info")
            )
            category = (
                "important"
                if severity == "high"
                else (
                    "earnings"
                    if etype in ("earnings", "dividend")
                    else ("portfolio" if owned else ("watchlist" if sym else "news"))
                )
            )
            mode = (pref.channels if pref else DEFAULTS).get(category, DEFAULTS[category])
            if mode == "off":
                continue
            if _create(db, user_id=uid, category=category, event_type=etype, severity=severity, symbol=sym, title=e.title[:300],
                       body=(facts.get("summary") or facts.get("headline") or "")[:1200] or None,
                       source_name=e.publisher, source_url=e.url, link=f"/pulse/ticker/{sym}" if sym else "/pulse",
                       event_id=e.id, dedupe_key=f"event:{e.id}:{sym or '-'}"):  # fmt: skip
                made += 1
            done.add(uid)
    v = {"v": new[-1].id}
    if row is None:
        db.add(MarketCache(key=STATE, payload=v, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = v, utcnow()
    db.commit()
    return {"events": len(new), "notifications": made}


def notify_reply(db: Session, comment: Comment) -> None:
    """A member's discussion or comment got a reply from someone else."""
    post = db.get(Post, comment.post_id)
    if post is None:
        return
    author = db.get(User, comment.user_id)
    who = author.display_name or author.username if author else "Someone"
    targets = {post.user_id: f"{who} replied to your discussion"}
    if comment.parent_id:
        parent = db.get(Comment, comment.parent_id)
        if parent is not None:
            targets[parent.user_id] = f"{who} replied to your comment"
    for uid, title in targets.items():
        if uid == comment.user_id:
            continue
        u = db.get(User, uid)
        if u is None or u.kind != "person":
            continue
        mode = preferences(db, u).channels.get("pulse", DEFAULTS["pulse"])
        if mode == "off":
            continue
        _create(db, user_id=uid, category="pulse", event_type="pulse", severity="info", symbol=post.asset, title=title[:300],
                body=comment.body[:400], link=f"/pulse/discussion/{post.id}#c{comment.id}", dedupe_key=f"comment:{comment.id}")  # fmt: skip
    db.commit()


# ------------------------------------------------------------------ reading


def serialize(n: UserNotification) -> dict[str, Any]:
    return {
        "id": n.id, "category": n.category, "event_type": n.event_type, "severity": n.severity, "symbol": n.symbol,
        "title": n.title, "body": n.body, "source_name": n.source_name, "source_url": n.source_url, "link": n.link,
        "created_at": n.created_at.isoformat() + "Z", "read": n.read_at is not None,
    }  # fmt: skip


def listing(db: Session, user: User, unread_only: bool = False, before: int | None = None, limit: int = 30) -> dict[str, Any]:
    q = select(UserNotification).where(UserNotification.user_id == user.id)
    if unread_only:
        q = q.where(UserNotification.read_at.is_(None))
    if before:
        q = q.where(UserNotification.id < before)
    rows = list(db.scalars(q.order_by(UserNotification.id.desc()).limit(limit + 1)))
    unread = (
        db.scalar(
            select(func.count(UserNotification.id)).where(UserNotification.user_id == user.id, UserNotification.read_at.is_(None))
        )
        or 0
    )
    return {
        "items": [serialize(n) for n in rows[:limit]],
        "next": rows[limit - 1].id if len(rows) > limit else None,
        "unread": unread,
    }


def mark_read(db: Session, user: User, ids: list[int] | None) -> int:
    q = db.query(UserNotification).filter(UserNotification.user_id == user.id, UserNotification.read_at.is_(None))
    if ids is not None:
        q = q.filter(UserNotification.id.in_(ids))
    n = q.update({UserNotification.read_at: utcnow()}, synchronize_session=False)
    db.commit()
    return int(n)


# ------------------------------------------------------------------ digests (email architecture)


class EmailSender:
    """Interface for an email provider. None is connected; ``configured`` returns False and nothing is sent."""

    name = "none"

    def configured(self) -> bool:
        return False

    def send(self, to: str, subject: str, html: str, text: str) -> None:  # pragma: no cover - no provider yet
        raise NotImplementedError("no email provider is connected")


def sender() -> EmailSender:
    return EmailSender()  # a provider (e.g. one with a free tier) plugs in here once it is chosen


def build_digest(db: Session, user: User, period: str = "daily") -> dict[str, Any]:
    if period not in ("daily", "weekly"):
        raise ValidationFailed("period must be daily or weekly")
    since = utcnow() - timedelta(days=1 if period == "daily" else 7)
    rows = list(db.scalars(select(UserNotification).where(UserNotification.user_id == user.id, UserNotification.created_at >= since)
                           .order_by(UserNotification.created_at.desc()).limit(60)))  # fmt: skip
    by_asset: dict[str, list[dict[str, Any]]] = {}
    for n in rows:
        by_asset.setdefault(n.symbol or "Markets", []).append(serialize(n))
    from app.services import pulse

    tracked = [h.symbol for h in db.scalars(select(UserHolding).where(UserHolding.user_id == user.id))]
    tracked += list(db.scalars(select(TopicFollow.value).where(TopicFollow.user_id == user.id, TopicFollow.kind == "symbol")))
    top: list[dict[str, Any]] = []
    for sym in list(dict.fromkeys(tracked))[:6]:
        top += pulse.listing(db, user, symbol=sym, sort="top", limit=1)["items"]
    return {
        "subject": f"Nexis {'Daily' if period == 'daily' else 'Weekly'} — your portfolio and watchlist",
        "period": period,
        "assets": [{"symbol": k, "items": v[:5]} for k, v in by_asset.items()],
        "top_discussions": [
            {"id": d["id"], "title": d["title"], "asset": d["asset"], "source": d["source"]["label"]} for d in top[:3]
        ],
        "empty": not rows and not top,
    }


def send_digest(db: Session, user: User, period: str) -> DigestRecord:
    content = build_digest(db, user, period)
    s = sender()
    if not s.configured():
        status, detail = "prepared", "No email provider is connected, so nothing was sent."
    else:  # pragma: no cover - no provider yet
        status, detail = "sent", None
    rec = DigestRecord(user_id=user.id, period=period, subject=content["subject"], content=content, status=status, detail=detail)
    db.add(rec)
    db.commit()
    return rec


def digests(db: Session, user: User) -> list[dict[str, Any]]:
    rows = db.scalars(select(DigestRecord).where(DigestRecord.user_id == user.id).order_by(DigestRecord.id.desc()).limit(10))
    return [{"id": r.id, "period": r.period, "subject": r.subject, "status": r.status, "detail": r.detail,
             "created_at": r.created_at.isoformat() + "Z"} for r in rows]  # fmt: skip


def get_notification(db: Session, user: User, nid: int) -> UserNotification:
    n = db.get(UserNotification, nid)
    if n is None or n.user_id != user.id:
        raise NotFoundError("notification not found")
    return n


def run_digests(db: Session) -> dict[str, int]:
    """Prepare daily digests (and weekly ones on Mondays) for members who switched email on."""
    weekly = utcnow().weekday() == 0
    made = 0
    for pref in list(db.scalars(select(NotificationPreference).where(NotificationPreference.email_enabled.is_(True)))):
        u = db.get(User, pref.user_id)
        if u is None or u.kind != "person":
            continue
        modes = set(pref.channels.values())
        for period in ("daily", "weekly"):
            if period in modes and (period == "daily" or weekly):
                send_digest(db, u, period)
                made += 1
    return {"prepared": made, "email_provider": int(sender().configured())}
