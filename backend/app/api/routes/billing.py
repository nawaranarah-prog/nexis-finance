"""Nexis plans (Free, Plus, Pro): plans and prices, the member's plan and usage, checkout, billing management, and Stripe webhooks."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import auth, billing, ratelimit

router = APIRouter(tags=["billing"])


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckoutIn(_Base):
    plan: Literal["plus_monthly", "pro_monthly"]


class ChangePlanIn(_Base):
    plan: Literal["plus_monthly", "pro_monthly"]
    keep: list[str] | None = Field(default=None, max_length=20)


class ConfirmIn(_Base):
    session_id: str = Field(min_length=8, max_length=200)


@router.get("/billing/plans")
def plans(viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return billing.catalog(db, viewer)


@router.get("/billing/status")
def status(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return billing.status(db, user)


@router.post("/billing/checkout")
def checkout(req: CheckoutIn, request: Request, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, str]:
    ratelimit.hit(db, f"checkout:{user.id}", 20)
    return billing.checkout(db, user, req.plan)


@router.post("/billing/change-plan")
def change_plan(req: ChangePlanIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Switch between Plus and Pro on the same subscription (prorated by Stripe)."""
    ratelimit.hit(db, f"billing-change:{user.id}", 10)
    return billing.change_plan(db, user, req.plan, req.keep)


@router.post("/billing/confirm")
def confirm(req: ConfirmIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    ratelimit.hit(db, f"billing-confirm:{user.id}", 60)
    return billing.confirm(db, user, req.session_id)


@router.post("/billing/cancel")
def cancel(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Cancel at the end of the paid period. The plan continues until then; nothing is deleted."""
    ratelimit.hit(db, f"billing-cancel:{user.id}", 20)
    return billing.set_cancel(db, user, True)


@router.post("/billing/resume")
def resume(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Undo a scheduled cancellation before the period ends."""
    ratelimit.hit(db, f"billing-cancel:{user.id}", 20)
    return billing.set_cancel(db, user, False)


@router.post("/billing/portal")
def portal(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, str]:
    ratelimit.hit(db, f"portal:{user.id}", 30)
    return billing.portal(db, user)


@router.post("/billing/webhook", include_in_schema=False)
async def webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Stripe events. Authenticated by Stripe's signature, not by a session."""
    from app.services import billing_stripe

    payload = await request.body()
    return billing_stripe.handle_webhook(db, payload, request.headers.get("stripe-signature"))
