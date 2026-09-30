"""Sliding-window rate limits stored in the database (works across serverless instances)."""

from __future__ import annotations

from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import NexisError
from app.db.base import utcnow
from app.models import RateEvent


class RateLimited(NexisError):
    status_code = 429
    code = "rate_limited"


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def hit(db: Session, bucket: str, limit: int, window: timedelta = timedelta(hours=1)) -> None:
    """Record one event in ``bucket``; raise :class:`RateLimited` if the window already holds ``limit``."""
    now = utcnow()
    since = now - window
    n = db.scalar(select(func.count(RateEvent.id)).where(RateEvent.bucket == bucket, RateEvent.created_at >= since)) or 0
    if n >= limit:
        minutes = max(1, int(window.total_seconds() // 60))
        raise RateLimited(f"too many requests — the limit is {limit} per {minutes} minutes; try again later")
    db.add(RateEvent(bucket=bucket, created_at=now))
    db.execute(delete(RateEvent).where(RateEvent.created_at < now - timedelta(days=2)))
    db.commit()
