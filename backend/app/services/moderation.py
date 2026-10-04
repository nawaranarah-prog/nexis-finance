"""Pulse moderation: the review queue and moderator actions (flag → review → action).

Moderators work on content, not on people. The queue shows the post, why it is there (automatic flags, member
reports and their reasons) and how many of the author's earlier posts were removed — but never who the author is.
Actions that affect the account (pausing someone's posting) are applied server-side through the content.

Every decision is written to ``moderation_actions`` and resolves the open reports on that content.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFoundError, ValidationFailed
from app.db.base import utcnow
from app.models import ContentReport, ModerationAction, PulseComment, PulseDiscussion, User
from app.services import pulse

ACTIONS = ("approve", "remove", "restore", "lock", "unlock", "dismiss", "suspend_author")


def require_moderator(user: User) -> None:
    if user.role not in ("moderator", "admin"):
        raise Forbidden("moderators only")


def _obj(db: Session, target_type: str, target_id: int) -> PulseDiscussion | PulseComment:
    model = {"discussion": PulseDiscussion, "comment": PulseComment}.get(target_type)
    obj = db.get(model, target_id) if model else None
    if obj is None:
        raise NotFoundError("content not found")
    return obj


def _history(db: Session, author_id: int | None) -> dict[str, Any]:
    if author_id is None:
        return {"prior_removals": 0, "posting_paused": False}
    removed = (db.scalar(select(func.count(PulseDiscussion.id)).where(PulseDiscussion.author_id == author_id, PulseDiscussion.status == "removed")) or 0) + (
        db.scalar(select(func.count(PulseComment.id)).where(PulseComment.author_id == author_id, PulseComment.status == "removed")) or 0)  # fmt: skip
    u = db.get(User, author_id)
    return {"prior_removals": removed, "posting_paused": bool(u and u.posting_suspended_until and u.posting_suspended_until > utcnow())}


def _entry(db: Session, target_type: str, obj: Any, reports: list[ContentReport]) -> dict[str, Any]:
    if target_type == "discussion":
        d = obj
        content = {"title": d.title, "body": d.body, "kind": d.kind, "url": pulse.url_for(d)}
    else:
        d = db.get(PulseDiscussion, obj.discussion_id)
        content = {"body": obj.body, "discussion": {"title": d.title if d else None, "url": pulse.url_for(d) if d else None}}
    return {
        "target_type": target_type, "target_id": obj.id, "status": obj.status, "flagged": obj.flagged, "flags": obj.flags or [],
        "locked": getattr(obj, "locked", None), "created_at": obj.created_at.isoformat() + "Z", "content": content,
        "reports": [{"reason": r.reason, "label": pulse.REPORT_REASONS.get(r.reason, r.reason), "detail": r.detail,
                     "created_at": r.created_at.isoformat() + "Z"} for r in reports],
        "author": {"display_name": "Anonymous", **_history(db, obj.author_id)},
    }


def queue(db: Session, moderator: User, limit: int = 50) -> dict[str, Any]:
    require_moderator(moderator)
    targets: dict[tuple[str, int], list[ContentReport]] = {}
    for r in db.scalars(select(ContentReport).where(ContentReport.status == "open").order_by(ContentReport.created_at).limit(500)):
        targets.setdefault((r.target_type, r.target_id), []).append(r)
    for d in db.scalars(select(PulseDiscussion).where((PulseDiscussion.status == "held") | ((PulseDiscussion.status == "visible") & PulseDiscussion.flagged.is_(True))).limit(200)):
        targets.setdefault(("discussion", d.id), [])
    for c in db.scalars(select(PulseComment).where((PulseComment.status == "held") | ((PulseComment.status == "visible") & PulseComment.flagged.is_(True))).limit(200)):
        targets.setdefault(("comment", c.id), [])
    items = []
    for (tt, tid), reports in targets.items():
        try:
            obj = _obj(db, tt, tid)
        except NotFoundError:
            continue
        if obj.status in ("deleted",):
            continue
        items.append(_entry(db, tt, obj, reports))
    # Held content first (it is invisible until someone decides), then by number of reports.
    items.sort(key=lambda i: (i["status"] != "held", -len(i["reports"]), i["created_at"]))
    return {"items": items[:limit], "total": len(items)}


def log(db: Session, moderator: User, limit: int = 100) -> list[dict[str, Any]]:
    require_moderator(moderator)
    rows = db.scalars(select(ModerationAction).order_by(ModerationAction.created_at.desc()).limit(limit))
    return [{"id": a.id, "target_type": a.target_type, "target_id": a.target_id, "action": a.action, "reason": a.reason,
             "by": "automatic" if a.actor_id is None else "moderator", "created_at": a.created_at.isoformat() + "Z"} for a in rows]  # fmt: skip


def _recount(db: Session, d: PulseDiscussion) -> None:
    db.flush()  # sessions don't autoflush; count what was just changed
    d.comment_count = db.scalar(select(func.count(PulseComment.id)).where(PulseComment.discussion_id == d.id, PulseComment.status == "visible")) or 0
    d.participant_count = pulse._participants(db, d)


def act(db: Session, moderator: User, target_type: str, target_id: int, action: str, reason: str | None, days: int | None = None) -> dict[str, Any]:
    require_moderator(moderator)
    if action not in ACTIONS:
        raise ValidationFailed("unknown moderation action")
    obj = _obj(db, target_type, target_id)
    was_visible = obj.status == "visible"
    resolution = "actioned"
    if action == "approve":
        if obj.status not in ("held", "visible"):
            raise ValidationFailed("only content waiting for review can be approved")
        obj.status, obj.flagged = "visible", False
        resolution = "dismissed"
    elif action == "remove":
        obj.status = "removed"
    elif action == "restore":
        if obj.status != "removed":
            raise ValidationFailed("only removed content can be restored")
        obj.status = "visible"
    elif action in ("lock", "unlock"):
        if target_type != "discussion":
            raise ValidationFailed("only discussions can be locked")
        obj.locked = action == "lock"  # type: ignore[union-attr]
    elif action == "dismiss":
        obj.flagged = False
        resolution = "dismissed"
    elif action == "suspend_author":
        if obj.author_id is None:
            raise ValidationFailed("this content has no member author")
        author = db.get(User, obj.author_id)
        if author is not None:
            author.posting_suspended_until = utcnow() + timedelta(days=max(1, min(int(days or 7), 365)))
    d = obj if target_type == "discussion" else db.get(PulseDiscussion, obj.discussion_id)  # type: ignore[union-attr]
    if target_type == "comment" and d is not None and was_visible != (obj.status == "visible"):
        _recount(db, d)
        if obj.status == "visible":
            d.last_activity_at = max(d.last_activity_at, obj.created_at)
    db.add(ModerationAction(target_type=target_type, target_id=obj.id, action=action, reason=(reason or "")[:300] or None,
                            actor_id=moderator.id, details={"days": days} if action == "suspend_author" else {}, created_at=utcnow()))  # fmt: skip
    for r in db.scalars(select(ContentReport).where(ContentReport.target_type == target_type, ContentReport.target_id == obj.id,
                                                    ContentReport.status == "open")):  # fmt: skip
        r.status, r.resolved_at, r.resolver_id = resolution, utcnow(), moderator.id
    db.commit()
    if action == "approve" and not was_visible and target_type == "discussion":
        from app.services import alerts

        alerts.notify_new_discussion(db, obj)  # type: ignore[arg-type]
    return {"target_type": target_type, "target_id": obj.id, "status": obj.status, "flagged": obj.flagged,
            "locked": getattr(obj, "locked", None)}  # fmt: skip
