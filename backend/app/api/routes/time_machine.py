"""Time Machine routes. Members only (401 for visitors); every run fetches market history, so runs are rate-limited.

A saved scenario is a research item (kind ``scenario``) whose state holds the inputs; each execution is an immutable
run with the data snapshot and model version it used. "Run again" adds a run; earlier runs are never rewritten.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.session import get_db
from app.models import User
from app.services import ratelimit, saved_research, time_machine
from app.services.auth import require_user

router = APIRouter(tags=["time-machine"])


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(_Base):
    symbol: str = Field(min_length=1, max_length=32)
    amount: float = Field(gt=0, le=1e12)


class ValuationIn(_Base):
    symbol: str = Field(min_length=1, max_length=32)
    growth_change: float = 0
    wacc_change: float = 0
    terminal_growth_change: float = 0


class ScenarioIn(_Base):
    holdings: str = Field(default="custom", pattern="^(mine|custom)$")
    positions: list[Position] = Field(default_factory=list, max_length=time_machine.MAX_POSITIONS)
    factor_shocks: dict[str, float | None] = Field(default_factory=dict)
    asset_shocks: dict[str, float] = Field(default_factory=dict, max_length=time_machine.MAX_POSITIONS)
    window_weeks: int = 104
    valuation: ValuationIn | None = None


class SaveIn(_Base):
    title: str = Field(min_length=1, max_length=200)
    inputs: ScenarioIn
    notes: str | None = Field(default=None, max_length=5000)


def _run(db: Session, user: User, inputs: ScenarioIn) -> dict[str, Any]:
    ratelimit.hit(db, f"tm-run:{user.id}", 40)
    return time_machine.run(db, user, inputs.model_dump())


@router.get("/time-machine/factors")
def factors(user: User = Depends(require_user)) -> dict:
    return {
        "factors": [{"key": k, "symbol": s, "label": lbl, "unit": u} for k, (s, lbl, u) in time_machine.FACTORS.items()],
        "windows": list(time_machine.WINDOWS),
        "model_version": time_machine.MODEL_VERSION,
    }


@router.post("/time-machine/run")
def run(body: ScenarioIn, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Compute without saving."""
    return _run(db, user, body)


@router.post("/time-machine/scenarios", status_code=201)
def save(body: SaveIn, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    result = _run(db, user, body.inputs)
    item = saved_research.create(db, user, "scenario", body.title, None, {"inputs": result["inputs"]}, body.notes)
    r = saved_research.add_run(db, item, "scenario", result)
    return {"item": saved_research.item_json(db, item, full=True), "run": saved_research.run_json(r)}


@router.get("/time-machine/scenarios/{item_id}")
def scenario(item_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    item = saved_research.get_item(db, user, item_id)
    if item.kind != "scenario":
        raise ValidationFailed("That saved item isn't a scenario.")
    rs = saved_research.runs(db, user, item_id)
    return {"item": saved_research.item_json(db, item, full=True), "runs": [saved_research.run_json(r, full=False) for r in rs],
            "latest": saved_research.run_json(rs[0]) if rs else None}  # fmt: skip


@router.post("/time-machine/scenarios/{item_id}/rerun", status_code=201)
def rerun(item_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Run the saved inputs again on today's data; the earlier runs stay as they were."""
    item = saved_research.get_item(db, user, item_id)
    if item.kind != "scenario":
        raise ValidationFailed("That saved item isn't a scenario.")
    result = _run(db, user, ScenarioIn.model_validate((item.state or {}).get("inputs") or {}))
    return saved_research.run_json(saved_research.add_run(db, item, "scenario", result))


@router.get("/time-machine/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return saved_research.run_json(saved_research.get_run(db, user, run_id))


@router.get("/time-machine/compare")
def compare(a: int, b: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    ra, rb = saved_research.get_run(db, user, a), saved_research.get_run(db, user, b)
    return {"a": saved_research.run_json(ra, full=False), "b": saved_research.run_json(rb, full=False),
            "diff": time_machine.compare(ra.output, rb.output)}  # fmt: skip
