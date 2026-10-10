"""Entity resolution, source evidence and evidence-backed relationships (shared by World Graph, Time Machine, Devil's Advocate).

Rules this module enforces:

* **Ambiguity is never guessed.** A name that matches more than one entity, or anything not yet known locally,
  comes back as a list of choices. Only names the source gives (label, listing ticker, Nexis symbol) resolve
  directly; what a member typed is never stored as a shared alias.
* **No edge without evidence.** Every relationship is written together with the evidence record(s) it came from.
* **Status says how sure we are.** ``verified`` — the source statement cites a reference; ``uncertain`` — the
  statement exists in the source but cites nothing; ``historical`` — it has an end date in the past; ``inferred``
  — computed by us (e.g. measured market sensitivity), never presented as a reported fact.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connectivity.providers.wikidata import WikidataProvider, check_qid
from app.core.errors import NotFoundError, ValidationFailed
from app.db.base import utcnow
from app.models import EvidenceRecord, FinEntity, FinEntityAlias, FinRelationship, RelationshipEvidence
from app.services import markets

KINDS = ("company", "asset", "industry", "sector", "country", "exchange", "commodity", "indicator", "organization", "other")
STATUS_RANK = {"verified": 3, "uncertain": 2, "inferred": 1, "historical": 0}
REL_LABELS = {
    "subsidiary_of": "subsidiary of",
    "owned_by": "owned by",
    "listed_on": "listed on",
    "in_industry": "industry",
    "located_in": "country",
}
RELATIONS_TTL = timedelta(days=7)
SEARCH_TTL = timedelta(days=1)
# Listing exchange (as Wikidata labels it) -> the suffix Nexis market symbols use. Only these are mapped.
EXCHANGE_SUFFIX = {"Dubai Financial Market": ".AE", "Abu Dhabi Securities Exchange": ".AD",
                   "New York Stock Exchange": "", "Nasdaq": "", "NASDAQ": ""}  # fmt: skip
_SUFFIXES = re.compile(
    r"\b(pjsc|p\.j\.s\.c|psc|plc|inc|incorporated|ltd|limited|llc|corp|corporation|co|company|sa|ag|nv|group)\b\.?"
)


def normalise(text: str) -> str:
    """Lower-case, accent-free, punctuation-free name key; legal-form suffixes (PJSC, Inc…) don't matter."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^\w\s.&-]", " ", s)
    s = _SUFFIXES.sub(" ", s)
    s = re.sub(r"[\s.\-]+", " ", s).strip()
    return s[:200]


def _symbol_key(symbol: str) -> str:
    return "sym:" + symbol.strip().upper()[:32]


# ---------------------------------------------------------------------------------------------- entities


def entity_json(e: FinEntity) -> dict[str, Any]:
    return {
        "id": e.id,
        "kind": e.kind,
        "name": e.name,
        "description": e.description,
        "wikidata_qid": e.wikidata_qid,
        "wikidata_url": f"https://www.wikidata.org/wiki/{e.wikidata_qid}" if e.wikidata_qid else None,
        "symbol": e.symbol,
        "country": e.country,
        "relations_fetched_at": e.relations_fetched_at.isoformat() + "Z" if e.relations_fetched_at else None,
    }


def add_alias(db: Session, entity: FinEntity, alias: str, source: str) -> None:
    key = _symbol_key(alias) if source == "symbol" else normalise(alias)
    if not key or key == "sym:":
        return
    exists = db.scalar(select(FinEntityAlias.id).where(FinEntityAlias.entity_id == entity.id, FinEntityAlias.alias_norm == key))
    if exists is None:
        insert_once(db, FinEntityAlias(entity_id=entity.id, alias_norm=key, source=source))


def insert_once(db: Session, row: object) -> bool:
    """Insert ``row`` now, inside a savepoint. Returns False (and leaves the session usable) when a concurrent request
    already inserted the same unique row — two members opening the same company at once is normal, not an error."""
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return True
    except IntegrityError:
        return False


def upsert_entity(db: Session, qid: str, name: str, description: str | None = None, kind: str = "other") -> FinEntity:
    qid = check_qid(qid)
    kind = kind if kind in KINDS else "other"
    find = select(FinEntity).where(FinEntity.wikidata_qid == qid)
    e = db.scalar(find)
    if e is None:
        new = FinEntity(
            wikidata_qid=qid, name=(name or qid)[:200], description=(description or None) and description[:300], kind=kind
        )
        e = new if insert_once(db, new) else db.scalar(find)  # lost a race: use the row the other request made
        if e is None:
            raise ValidationFailed("Couldn't save that entity; please try again.")
    if e.wikidata_qid == qid:
        if e.name == e.wikidata_qid and name and name != qid:
            e.name = name[:200]  # a real label replaces a bare id
        if not e.description and description:
            e.description = description[:300]
        if e.kind in ("other", "organization") and kind not in ("other", "organization"):
            e.kind = kind  # more specific knowledge wins; a known kind is never downgraded
    if name and name != qid:
        add_alias(db, e, name, "label")
    return e


def link_symbol(db: Session, entity: FinEntity, symbol: str) -> None:
    symbol = markets.clean_symbol(symbol)
    if entity.symbol is None:
        entity.symbol = symbol
    add_alias(db, entity, symbol, "symbol")


def _local_matches(db: Session, query: str) -> list[FinEntity]:
    keys = {normalise(query)}
    if re.match(r"^[A-Za-z0-9^=.\-]{1,24}$", query.strip()):
        keys.add(_symbol_key(query))
    keys.discard("")
    if not keys:
        return []
    ids = db.scalars(select(FinEntityAlias.entity_id).where(FinEntityAlias.alias_norm.in_(keys)).distinct()).all()
    return list(db.scalars(select(FinEntity).where(FinEntity.id.in_(ids)).order_by(FinEntity.id)).all()) if ids else []


def search_choices(
    db: Session, text: str, provider: WikidataProvider | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = provider or WikidataProvider()
    key = "kg:wd:search:" + normalise(text)[:120]
    rows, meta = markets.cached(db, key, SEARCH_TTL, lambda: provider.search(text))
    known = (
        {
            e.wikidata_qid: e
            for e in db.scalars(select(FinEntity).where(FinEntity.wikidata_qid.in_([r["qid"] for r in rows]))).all()
        }
        if rows
        else {}
    )
    out = []
    for r in rows:
        e = known.get(r["qid"])
        out.append({**r, "entity_id": e.id if e else None, "symbol": e.symbol if e else None})
    return out, meta


def resolve(db: Session, query: str, provider: WikidataProvider | None = None) -> dict[str, Any]:
    """``resolved`` (one entity), ``ambiguous`` (pick one of ``choices``) or ``not_found``. Never auto-picks."""
    q = (query or "").strip()
    if len(q) < 2 or len(q) > 120:
        raise ValidationFailed("Type between 2 and 120 characters.")
    if re.match(r"^[Qq][1-9]\d{0,11}$", q):  # an exact Wikidata id is unambiguous by definition
        e = db.scalar(select(FinEntity).where(FinEntity.wikidata_qid == q.upper()))
        if e is not None:
            return {"status": "resolved", "entity": entity_json(e), "choices": []}
    local = _local_matches(db, q)
    if len(local) == 1:
        return {"status": "resolved", "entity": entity_json(local[0]), "choices": []}
    if len(local) > 1:
        return {
            "status": "ambiguous",
            "entity": None,
            "source": "local",
            "choices": [{**entity_json(e), "entity_id": e.id, "qid": e.wikidata_qid} for e in local],
        }
    text = q.split(".")[0] if re.match(r"^[A-Za-z0-9]{1,12}\.[A-Za-z]{2}$", q) else q  # EMAAR.AE -> "EMAAR"
    choices, meta = search_choices(db, text, provider)
    if not choices:
        return {"status": "not_found", "entity": None, "choices": [], "source": "wikidata", "fetched_at": meta.get("fetched_at")}
    return {
        "status": "ambiguous",
        "entity": None,
        "choices": choices,
        "source": "wikidata",
        "fetched_at": meta.get("fetched_at"),
        "stale": meta.get("stale", False),
    }


def confirm(db: Session, qid: str, provider: WikidataProvider | None = None) -> FinEntity:
    """A member picked ``qid`` from the choices: make sure the entity exists locally.

    What the member typed is deliberately *not* stored as an alias: aliases are shared by everyone, so only names the
    source itself gives (label, listing ticker, Nexis symbol) become aliases. Otherwise one member could make a search
    term resolve to the wrong company for every other member."""
    qid = check_qid(qid)
    e = db.scalar(select(FinEntity).where(FinEntity.wikidata_qid == qid))
    if e is None:
        hits, _ = search_choices(db, qid, provider)
        hit = next((h for h in hits if h["qid"] == qid), None)
        if hit is None:
            raise NotFoundError("That Wikidata item wasn't found.")
        e = upsert_entity(db, qid, hit["name"], hit.get("description"), "organization")
    db.commit()
    return e


def get_entity(db: Session, entity_id: int) -> FinEntity:
    e = db.get(FinEntity, entity_id)
    if e is None:
        raise NotFoundError("Entity not found.")
    return e


# ---------------------------------------------------------------------------------------------- evidence


def _hash(*parts: str | None) -> str:
    return hashlib.sha256("|".join(p or "" for p in parts).encode()).hexdigest()


def upsert_evidence(
    db: Session,
    *,
    source_kind: str,
    title: str,
    url: str | None = None,
    external_id: str | None = None,
    publisher: str | None = None,
    published_at: datetime | None = None,
    excerpt: str | None = None,
) -> EvidenceRecord:
    """One row per distinct source item (deduplicated by kind + id + url); bounded excerpt, never a whole document."""
    if url and not re.match(r"^https?://", url, re.I):
        url = None  # only web links are stored as links
    h = _hash(source_kind, external_id, url if not external_id else None)
    find = select(EvidenceRecord).where(EvidenceRecord.content_hash == h)
    ev = db.scalar(find)
    if ev is None:
        new = EvidenceRecord(
            source_kind=source_kind,
            content_hash=h,
            title=title[:500],
            url=url and url[:1500],
            external_id=external_id and external_id[:200],
            publisher=publisher and publisher[:160],
            published_at=published_at,
            excerpt=excerpt and excerpt[:600],
        )
        if insert_once(db, new):
            return new
        ev = db.scalar(find)  # a concurrent request stored the same source item first
        if ev is None:
            raise ValidationFailed("Couldn't save the evidence; please try again.")
    else:
        ev.retrieved_at = utcnow()
        ev.title, ev.url = title[:500], url and url[:1500]
        ev.excerpt = excerpt and excerpt[:600]
    return ev


def evidence_json(ev: EvidenceRecord) -> dict[str, Any]:
    return {
        "id": ev.id,
        "source_kind": ev.source_kind,
        "title": ev.title,
        "url": ev.url,
        "publisher": ev.publisher,
        "published_at": ev.published_at.isoformat() + "Z" if ev.published_at else None,
        "retrieved_at": ev.retrieved_at.isoformat() + "Z",
        "excerpt": ev.excerpt,
    }


# ---------------------------------------------------------------------------------------------- relationships


def _day(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def statement_status(st: dict[str, Any], today: date | None = None) -> str:
    end = _day(st.get("end"))
    if end is not None and end < (today or date.today()):
        return "historical"
    return "verified" if (st.get("ref_urls") or st.get("stated_in")) else "uncertain"


def upsert_edge(
    db: Session,
    source: FinEntity,
    target: FinEntity,
    rel_type: str,
    status: str,
    evidence: list[EvidenceRecord],
    valid_from: date | None = None,
    valid_to: date | None = None,
    note: str | None = None,
) -> FinRelationship:
    if not evidence:
        raise ValidationFailed("A relationship needs at least one evidence record.")
    if source.id == target.id:
        raise ValidationFailed("An entity can't be related to itself.")
    find = select(FinRelationship).where(
        FinRelationship.source_id == source.id, FinRelationship.target_id == target.id, FinRelationship.rel_type == rel_type
    )
    edge = db.scalar(find)
    now = utcnow()
    created = False
    if edge is None:
        new = FinRelationship(
            source_id=source.id,
            target_id=target.id,
            rel_type=rel_type,
            status=status,
            valid_from=valid_from,
            valid_to=valid_to,
            note=note,
            first_seen=now,
            last_verified=now,
        )
        created = insert_once(db, new)
        edge = new if created else db.scalar(find)  # lost a race: update the other request's edge instead
        if edge is None:
            raise ValidationFailed("Couldn't save the relationship; please try again.")
    if not created:
        if STATUS_RANK.get(status, 0) > STATUS_RANK.get(edge.status, 0):
            edge.status = status  # the best-supported statement decides
        edge.valid_from = min(filter(None, [edge.valid_from, valid_from]), default=None)
        edge.valid_to = None if edge.status != "historical" else max(filter(None, [edge.valid_to, valid_to]), default=None)
        edge.note = note or edge.note
        edge.last_verified = now
    for ev in evidence:
        if db.get(RelationshipEvidence, (edge.id, ev.id)) is None:
            insert_once(db, RelationshipEvidence(relationship_id=edge.id, evidence_id=ev.id))
    return edge


def _wikidata_evidence(db: Session, st: dict[str, Any], names: dict[str, str]) -> EvidenceRecord:
    on = st["on_item"]
    subject = names.get(on, on)
    value_qid = st["target_qid"] if on == st["source_qid"] else st["source_qid"]
    value = names.get(value_qid, value_qid)
    quals = []
    if st.get("start"):
        quals.append(f"from {st['start']}")
    if st.get("end"):
        quals.append(f"until {st['end']}")
    if st.get("ticker"):
        quals.append(f"ticker {st['ticker']}")
    cited = st.get("ref_urls") or []
    stated = st.get("stated_in") or []
    refs = ("Cites: " + "; ".join(cited[:2] + stated[:2])) if (cited or stated) else "No reference is cited for this statement."
    excerpt = f"{subject} — {st['property_label']}: {value}" + (f" ({', '.join(quals)})" if quals else "") + f". {refs}"
    return upsert_evidence(
        db,
        source_kind="wikidata",
        external_id=st["statement"],
        url=cited[0] if cited else f"https://www.wikidata.org/wiki/{on}#{st['property']}",
        title=f"Wikidata statement on {subject}: {st['property_label']} {value}",
        publisher="Wikidata" + (f", citing {stated[0]}" if stated else ""),
        excerpt=excerpt,
    )


def expand(db: Session, entity: FinEntity, provider: WikidataProvider | None = None, today: date | None = None) -> dict[str, Any]:
    """Fetch (or reuse, within a week) the entity's Wikidata relationships and store them with their evidence."""
    if not entity.wikidata_qid:
        return {"fetched": False, "reason": "This entity has no Wikidata id, so there are no reported relationships to fetch."}
    provider = provider or WikidataProvider()
    stmts, meta = markets.cached(
        db, "kg:wd:rel:" + entity.wikidata_qid, RELATIONS_TTL, lambda: provider.relations(entity.wikidata_qid)
    )
    names = {entity.wikidata_qid: entity.name}
    for st in stmts:
        names[st["other"]["qid"]] = st["other"]["name"]
    edges = 0
    for st in stmts:
        o = st["other"]
        other = upsert_entity(db, o["qid"], o["name"], o.get("description"), o.get("kind", "other"))
        src, dst = (entity, other) if st["source_qid"] == entity.wikidata_qid else (other, entity)
        ev = _wikidata_evidence(db, st, names)
        status = statement_status(st, today)
        upsert_edge(db, src, dst, st["rel_type"], status, [ev], _day(st.get("start")), _day(st.get("end")))
        edges += 1
        if st["property"] == "P414" and st.get("ticker") and st["source_qid"] == entity.wikidata_qid:
            suffix = EXCHANGE_SUFFIX.get(o["name"])
            if suffix is not None:
                link_symbol(db, entity, st["ticker"].replace(".", "-") + suffix)
            add_alias(db, entity, st["ticker"], "ticker")
        if st["property"] == "P414" and st["source_qid"] == entity.wikidata_qid and entity.kind in ("other", "organization"):
            entity.kind = "company"  # it has a stock-exchange listing
        if st["property"] == "P17" and st["source_qid"] == entity.wikidata_qid and not entity.country:
            entity.country = o["name"][:80]
    entity.relations_fetched_at = utcnow()
    db.commit()
    return {
        "fetched": True,
        "statements": edges,
        "fetched_at": meta.get("fetched_at"),
        "stale": meta.get("stale", False),
        "error": meta.get("error"),
    }


def edge_json(db: Session, edge: FinRelationship) -> dict[str, Any]:
    evs = db.scalars(
        select(EvidenceRecord)
        .join(RelationshipEvidence, RelationshipEvidence.evidence_id == EvidenceRecord.id)
        .where(RelationshipEvidence.relationship_id == edge.id)
        .order_by(EvidenceRecord.id)
    ).all()
    return {
        "id": edge.id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "type": edge.rel_type,
        "label": REL_LABELS.get(edge.rel_type, edge.rel_type.replace("_", " ")),
        "status": edge.status,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "note": edge.note,
        "last_verified": edge.last_verified.isoformat() + "Z",
        "evidence": [evidence_json(e) for e in evs],
    }
