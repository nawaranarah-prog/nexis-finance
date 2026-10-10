"""World Graph: a bounded, evidence-backed neighbourhood around one entity, plus "shared connections" and "path between".

Graphs are built only from stored relationships (each with evidence). The centre's relationships are fetched on first
view (then reused for a week); neighbours are fetched only when a member asks to expand them, so one view costs at
most one Wikidata query. Size is capped (``MAX_NODES``); when the cap cuts nodes the response says so and how many.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.connectivity.providers.wikidata import WikidataProvider
from app.core.errors import ValidationFailed
from app.db.base import utcnow
from app.models import FinEntity, FinRelationship
from app.services import knowledge

MAX_NODES = 40
STATUSES = ("verified", "uncertain", "historical", "inferred")
TYPES = tuple(knowledge.REL_LABELS)
# Order in which neighbours keep their place when the cap applies: better evidence first, then the more
# informative relationship types (corporate structure before country/industry tags).
TYPE_ORDER = {"subsidiary_of": 0, "owned_by": 1, "listed_on": 2, "in_industry": 3, "located_in": 4}


def _edges_touching(db: Session, ids: set[int]) -> list[FinRelationship]:
    if not ids:
        return []
    return list(
        db.scalars(
            select(FinRelationship).where(or_(FinRelationship.source_id.in_(ids), FinRelationship.target_id.in_(ids)))
        ).all()
    )


def _keep(edge: FinRelationship, statuses: set[str], types: set[str]) -> bool:
    return edge.status in statuses and edge.rel_type in types


def _filters(statuses: list[str] | None, types: list[str] | None) -> tuple[set[str], set[str]]:
    s = set(statuses or STATUSES)
    t = set(types or TYPES)
    if not s <= set(STATUSES) or not t <= set(TYPES):
        raise ValidationFailed("Unknown evidence status or relationship type.")
    return s, t


def needs_fetch(e: FinEntity) -> bool:
    return bool(e.wikidata_qid) and (
        e.relations_fetched_at is None or utcnow() - e.relations_fetched_at > knowledge.RELATIONS_TTL
    )


def build(
    db: Session,
    center: FinEntity,
    depth: int = 1,
    statuses: list[str] | None = None,
    types: list[str] | None = None,
    expanded: list[int] | None = None,
    provider: WikidataProvider | None = None,
) -> dict[str, Any]:
    """Neighbourhood of ``center`` to ``depth`` (1 or 2) over stored edges. ``expanded`` lists nodes whose own
    relationships the member asked to see; they are fetched if never fetched before."""
    if depth not in (1, 2):
        raise ValidationFailed("Depth must be 1 or 2.")
    want_s, want_t = _filters(statuses, types)
    fetch: dict[str, Any] | None = None
    if needs_fetch(center):
        fetch = knowledge.expand(db, center, provider)
    for eid in (expanded or [])[:10]:  # at most 10 expansions per view
        e = db.get(FinEntity, eid)
        if e is not None and e.id != center.id and needs_fetch(e):
            knowledge.expand(db, e, provider)

    level = {center.id: 0}
    kept: dict[int, FinRelationship] = {}
    cut: set[int] = set()

    def grow(sources: set[int]) -> None:
        edges = sorted(
            (e for e in _edges_touching(db, sources) if _keep(e, want_s, want_t)),
            key=lambda e: (-knowledge.STATUS_RANK.get(e.status, 0), TYPE_ORDER.get(e.rel_type, 9), e.id),
        )
        for e in edges:
            for here, there in ((e.source_id, e.target_id), (e.target_id, e.source_id)):
                if here in sources and there not in level:
                    if len(level) >= MAX_NODES:
                        cut.add(there)
                    else:
                        level[there] = level[here] + 1

    grow({center.id})
    if depth == 2:
        grow({i for i, d in level.items() if d == 1})
    for eid in expanded or []:  # progressive: each node the member expanded shows its own neighbours
        if eid in level and eid != center.id:
            grow({eid})
    # every stored edge among visible nodes is drawn (e.g. a subsidiary also owned by the same owner)
    for e in _edges_touching(db, set(level)):
        if e.source_id in level and e.target_id in level and _keep(e, want_s, want_t):
            kept[e.id] = e
    truncated = len(cut - set(level))

    entities = {e.id: e for e in db.scalars(select(FinEntity).where(FinEntity.id.in_(level))).all()}
    degree: dict[int, int] = dict.fromkeys(level, 0)
    for e in kept.values():
        degree[e.source_id] += 1
        degree[e.target_id] += 1
    nodes = [
        {**knowledge.entity_json(entities[i]), "level": level[i], "degree": degree[i],
         "expandable": bool(entities[i].wikidata_qid), "expanded": entities[i].relations_fetched_at is not None}
        for i in sorted(level, key=lambda i: (level[i], -degree[i], entities[i].name.lower()))
    ]  # fmt: skip
    edges_json = [knowledge.edge_json(db, e) for e in sorted(kept.values(), key=lambda e: e.id)]
    counts = {s: sum(1 for e in edges_json if e["status"] == s) for s in STATUSES}
    db.refresh(center)
    return {
        "center": knowledge.entity_json(center),
        "nodes": nodes,
        "edges": edges_json,
        "counts": counts,
        "truncated": truncated,
        "max_nodes": MAX_NODES,
        "fetch": fetch,
        "signature": signature(edges_json),
    }


def signature(edges: list[dict[str, Any]]) -> list[list[Any]]:
    """What a saved investigation remembers, to tell the member later whether the data changed."""
    return sorted([e["id"], e["status"]] for e in edges)


def compare_signature(saved: list[list[Any]] | None, now: list[list[Any]]) -> dict[str, Any]:
    old = {int(i): s for i, s in (saved or [])}
    new = {int(i): s for i, s in now}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(i for i in set(old) & set(new) if old[i] != new[i])
    return {
        "changed": bool(added or removed or changed),
        "added": len(added),
        "removed": len(removed),
        "status_changed": len(changed),
    }


# ---------------------------------------------------------------------------------------------- between two entities


def _allowed(include_unreferenced: bool) -> set[str]:
    return {"verified", "uncertain"} if include_unreferenced else {"verified"}


def shared(db: Session, a: FinEntity, b: FinEntity, include_unreferenced: bool = False) -> dict[str, Any]:
    """Entities directly related to both ``a`` and ``b`` (current relationships only, never historical)."""
    if a.id == b.id:
        raise ValidationFailed("Pick two different entities.")
    ok = _allowed(include_unreferenced)
    nbrs: dict[int, dict[int, list[FinRelationship]]] = {a.id: {}, b.id: {}}
    for e in _edges_touching(db, {a.id, b.id}):
        if e.status not in ok:
            continue
        for me, other in ((e.source_id, e.target_id), (e.target_id, e.source_id)):
            if me in nbrs and other not in (a.id, b.id):
                nbrs[me].setdefault(other, []).append(e)
    common = set(nbrs[a.id]) & set(nbrs[b.id])
    ents = {e.id: e for e in db.scalars(select(FinEntity).where(FinEntity.id.in_(common))).all()} if common else {}
    items = [
        {"entity": knowledge.entity_json(ents[i]),
         "via_a": [knowledge.edge_json(db, e) for e in nbrs[a.id][i]],
         "via_b": [knowledge.edge_json(db, e) for e in nbrs[b.id][i]]}
        for i in sorted(common, key=lambda i: ents[i].name.lower())
    ]  # fmt: skip
    return {"a": knowledge.entity_json(a), "b": knowledge.entity_json(b), "shared": items,
            "statuses": sorted(ok), "note": _coverage_note(db, a, b)}  # fmt: skip


def path(db: Session, a: FinEntity, b: FinEntity, include_unreferenced: bool = False, max_hops: int = 4) -> dict[str, Any]:
    """Shortest chain of stored relationships between ``a`` and ``b`` (either direction), at most ``max_hops`` long."""
    if a.id == b.id:
        raise ValidationFailed("Pick two different entities.")
    ok = _allowed(include_unreferenced)
    prev: dict[int, tuple[int, FinRelationship] | None] = {a.id: None}
    q: deque[tuple[int, int]] = deque([(a.id, 0)])
    found = False
    while q and not found:
        node, hops = q.popleft()
        if hops >= max_hops:
            continue
        for e in _edges_touching(db, {node}):
            if e.status not in ok:
                continue
            other = e.target_id if e.source_id == node else e.source_id
            if other in prev:
                continue
            prev[other] = (node, e)
            if other == b.id:
                found = True
                break
            q.append((other, hops + 1))
        if len(prev) > 5000:  # stored graph is small today; this keeps any future growth bounded
            break
    steps: list[dict[str, Any]] = []
    if found:
        cur = b.id
        while prev[cur] is not None:
            frm, e = prev[cur]  # type: ignore[misc]
            steps.append({"from": knowledge.entity_json(db.get(FinEntity, frm)), "to": knowledge.entity_json(db.get(FinEntity, cur)),
                          "edge": knowledge.edge_json(db, e)})  # fmt: skip
            cur = frm
        steps.reverse()
    return {"a": knowledge.entity_json(a), "b": knowledge.entity_json(b), "found": found, "steps": steps, "max_hops": max_hops,
            "statuses": sorted(ok), "note": _coverage_note(db, a, b)}  # fmt: skip


def ensure_loaded(db: Session, *entities: FinEntity, provider: WikidataProvider | None = None) -> None:
    """Fetch relationships for any entity that has never had them (or whose copy is over a week old)."""
    for e in entities:
        if needs_fetch(e):
            knowledge.expand(db, e, provider)


def _coverage_note(db: Session, a: FinEntity, b: FinEntity) -> str | None:
    missing = [e.name for e in (a, b) if e.relations_fetched_at is None]
    if missing:
        return f"Relationships for {' and '.join(missing)} haven't been loaded yet — open them in the graph first."
    return None
