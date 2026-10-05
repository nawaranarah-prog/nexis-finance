"""The signed-in member's private area: portfolio, watchlist, intelligence, notifications and digests.

Every endpoint requires a session and only ever reads or writes the caller's own rows.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import alerts, auth, portfolio, ratelimit

router = APIRouter(tags=["me"])
AssetType = Literal["stock", "etf", "fund", "bond", "crypto", "other"]
Mode = Literal["immediate", "daily", "weekly", "off"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HoldingIn(_Base):
    symbol: str = Field(min_length=1, max_length=32)
    quantity: float = Field(gt=0)
    purchase_price: float | None = Field(default=None, ge=0)
    purchase_date: date | None = None
    asset_type: AssetType | None = None
    portfolio: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=300)


class HoldingPatch(_Base):
    quantity: float | None = Field(default=None, gt=0)
    purchase_price: float | None = Field(default=None, ge=0)
    purchase_date: date | None = None
    asset_type: AssetType | None = None
    portfolio: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=300)


class ReadIn(_Base):
    ids: list[int] | None = Field(default=None, max_length=500)


class PreferencesIn(_Base):
    channels: dict[Literal["important", "earnings", "portfolio", "watchlist", "news", "pulse", "discussions"], Mode] | None = None
    email_enabled: bool | None = None
    price_move_pct: float | None = Field(default=None, ge=1, le=50)


class RuleIn(_Base):
    symbol: str | None = Field(default=None, max_length=32)
    event_type: str = Field(max_length=16)
    enabled: bool = True
    threshold: float | None = Field(default=None, ge=0, le=100)


# ------------------------------------------------------------------ portfolio


@router.get("/me/portfolio")
def get_portfolio(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return portfolio.summary(db, user)


@router.post("/me/holdings", status_code=201)
def add_holding(req: HoldingIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return portfolio.add_holding(db, user, req.model_dump())


@router.patch("/me/holdings/{hid}")
def update_holding(
    hid: int, req: HoldingPatch, user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return portfolio.update_holding(db, user, hid, {k: getattr(req, k) for k in req.model_fields_set})


@router.delete("/me/holdings/{hid}", status_code=204)
def delete_holding(hid: int, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> Response:
    portfolio.delete_holding(db, user, hid)
    return Response(status_code=204)


# ------------------------------------------------------------------ watchlist


@router.get("/me/watchlist")
def get_watchlist(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return portfolio.watchlist(db, user)


@router.put("/me/watchlist/{symbol}")
def watch(symbol: str, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return portfolio.watch(db, user, symbol, True)


@router.delete("/me/watchlist/{symbol}")
def unwatch(symbol: str, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return portfolio.watch(db, user, symbol, False)


@router.get("/me/track/{symbol}")
def track_status(symbol: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return portfolio.status_for(db, viewer, symbol)


# ------------------------------------------------------------------ intelligence


@router.get("/me/today")
def today(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Your Nexis Today: material developments, active Pulse discussions and upcoming events for what you track."""
    return portfolio.today(db, user)


@router.get("/me/intelligence")
def intelligence(
    days: int = Query(default=7, ge=1, le=30), user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return portfolio.intelligence(db, user, days)


@router.get("/intelligence/{symbol}")
def asset_intelligence(
    symbol: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return portfolio.asset_intelligence(db, viewer, symbol)


@router.post("/intelligence/{symbol}/brief")
def brief(
    symbol: str, request: Request, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    from app.services import entitlements

    user = entitlements.require_account(viewer, "brief")
    ratelimit.hit(db, f"intel-brief:{ratelimit.client_ip(request)}", 30)
    return portfolio.brief(db, user, symbol, metered=True)


# ------------------------------------------------------------------ notifications


@router.get("/me/notifications")
def notifications(
    unread: bool = False, before: int | None = None, user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return alerts.listing(db, user, unread, before)


@router.post("/me/notifications/read")
def read(req: ReadIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, int]:
    return {"marked": alerts.mark_read(db, user, req.ids)}


@router.get("/me/notification-preferences")
def get_preferences(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return alerts.serialize_preferences(db, user)


@router.put("/me/notification-preferences")
def put_preferences(req: PreferencesIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return alerts.update_preferences(db, user, req.channels, req.email_enabled, req.price_move_pct)


@router.put("/me/alert-rules")
def put_rule(req: RuleIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return alerts.set_rule(db, user, req.symbol, req.event_type, req.enabled, req.threshold)


@router.get("/me/digest/preview")
def digest_preview(
    period: Literal["daily", "weekly"] = "daily", user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return alerts.build_digest(db, user, period)


@router.post("/me/digest")
def digest_send(
    period: Literal["daily", "weekly"] = "daily", user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    rec = alerts.send_digest(db, user, period)
    return {"status": rec.status, "detail": rec.detail, "subject": rec.subject}


@router.get("/me/digests")
def digests(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return alerts.digests(db, user)
