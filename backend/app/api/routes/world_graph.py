"""World Graph and saved research. Members only (401 for visitors): the graph is a research tool, and every lookup can
spend the shared Wikidata budget, so each member is also rate-limited."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models import FinRelationship, User
from app.services import knowledge, ratelimit, research_reports, saved_research, world_graph
from app.services.auth import require_user

router = APIRouter(tags=["world-graph"])


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmIn(_Base):
    qid: str = Field(min_length=2, max_length=14, pattern=r"^[Qq][1-9]\d{0,11}$")


class ItemIn(_Base):
    kind: Literal["graph", "scenario", "thesis", "report"]
    title: str = Field(min_length=1, max_length=200)
    entity_id: int | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=5000)


class ItemPatch(_Base):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    status: Literal["active", "under_review", "archived"] | None = None
    state: dict[str, Any] | None = None


def _csv(v: str | None) -> list[str] | None:
    return [x for x in (v or "").split(",") if x] or None


@router.get("/world-graph/resolve")
def resolve(
    q: str = Query(min_length=2, max_length=120), user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict:
    ratelimit.hit(db, f"wg-resolve:{user.id}", 60)
    return knowledge.resolve(db, q)


@router.post("/world-graph/entities")
def confirm(body: ConfirmIn, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    ratelimit.hit(db, f"wg-confirm:{user.id}", 30)
    return knowledge.entity_json(knowledge.confirm(db, body.qid))


@router.get("/world-graph/entities/{entity_id}/graph")
def graph(
    entity_id: int,
    depth: int = Query(1, ge=1, le=2),
    status: str | None = Query(None, max_length=60),
    types: str | None = Query(None, max_length=120),
    expanded: str | None = Query(None, max_length=200, pattern=r"^[0-9,]*$"),
    saved: int | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    center = knowledge.get_entity(db, entity_id)
    exp = [int(x) for x in _csv(expanded) or []][:10]
    if any(world_graph.needs_fetch(knowledge.get_entity(db, i)) for i in [center.id, *exp]):
        ratelimit.hit(db, f"wg-fetch:{user.id}", 30)  # this view will query Wikidata
    out = world_graph.build(db, center, depth, _csv(status), _csv(types), exp)
    if saved is not None:
        item = saved_research.get_item(db, user, saved)
        out["since_saved"] = world_graph.compare_signature((item.state or {}).get("signature"), out["signature"])
    return out


@router.get("/world-graph/edges/{edge_id}")
def edge(edge_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    e = db.get(FinRelationship, edge_id)
    if e is None:
        raise NotFoundError("Relationship not found.")
    return knowledge.edge_json(db, e)


@router.get("/world-graph/shared")
def shared(a: int, b: int, unreferenced: bool = False, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    ea, eb = knowledge.get_entity(db, a), knowledge.get_entity(db, b)
    if world_graph.needs_fetch(ea) or world_graph.needs_fetch(eb):
        ratelimit.hit(db, f"wg-fetch:{user.id}", 30)
        world_graph.ensure_loaded(db, ea, eb)
    return world_graph.shared(db, ea, eb, unreferenced)


@router.get("/world-graph/path")
def path(a: int, b: int, unreferenced: bool = False, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    ea, eb = knowledge.get_entity(db, a), knowledge.get_entity(db, b)
    if world_graph.needs_fetch(ea) or world_graph.needs_fetch(eb):
        ratelimit.hit(db, f"wg-fetch:{user.id}", 30)
        world_graph.ensure_loaded(db, ea, eb)
    return world_graph.path(db, ea, eb, unreferenced)


# ---------------------------------------------------------------------------------------------- saved research


@router.get("/saved-research")
def list_saved(kind: str | None = None, user: User = Depends(require_user), db: Session = Depends(get_db)) -> list[dict]:
    return saved_research.list_items(db, user, kind)


@router.post("/saved-research", status_code=201)
def create_saved(body: ItemIn, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    ratelimit.hit(db, f"saved-research:{user.id}", 120)
    it = saved_research.create(db, user, body.kind, body.title, body.entity_id, body.state, body.notes)
    return saved_research.item_json(db, it, full=True)


@router.get("/saved-research/{item_id}")
def get_saved(item_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    return saved_research.item_json(db, saved_research.get_item(db, user, item_id), full=True)


@router.patch("/saved-research/{item_id}")
def patch_saved(item_id: int, body: ItemPatch, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    it = saved_research.update(db, user, item_id, body.model_dump(exclude_unset=True))
    return saved_research.item_json(db, it, full=True)


@router.delete("/saved-research/{item_id}", status_code=204)
def delete_saved(item_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> Response:
    saved_research.delete(db, user, item_id)
    return Response(status_code=204)


@router.post("/saved-research/runs/{run_id}/report", status_code=201)
def run_report(run_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """A PDF of one stored run (owner only). The report is private to the member, like every member report."""
    ratelimit.hit(db, f"research-report:{user.id}", 20)
    run = saved_research.get_run(db, user, run_id)
    item = saved_research.get_item(db, user, run.item_id)
    rep = research_reports.generate(db, item, run, user.id)
    return {"id": rep.id, "title": rep.title, "file_name": rep.file_name, "download_url": f"/api/reports/{rep.id}/download"}
