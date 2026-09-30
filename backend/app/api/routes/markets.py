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
from app.services import advisor, market_reports, markets, ratelimit, valuation
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
def compare_report(req: CompareRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(db, f"report:{ratelimit.client_ip(request)}", 20)
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
def valuation_report(symbol: str, req: ValuationRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(db, f"report:{ratelimit.client_ip(request)}", 20)
    return market_reports.generate_valuation(db, symbol, req.overrides, req.peers, req.title)


@router.get("/advisor/status")
def advisor_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {**advisor.status(db), "limit_per_hour": get_settings().advisor_requests_per_hour, "disclaimer": advisor.DISCLAIMER}


@router.post("/advisor/chat")
def advisor_chat(req: AdvisorRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(db, f"advisor:{ratelimit.client_ip(request)}", get_settings().advisor_requests_per_hour)
    return advisor.ask(db, [m.model_dump() for m in req.messages])


@router.post("/advisor/chat/stream")
def advisor_chat_stream(req: AdvisorRequest, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    """Server-sent events: ``status`` while data is fetched, ``delta`` chunks of the answer, then ``done`` with sources."""
    ratelimit.hit(db, f"advisor:{ratelimit.client_ip(request)}", get_settings().advisor_requests_per_hour)
    messages = [m.model_dump() for m in req.messages]

    def events() -> Iterator[str]:
        with db_session.SessionLocal() as s:
            try:
                for ev in advisor.stream(s, messages):
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
            except NexisError as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': exc.message})}\n\n"
            except Exception:
                log.exception("advisor stream failed")
                yield f"data: {json.dumps({'type': 'error', 'message': 'Something went wrong — please try again.'})}\n\n"

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
