"""Nexis Pulse: public investor opinions about an asset, and an AI summary of them."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import pulse, ratelimit

router = APIRouter(tags=["pulse"])


@router.get("/pulse/sources")
def sources() -> dict[str, Any]:
    return {"sources": pulse.sources_status(), "disclaimer": pulse.DISCLAIMER}


@router.get("/pulse/{symbol}")
def asset(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return pulse.collect(db, symbol)


@router.get("/pulse/{symbol}/synthesis")
def synthesis(symbol: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(db, f"pulse-ai:{ratelimit.client_ip(request)}", 40)
    return pulse.synthesis(db, symbol)
