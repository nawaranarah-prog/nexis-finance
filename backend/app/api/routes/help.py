"""The Help Agent: verified help articles (searchable without AI) and AI answers grounded in them."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import User
from app.services import auth, help

router = APIRouter(tags=["help"])


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=help.MAX_QUESTION)
    context: str | None = Field(default=None, max_length=24)
    history: list[Turn] = Field(default_factory=list, max_length=12)


@router.get("/help/articles")
def articles(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=600"
    return help.articles()


@router.post("/help/ask")
def ask(
    req: AskIn, request: Request, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Answers about using Nexis. Signed-in members get answers about their own plan; nothing is stored."""
    return help.ask(db, viewer, request, req.question, req.context, [t.model_dump() for t in req.history])
