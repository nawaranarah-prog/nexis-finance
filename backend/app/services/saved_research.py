"""Members' saved research (graph investigations now; scenarios and theses later). Private: only the owner can see,
change or delete an item — anyone else gets "not found", the same as an id that doesn't exist."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationFailed
from app.db.base import utcnow
from app.models import FinEntity, ResearchItem, ResearchRun, User
from app.services import knowledge

KINDS = ("graph", "scenario", "thesis", "report")
ITEM_STATUSES = ("active", "under_review", "archived")
MAX_ITEMS = 300
MAX_STATE_BYTES = 20_000


def _check_state(state: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(state, separators=(",", ":"))) > MAX_STATE_BYTES:
        raise ValidationFailed("That's too much to save in one item.")
    return state


def item_json(db: Session, it: ResearchItem, full: bool = False) -> dict[str, Any]:
    ent = db.get(FinEntity, it.entity_id) if it.entity_id else None
    out = {
        "id": it.id,
        "kind": it.kind,
        "title": it.title,
        "status": it.status,
        "entity": knowledge.entity_json(ent) if ent else None,
        "notes": it.notes,
        "created_at": it.created_at.isoformat() + "Z",
        "updated_at": it.updated_at.isoformat() + "Z",
    }
    if full:
        out["state"] = it.state
    return out


def list_items(db: Session, user: User, kind: str | None = None) -> list[dict[str, Any]]:
    q = select(ResearchItem).where(ResearchItem.user_id == user.id)
    if kind:
        if kind not in KINDS:
            raise ValidationFailed("Unknown kind.")
        q = q.where(ResearchItem.kind == kind)
    return [item_json(db, it) for it in db.scalars(q.order_by(ResearchItem.updated_at.desc()).limit(MAX_ITEMS)).all()]


def get_item(db: Session, user: User, item_id: int) -> ResearchItem:
    it = db.get(ResearchItem, item_id)
    if it is None or it.user_id != user.id:
        raise NotFoundError("Saved item not found.")
    return it


def create(
    db: Session, user: User, kind: str, title: str, entity_id: int | None, state: dict[str, Any], notes: str | None
) -> ResearchItem:
    if kind not in KINDS:
        raise ValidationFailed("Unknown kind.")
    if db.scalar(select(func.count()).select_from(ResearchItem).where(ResearchItem.user_id == user.id)) >= MAX_ITEMS:
        raise ValidationFailed(f"You can keep up to {MAX_ITEMS} saved items. Delete some to save more.")
    if entity_id is not None:
        knowledge.get_entity(db, entity_id)
    it = ResearchItem(
        user_id=user.id, kind=kind, title=title.strip()[:200], entity_id=entity_id, state=_check_state(state), notes=notes
    )
    db.add(it)
    db.commit()
    return it


def update(db: Session, user: User, item_id: int, changes: dict[str, Any]) -> ResearchItem:
    it = get_item(db, user, item_id)
    if "title" in changes and changes["title"] is not None:
        it.title = changes["title"].strip()[:200]
    if "notes" in changes:
        it.notes = changes["notes"]
    if changes.get("status") is not None:
        if changes["status"] not in ITEM_STATUSES:
            raise ValidationFailed("Unknown status.")
        it.status = changes["status"]
    if changes.get("state") is not None:
        it.state = _check_state(changes["state"])
    db.commit()
    return it


def delete(db: Session, user: User, item_id: int) -> None:
    db.delete(get_item(db, user, item_id))
    db.commit()


# ---------------------------------------------------------------------------------------------- runs (immutable)


def add_run(db: Session, item: ResearchItem, run_type: str, result: dict[str, Any], status: str = "completed") -> ResearchRun:
    """Store one execution: its inputs, the data it used, the model version and the output. Never updated later."""
    r = ResearchRun(item_id=item.id, run_type=run_type, model_version=result["model_version"], inputs=result["inputs"],
                    snapshot=result["snapshot"], output=result["output"], status=status)  # fmt: skip
    db.add(r)
    item.updated_at = utcnow()
    db.commit()
    return r


def run_json(r: ResearchRun, full: bool = True) -> dict[str, Any]:
    out = {"id": r.id, "item_id": r.item_id, "run_type": r.run_type, "model_version": r.model_version, "status": r.status,
           "created_at": r.created_at.isoformat() + "Z"}  # fmt: skip
    if full:
        out.update(inputs=r.inputs, snapshot=r.snapshot, output=r.output)
    else:
        out["summary"] = (r.output or {}).get("portfolio")
    return out


def runs(db: Session, user: User, item_id: int) -> list[ResearchRun]:
    get_item(db, user, item_id)
    return list(
        db.scalars(select(ResearchRun).where(ResearchRun.item_id == item_id).order_by(ResearchRun.id.desc()).limit(50)).all()
    )


def get_run(db: Session, user: User, run_id: int) -> ResearchRun:
    r = db.get(ResearchRun, run_id)
    if r is None:
        raise NotFoundError("Run not found.")
    get_item(db, user, r.item_id)  # 404 unless the caller owns the item
    return r
