"""Nexis Pulse: anonymous community discussions and Nexis editorial discussions.

Reading is public. Writing (posting, replying, reacting, following, saving, reporting) needs a signed-in account,
and posting needs the current Terms accepted. Public responses never contain account information: community
authors are always ``{"display_name": "Anonymous"}``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import auth, pulse, ratelimit

router = APIRouter(tags=["pulse"])

Sort = Literal["latest", "trending", "discussed", "activity", "following", "for_you", "saved"]
Kind = Literal["community", "editorial", "public"]
Target = Literal["discussion", "comment"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscussionIn(_Base):
    title: str = Field(min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=6000)
    symbol: str | None = Field(default=None, max_length=32)
    stance: Literal["bullish", "bearish", "neutral", "question"] | None = None
    topics: list[str] = Field(default_factory=list, max_length=5)
    reddit_url: str | None = Field(default=None, max_length=600)  # a Reddit post a member wants to discuss (link only)


class CommentIn(_Base):
    body: str = Field(min_length=1, max_length=4000)
    parent_id: int | None = Field(default=None, ge=1)
    stance: Literal["agree", "disagree", "question"] | None = None


class ReactionIn(_Base):
    target_type: Target
    target_id: str = Field(min_length=1, max_length=20)
    kind: Literal["agree", "disagree", "interesting"]


class ToggleIn(_Base):
    on: bool | None = None


class ReportIn(_Base):
    target_type: Target
    target_id: str = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=20)
    detail: str | None = Field(default=None, max_length=500)


@router.get("/pulse/meta")
def meta() -> dict[str, Any]:
    return {
        "topics": [{"key": k, "label": v, "primary": k in pulse.PRIMARY_TOPICS} for k, v in pulse.TOPICS.items()],
        "max_topics": pulse.MAX_TOPICS,
        "stances": list(pulse.STANCES),
        "reactions": list(pulse.REACTIONS),
        "report_reasons": [{"key": k, "label": v} for k, v in pulse.REPORT_REASONS.items()],
        "sorts": list(pulse.SORTS),
        "disclaimer": pulse.DISCLAIMER,
    }


@router.get("/pulse/overview")
def overview(viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.overview(db, viewer)


@router.get("/pulse/feed")
def feed(
    sort: Sort = "latest",
    kind: Kind | None = None,
    topic: str | None = Query(default=None, max_length=32),
    symbol: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=80),
    cursor: str | None = Query(default=None, max_length=8),
    limit: int = Query(default=20, ge=1, le=50),
    viewer: User | None = Depends(auth.optional_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return pulse.feed(db, viewer, sort, kind, topic, symbol, q, cursor, limit)


@router.get("/pulse/topics")
def topics(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return pulse.topics_overview(db)


@router.get("/pulse/search")
def search(q: str = Query(min_length=1, max_length=80), viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.search(db, viewer, q)


@router.get("/pulse/assets/{symbol}")
def asset(symbol: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.asset_page(db, symbol[:32], viewer)


@router.get("/pulse/legacy/{post_id}")
def legacy(post_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Where an old (pre-anonymous) Pulse discussion link now lives."""
    return pulse.legacy(db, post_id)


@router.get("/pulse/discussions/{pid}")
def get(pid: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.detail(db, pid, viewer)


@router.get("/pulse/discussions/{pid}/comments")
def comments(
    pid: str, sort: Literal["old", "new", "top"] = "old", viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return pulse.comments(db, pid, viewer, sort)


@router.get("/pulse/discussions/{pid}/related")
def related(pid: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return pulse.related(db, pid, viewer)


@router.post("/pulse/discussions", status_code=201)
def create(req: DiscussionIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.create(db, user, req.title, req.body, req.symbol, req.stance, req.topics, req.reddit_url)


@router.delete("/pulse/discussions/{pid}", status_code=204)
def delete(pid: str, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> Response:
    pulse.delete_discussion(db, pid, user)
    return Response(status_code=204)


@router.post("/pulse/discussions/{pid}/comments", status_code=201)
def add_comment(pid: str, req: CommentIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.comment(db, pid, user, req.body, req.parent_id, req.stance)


@router.delete("/pulse/comments/{cid}", status_code=204)
def delete_comment(cid: int, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> Response:
    pulse.delete_comment(db, cid, user)
    return Response(status_code=204)


@router.post("/pulse/reactions")
def react(req: ReactionIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.react(db, user, req.target_type, req.target_id, req.kind)


@router.post("/pulse/discussions/{pid}/follow")
def follow(pid: str, req: ToggleIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.follow(db, user, pid, req.on)


@router.post("/pulse/discussions/{pid}/save")
def save(pid: str, req: ToggleIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.save(db, user, pid, req.on)


@router.post("/pulse/reports", status_code=201)
def report(req: ReportIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.report(db, user, req.target_type, req.target_id, req.reason, req.detail)


@router.get("/pulse/me/activity")
def my_activity(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """The signed-in member's own Pulse posts. Private: only ever returned to that member."""
    return pulse.my_activity(db, user)


# ------------------------------------------------------------------ evidence and the background engine


@router.get("/pulse/events")
def market_events(symbol: str | None = Query(default=None, max_length=32), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Recent sourced events. Only the public universe is listed, so nobody's private holdings show up here."""
    from app.services import events, market_pulse

    public = market_pulse.public_symbols(db)
    if symbol:
        sym = symbol.upper()
        rows = events.recent(db, [sym], days=30, limit=12) if sym in public else []
    else:
        rows = [e for e in events.important(db, limit=40) if e.provider != "nexis" and (not e.assets or set(e.assets) & public)][:8]
    return {"items": [events.serialize(e) for e in rows]}


@router.get("/pulse/public/status")
def public_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Which outside sources are collected, and how the last run went."""
    from app.services import public_discussions

    return public_discussions.status(db)


@router.get("/pulse/engine")
def engine_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services import pulse_engine

    return pulse_engine.status(db)


@router.post("/pulse/engine/tick")
def engine_tick(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Advance Pulse: anyone may nudge it (throttled server-side); the cron secret runs a larger batch."""
    from app.core.config import get_settings
    from app.services import pulse_engine

    secret = get_settings().cron_secret
    trusted = bool(secret) and request.headers.get("authorization") == f"Bearer {secret}"
    if not trusted:
        ratelimit.hit(db, f"pulse-tick:{ratelimit.client_ip(request)}", 20)
    out = pulse_engine.tick(db, max_assets=4 if trusted else 1, force=trusted)
    return out if trusted else {"skipped": out.get("skipped", False), "editorial": len(out.get("editorial") or [])}
