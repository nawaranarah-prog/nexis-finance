"""Devil's Advocate routes. Members only (401 for visitors). Each analysis is an AI call, so it is rate-limited per
member. A thesis is a research item (kind ``thesis``); each analysis is an immutable run, failed ones included."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.session import get_db
from app.models import ResearchItem, User
from app.services import devils_advocate as da
from app.services import ratelimit, saved_research
from app.services.auth import require_user

router = APIRouter(tags=["devils-advocate"])
DAILY = 10


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThesisIn(_Base):
    symbol: str = Field(min_length=1, max_length=32)
    thesis: str = Field(min_length=30, max_length=3000)
    horizon: str | None = Field(default=None, max_length=60)
    title: str | None = Field(default=None, max_length=200)


class ThesisPatch(_Base):
    thesis: str | None = Field(default=None, min_length=30, max_length=3000)
    horizon: str | None = Field(default=None, max_length=60)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "under_review", "archived"] | None = None


def _thesis(db: Session, user: User, item_id: int) -> ResearchItem:
    item = saved_research.get_item(db, user, item_id)
    if item.kind != "thesis":
        raise ValidationFailed("That saved item isn't a thesis.")
    return item


def _view(db: Session, user: User, item: ResearchItem) -> dict[str, Any]:
    rs = saved_research.runs(db, user, item.id)
    latest = saved_research.run_json(rs[0]) if rs else None
    done = [r for r in rs if r.status == "completed"]
    changes = da.compare(done[1].output, done[0].output) if len(done) >= 2 else None
    return {"item": saved_research.item_json(db, item, full=True), "runs": [saved_research.run_json(r, full=False) for r in rs],
            "latest": latest, "changes_since_previous": changes, "daily_limit": DAILY}  # fmt: skip


@router.post("/devils-advocate/theses", status_code=201)
def create(body: ThesisIn, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    sym, text = da.check_input(body.symbol, body.thesis)
    title = (body.title or f"{sym}: {text[:60]}{'…' if len(text) > 60 else ''}").strip()
    item = saved_research.create(
        db, user, "thesis", title, None, {"symbol": sym, "thesis": text, "horizon": body.horizon, "claims": None}, None
    )
    return _view(db, user, item)


@router.get("/devils-advocate/theses/{item_id}")
def get(item_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return _view(db, user, _thesis(db, user, item_id))


@router.patch("/devils-advocate/theses/{item_id}")
def patch(item_id: int, body: ThesisPatch, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    item = _thesis(db, user, item_id)
    st = dict(item.state or {})
    changes: dict[str, Any] = {}
    if body.thesis is not None:
        _, text = da.check_input(st.get("symbol", ""), body.thesis)
        if text != st.get("thesis"):
            st.update(thesis=text, claims=None)  # a rewritten thesis gets its claims extracted afresh
    if body.horizon is not None:
        st["horizon"] = body.horizon or None
    changes["state"] = st
    if body.title is not None:
        changes["title"] = body.title
    if body.status is not None:
        changes["status"] = body.status
    saved_research.update(db, user, item_id, changes)
    return _view(db, user, item)


@router.post("/devils-advocate/theses/{item_id}/analyse", status_code=201)
def analyse(item_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    item = _thesis(db, user, item_id)
    ratelimit.hit(db, f"da-analyse:{user.id}", DAILY, timedelta(days=1))
    result = da.analyse(db, item)
    run = saved_research.add_run(db, item, "thesis", result, status=result["status"])
    if result["status"] == "completed" and not (item.state or {}).get("claims"):
        claims = [{k: c[k] for k in ("id", "text", "kind")} for c in result["output"]["claims"]]
        saved_research.update(db, user, item.id, {"state": {**(item.state or {}), "claims": claims}})  # fixed from now on
    return {**_view(db, user, item), "run": saved_research.run_json(run)}


@router.get("/devils-advocate/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return saved_research.run_json(saved_research.get_run(db, user, run_id))
