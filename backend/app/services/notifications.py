"""User-facing notifications for events that need attention or signal completion."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Notification

LEVELS = ("info", "success", "warning", "error")


def notify(db: Session, level: str, category: str, title: str, message: str, link: str | None = None) -> Notification:
    n = Notification(level=level if level in LEVELS else "info", category=category, title=title[:200], message=message, link=link)
    db.add(n)
    db.flush()
    return n


def list_notifications(db: Session, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    q = select(Notification).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    return [serialize(n) for n in db.scalars(q)]


def mark_read(db: Session, ids: list[int] | None) -> int:
    stmt = update(Notification).values(is_read=True)
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    res = db.execute(stmt)
    db.commit()
    return int(res.rowcount or 0)


def serialize(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "level": n.level,
        "category": n.category,
        "title": n.title,
        "message": n.message,
        "link": n.link,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat(),
    }
