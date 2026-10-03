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
    cursor: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=20, ge=1, le=50),
    viewer: User | None = Depends(auth.optional_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return pulse.listing(db, viewer, symbol, sort, sentiment, topic, author, cursor, limit)


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
