"""Global markets, comparisons, valuation, the AI advisor and their PDF reports."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError
from app.core.logging import get_logger
from app.db import session as db_session
from app.db.session import get_db
from app.models import User
from app.services import advisor, auth, entitlements, market_reports, markets, ratelimit, valuation
from app.services import compare as cmp

router = APIRouter(tags=["markets"])
log = get_logger(__name__)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompareRequest(_Base):
    symbols: list[str] = Field(min_length=1, max_length=8)
    period: str | None = "1y"
    start: date | None = None
    end: date | None = None
    interval: Literal["1h", "1d", "1wk", "1mo"] | None = None
    bucket: Literal["hour", "day", "week", "month", "quarter", "year"] | None = None
    risk_free_rate: float | None = Field(default=None, ge=-0.05, le=0.3)
    title: str | None = Field(default=None, max_length=160)


class ValuationRequest(_Base):
    overrides: dict[str, float] | None = None
    peers: list[str] | None = Field(default=None, max_length=8)
    title: str | None = Field(default=None, max_length=160)


class ChatMessage(_Base):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AdvisorRequest(_Base):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)
    language: Literal["en", "ar"] | None = None


@router.get("/markets/search")
def search(q: str = Query(min_length=1, max_length=80), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return markets.search(db, q)


@router.get("/markets/lists")
def lists() -> list[dict[str, str]]:
    return [{"key": k, "label": v["label"], "note": v["note"]} for k, v in markets.LISTS.items()]


@router.get("/markets/lists/{key}")
def market_list(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return markets.market_list(db, key)


@router.get("/markets/quotes")
def quotes(symbols: str = Query(min_length=1, max_length=600), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return markets.quotes(db, [s for s in symbols.split(",") if s.strip()])


@router.get("/markets/instruments/{symbol}")
def instrument(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return markets.details(db, symbol)


@router.get("/markets/instruments/{symbol}/history")
def history(
    symbol: str,
    period: str | None = None,
    start: date | None = None,
    end: date | None = None,
    interval: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    s, e, iv = markets.resolve_window(period, start, end, interval)
    return markets.history(db, symbol, iv, s, e)


@router.get("/markets/instruments/{symbol}/statements")
def statements(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return markets.statements(db, symbol)


@router.get("/markets/instruments/{symbol}/news")
def news(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return markets.news(db, symbol)


@router.get("/markets/instruments/{symbol}/peers")
def peers(symbol: str, db: Session = Depends(get_db)) -> list[str]:
    return markets.peers(db, symbol)


@router.post("/markets/compare")
def compare(req: CompareRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return cmp.compare(db, req.symbols, req.period, req.start, req.end, req.interval, req.bucket, req.risk_free_rate)


@router.post("/markets/compare/report", status_code=201)
def compare_report(
    req: CompareRequest, request: Request, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """PDF comparison report (with an AI-written narrative): a metered feature, counted per account."""
    user = entitlements.require_account(viewer, "report")
    ratelimit.hit(db, f"report:{ratelimit.client_ip(request)}", 20)
    with entitlements.metered(db, user, "report"):
        return market_reports.generate_comparison(
            db, req.symbols, req.period, req.start, req.end, req.interval, req.bucket, req.title
        )


@router.get("/valuation/{symbol}")
def valuation_default(symbol: str, peers: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return valuation.run(db, symbol, None, [p for p in peers.split(",") if p.strip()] if peers else None)


@router.post("/valuation/{symbol}")
def valuation_custom(symbol: str, req: ValuationRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return valuation.run(db, symbol, req.overrides, req.peers)


@router.post("/valuation/{symbol}/report", status_code=201)
def valuation_report(
    symbol: str, req: ValuationRequest, request: Request, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """PDF valuation report: a metered feature, counted per account."""
    user = entitlements.require_account(viewer, "report")
    ratelimit.hit(db, f"report:{ratelimit.client_ip(request)}", 20)
    with entitlements.metered(db, user, "report"):
        return market_reports.generate_valuation(db, symbol, req.overrides, req.peers, req.title)


@router.get("/advisor/status")
def advisor_status(viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    usage = entitlements.usage(db, viewer)["advisor"] if viewer else None
    return {**advisor.status(db), "limit_per_hour": get_settings().advisor_requests_per_hour, "disclaimer": advisor.DISCLAIMER,
            "usage": usage, "plan": ("pro" if entitlements.has_pro(db, viewer) else "free") if viewer else None,
            "anonymous_per_day": get_settings().anonymous_advisor_per_day}  # fmt: skip


def _advisor_gate(db: Session, request: Request, viewer: User | None) -> int | None:
    """Burst protection per IP, then the plan allowance: returns the usage reservation (None for visitors)."""
    ratelimit.hit(db, f"advisor:{ratelimit.client_ip(request)}", get_settings().advisor_requests_per_hour)
    if viewer is None:
        entitlements.anonymous_advisor(db, request)
        return None
    return entitlements.reserve(db, viewer, "advisor")


@router.post("/advisor/chat")
def advisor_chat(
    req: AdvisorRequest, request: Request, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    rid = _advisor_gate(db, request, viewer)
    try:
        return advisor.ask(db, [m.model_dump() for m in req.messages], req.language)
    except Exception:
        db.rollback()
        entitlements.refund(db, rid)
        raise


@router.post("/advisor/chat/stream")
def advisor_chat_stream(
    req: AdvisorRequest, request: Request, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> StreamingResponse:
    """Server-sent events: ``status`` while data is fetched, ``delta`` chunks of the answer, then ``done`` with sources.

    The plan allowance is checked before streaming starts; a question that fails before any answer arrives is refunded.
    """
    rid = _advisor_gate(db, request, viewer)
    messages = [m.model_dump() for m in req.messages]

    def events() -> Iterator[str]:
        answered = False
        with db_session.SessionLocal() as s:
            try:
                for ev in advisor.stream(s, messages, req.language):
                    answered = answered or ev.get("type") == "delta"
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
            except NexisError as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': exc.message})}\n\n"
            except Exception:
                log.exception("advisor stream failed")
                yield f"data: {json.dumps({'type': 'error', 'message': 'Something went wrong — please try again.'})}\n\n"
            finally:
                if not answered:
                    s.rollback()
                    entitlements.refund(s, rid)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
