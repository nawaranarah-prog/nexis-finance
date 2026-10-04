"""Legal document versions, Terms acceptance, and Pulse moderation."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import auth, legal, moderation

router = APIRouter(tags=["legal"])


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcceptIn(_Base):
    terms_version: str = Field(min_length=1, max_length=20)
    privacy_version: str = Field(min_length=1, max_length=20)


class ActionIn(_Base):
    target_type: Literal["discussion", "comment"]
    target_id: int = Field(ge=1)
    action: Literal["approve", "remove", "restore", "lock", "unlock", "dismiss", "suspend_author"]
    reason: str | None = Field(default=None, max_length=300)
    days: int | None = Field(default=None, ge=1, le=365)


@router.get("/legal")
def documents() -> dict[str, Any]:
    return legal.documents()


@router.get("/legal/status")
def status(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return legal.status(db, user)


@router.post("/legal/accept")
def accept(req: AcceptIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return legal.accept(db, user, req.terms_version, req.privacy_version, method="prompt")


@router.get("/moderation/queue")
def queue(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return moderation.queue(db, user)


@router.get("/moderation/log")
def log(limit: int = Query(default=100, ge=1, le=500), user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return moderation.log(db, user, limit)


@router.post("/moderation/actions")
def act(req: ActionIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return moderation.act(db, user, req.target_type, req.target_id, req.action, req.reason, req.days)
