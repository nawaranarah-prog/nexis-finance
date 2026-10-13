"""Nexis plans (Free, Plus, Pro): plans and prices, the member's plan and usage, checkout, billing management, and Stripe webhooks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import auth, billing, ratelimit

router = APIRouter(tags=["billing"])


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# The plan is a key of plans.PRICES (checked by the billing service, which answers 422 for anything else).
PLAN_PATTERN = r"^(plus|pro)_(monthly|yearly)$"


class CheckoutIn(_Base):
    plan: str = Field(pattern=PLAN_PATTERN)


class PreviewIn(_Base):
    plan: str = Field(pattern=PLAN_PATTERN)


class ChangePlanIn(_Base):
    plan: str = Field(pattern=PLAN_PATTERN)
    keep: list[str] | None = Field(default=None, max_length=20)
    # From the preview the member confirmed: the amount due today (fils) and Stripe's proration timestamp. The server
    # recalculates both with Stripe; they only make sure nothing is charged that the member didn't see.
    expected_amount: int | None = Field(default=None, ge=0, le=10_000_000)
    proration_date: int | None = Field(default=None, ge=0)


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


@router.post("/billing/change-plan/preview")
def preview_change(req: PreviewIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """What a plan change costs today and when it takes effect (Stripe's calculation). Changes nothing."""
    ratelimit.hit(db, f"billing-preview:{user.id}", 30)
    return billing.preview_change(db, user, req.plan)


@router.post("/billing/change-plan")
def change_plan(req: ChangePlanIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Apply a reviewed change (tier or billing period) on the same subscription. The new plan starts once Stripe has
    the payment; if the member's action is needed, the response carries Stripe's hosted payment page."""
    ratelimit.hit(db, f"billing-change:{user.id}", 10)
    return billing.change_plan(db, user, req.plan, req.keep, req.expected_amount, req.proration_date)


@router.post("/billing/pending/pay")
def pay_pending(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, str]:
    """Stripe's hosted page to pay for a plan change that's waiting for payment."""
    ratelimit.hit(db, f"billing-change:{user.id}", 10)
    return billing.pending_payment_url(db, user)


@router.post("/billing/pending/cancel")
def cancel_pending(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Drop a plan change that's waiting for payment; the current plan continues."""
    ratelimit.hit(db, f"billing-change:{user.id}", 10)
    return billing.cancel_pending_change(db, user)


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
