"""Nexis Pulse: asset discussions, the Pulse score, discovery and the AI summary."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import auth, pulse, pulse_score, pulse_sources, ratelimit

router = APIRouter(tags=["pulse"])

Sentiment = Literal["bullish", "neutral", "bearish"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscussionIn(_Base):
    symbol: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=6000)
    sentiment: Sentiment | None = None
    topics: list[str] = Field(default_factory=list, max_length=10)


class DiscussionPatch(_Base):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=6000)
    sentiment: Sentiment | None = None
    topics: list[str] | None = Field(default=None, max_length=10)


@router.get("/pulse/meta")
def meta() -> dict[str, Any]:
    """Topics people can pick, how the score is banded, and which discussion sources exist."""
    return {
        "topics": [{"key": k, "label": v} for k, v in pulse.TOPICS.items()],
        "max_topics": pulse.MAX_TOPICS,
        "bands": [{"up_to": top, "label": name} for top, name in pulse_score.BANDS],
        "minimum_discussions": pulse_score.MIN_DISCUSSIONS,
        "sources": pulse_sources.describe(),
        "disclaimer": pulse.DISCLAIMER,
    }


@router.get("/pulse/discover")
def discover(viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.discover(db, viewer)


@router.get("/pulse/discussions")
def discussions(
    symbol: str | None = Query(default=None, max_length=32),
    sort: Literal["new", "top"] = "new",
    sentiment: Sentiment | None = None,
    topic: str | None = Query(default=None, max_length=32),
    author: str | None = Query(default=None, max_length=30),
    cursor: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=20, ge=1, le=50),
    source: Literal["community", "research"] | None = None,
    viewer: User | None = Depends(auth.optional_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return pulse.listing(db, viewer, symbol, sort, sentiment, topic, author, cursor, limit, source)


@router.post("/pulse/discussions", status_code=201)
def create(req: DiscussionIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.create(db, user, req.symbol, req.title, req.body, req.sentiment, req.topics)


@router.get("/pulse/discussions/{pid}")
def get(pid: int, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.get(db, pid, viewer)


@router.patch("/pulse/discussions/{pid}")
def update(
    pid: int, req: DiscussionPatch, user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    fields = {k: getattr(req, k) for k in req.model_fields_set}
    return pulse.update(db, pid, user, **fields)


@router.delete("/pulse/discussions/{pid}", status_code=204)
def delete(pid: int, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> Response:
    pulse.delete(db, pid, user)
    return Response(status_code=204)


@router.get("/pulse/assets/{symbol}")
def asset(symbol: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.asset(db, symbol, viewer)


@router.get("/pulse/assets/{symbol}/summary")
def summary(symbol: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(db, f"pulse-summary:{ratelimit.client_ip(request)}", 60)
    return pulse.summary(db, symbol)


@router.get("/pulse/users/{username}")
def user(username: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.user_activity(db, username, viewer)


# ------------------------------------------------------------------ feed, search, people, events, engine


@router.get("/pulse/feed")
def feed(
    mode: Literal["latest", "trending", "following", "saved"] = "latest",
    topic: str | None = Query(default=None, max_length=32),
    source: Literal["community", "research", "generated"] | None = None,
    cursor: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=15, ge=1, le=40),
    viewer: User | None = Depends(auth.optional_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return pulse.feed(db, viewer, mode, topic, source, cursor, limit)


@router.get("/pulse/search")
def search(
    q: str = Query(min_length=1, max_length=80), viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return pulse.search(db, viewer, q)


@router.get("/pulse/events")
def market_events(symbol: str | None = Query(default=None, max_length=32), db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services import events

    rows = events.recent(db, [symbol.upper()], days=30, limit=12) if symbol else events.important(db, limit=8)
    return {"items": [events.serialize(e) for e in rows], "providers": [p.__dict__ for p in events.PROVIDERS.values()]}


@router.get("/pulse/personas/{username}")
def persona(username: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.persona_profile(db, username, viewer)


@router.get("/pulse/discussions/{pid}/related")
def related(pid: int, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return pulse.related(db, pid, viewer)


@router.get("/pulse/engine")
def engine_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services import pulse_engine

    return pulse_engine.status(db)


@router.post("/pulse/engine/tick")
def engine_tick(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Advance Pulse: anyone may nudge it (throttled, at most one thread); the cron secret runs a larger batch."""
    from app.core.config import get_settings
    from app.services import pulse_engine

    secret = get_settings().cron_secret
    trusted = bool(secret) and request.headers.get("authorization") == f"Bearer {secret}"
    if not trusted:
        ratelimit.hit(db, f"pulse-tick:{ratelimit.client_ip(request)}", 20)
    return pulse_engine.tick(db, max_threads=4 if trusted else 1, force=trusted)
