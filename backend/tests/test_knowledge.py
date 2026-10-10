"""Entity resolution, evidence and relationships — offline, from Wikidata responses recorded for Emaar Properties."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.connectivity.providers import wikidata
from app.connectivity.providers.wikidata import WikidataProvider, check_qid, parse_relations, parse_search, relations_query
from app.core.config import get_settings
from app.core.errors import ProviderError, ValidationFailed
from app.models import FinEntity, FinRelationship, RelationshipEvidence
from app.services import knowledge

FIX = Path(__file__).parent / "fixtures"
EMAAR = "Q1153463"
SEARCH = json.loads((FIX / "wikidata_search_emaar.json").read_text(encoding="utf-8"))
RELATIONS = json.loads((FIX / f"wikidata_relations_{EMAAR}.json").read_text(encoding="utf-8"))


class RecordedWikidata(WikidataProvider):
    """Answers from the recorded responses and counts calls (so caching can be checked)."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def search(self, text: str, limit: int = 7) -> list[dict[str, Any]]:
        self.calls.append("search:" + text)
        return parse_search(SEARCH) if "emaar" in text.lower() or text == EMAAR else []

    def relations(self, qid: str) -> list[dict[str, Any]]:
        self.calls.append("relations:" + qid)
        return parse_relations(qid, RELATIONS) if qid == EMAAR else []


class FailingWikidata(WikidataProvider):
    def search(self, text: str, limit: int = 7) -> list[dict[str, Any]]:
        raise ProviderError("Wikidata: rate limited (HTTP 429) — try again later")

    def relations(self, qid: str) -> list[dict[str, Any]]:
        raise ProviderError("Wikidata: rate limited (HTTP 429) — try again later")


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


# ---------------------------------------------------------------------------------------------- parsing


def test_search_parsing_keeps_every_candidate_with_its_description() -> None:
    rows = parse_search(SEARCH)
    assert rows[0] == {"qid": EMAAR, "name": "Emaar Properties", "description": "company"}
    assert {"Q55610796", "Q17052427"} <= {r["qid"] for r in rows}  # Emaar Development, Emaar MGF: same word, other companies


def test_relations_parsing_orients_edges_and_keeps_qualifiers_and_references() -> None:
    rels = {(r["property"], r["source_qid"], r["target_qid"]): r for r in parse_relations(EMAAR, RELATIONS)}
    listing = rels[("P414", EMAAR, "Q1973019")]
    assert listing["rel_type"] == "listed_on" and listing["start"] == "2000-01-01" and listing["ticker"] == "EMAAR"
    # "Emaar has subsidiary Emaar Development" (on Emaar's item) becomes "Emaar Development subsidiary_of Emaar"
    assert rels[("P355", "Q55610796", EMAAR)]["rel_type"] == "subsidiary_of"
    burj = rels[("P127", "Q12495", EMAAR)]
    assert burj["rel_type"] == "owned_by" and burj["ref_urls"] == ["http://skyscrapercenter.com/building/burj-khalifa/3"]
    assert burj["other"]["kind"] == "other"  # a building is not a company
    assert rels[("P127", EMAAR, "Q3153926")]["other"]["kind"] == "organization"  # an owner: as far as we can say


def test_query_uses_cheap_patterns_and_rejects_anything_but_an_item_id() -> None:
    q = relations_query(EMAAR)
    assert 'hint:Query hint:optimizer "None"' in q and f"?st ps:P749 wd:{EMAAR}" in q and "LIMIT 400" in q
    for bad in ["Q1 } DELETE", "P31", "Q0", "", "Q" + "1" * 13]:
        with pytest.raises(ValidationFailed):
            check_qid(bad)
    assert check_qid(" q42 ") == "Q42"


def test_normalise_ignores_case_accents_punctuation_and_legal_form() -> None:
    assert knowledge.normalise("Emaar Properties P.J.S.C.") == knowledge.normalise("emaar properties") == "emaar properties"
    assert knowledge.normalise("Société Générale SA") == "societe generale"


# ---------------------------------------------------------------------------------------------- provider HTTP


def _provider_with(handler, monkeypatch) -> WikidataProvider:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "external_data_enabled", True)
    monkeypatch.setattr(wikidata.time, "sleep", lambda s: None)
    return WikidataProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_provider_identifies_itself_and_reports_rate_limits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def ok(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=SEARCH)

    assert _provider_with(ok, monkeypatch).search("Emaar")[0]["qid"] == EMAAR
    assert seen[0].headers["user-agent"].startswith("NexisFinance/") and seen[0].url.params["action"] == "wbsearchentities"

    limited = _provider_with(lambda req: httpx.Response(429, headers={"Retry-After": "60"}), monkeypatch)
    with pytest.raises(ProviderError, match="rate limited"):
        limited.relations(EMAAR)


def test_provider_makes_no_request_when_external_data_is_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(req: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    p = WikidataProvider(client=httpx.Client(transport=httpx.MockTransport(boom)))
    monkeypatch.setattr(get_settings(), "external_data_enabled", False)
    with pytest.raises(ProviderError, match="switched off"):
        p.search("Emaar")


# ---------------------------------------------------------------------------------------------- resolution


def test_a_name_is_never_auto_picked_and_a_confirmed_choice_is_remembered(db) -> None:  # type: ignore[no-untyped-def]
    wd = RecordedWikidata()
    first = knowledge.resolve(db, "Emaar", wd)
    assert first["status"] == "ambiguous" and first["entity"] is None
    assert [c["qid"] for c in first["choices"]][:3] == [EMAAR, "Q130611824", "Q55610796"]

    e = knowledge.confirm(db, EMAAR, "Emaar", wd)
    assert (e.name, e.description) == ("Emaar Properties", "company")
    again = knowledge.resolve(db, "  EMAAR ", wd)
    assert again["status"] == "resolved" and again["entity"]["wikidata_qid"] == EMAAR
    assert knowledge.resolve(db, EMAAR.lower(), wd)["status"] == "resolved"


def test_two_local_entities_with_the_same_name_are_offered_as_choices(db) -> None:  # type: ignore[no-untyped-def]
    a = knowledge.upsert_entity(db, "Q900000001", "Gulf Holding", "a bank", "company")
    b = knowledge.upsert_entity(db, "Q900000002", "Gulf Holding", "a shipping firm", "company")
    db.commit()
    r = knowledge.resolve(db, "gulf holding", RecordedWikidata())
    assert r["status"] == "ambiguous" and r["source"] == "local"
    assert {c["entity_id"] for c in r["choices"]} == {a.id, b.id}


def test_nothing_found_is_said_plainly(db) -> None:  # type: ignore[no-untyped-def]
    assert knowledge.resolve(db, "zzqx unknown co", RecordedWikidata())["status"] == "not_found"
    with pytest.raises(ValidationFailed):
        knowledge.resolve(db, "x", RecordedWikidata())


def test_a_provider_outage_with_nothing_cached_is_an_honest_error(db) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ProviderError):
        knowledge.resolve(db, "Brand New Name Never Searched", FailingWikidata())


# ---------------------------------------------------------------------------------------------- relationships


def _edges(db, entity: FinEntity) -> dict[tuple[str, str, str], FinRelationship]:  # type: ignore[no-untyped-def]
    rows = db.scalars(
        select(FinRelationship).where((FinRelationship.source_id == entity.id) | (FinRelationship.target_id == entity.id))
    ).all()
    name = {e.id: e.name for e in db.scalars(select(FinEntity)).all()}
    return {(name[r.source_id], r.rel_type, name[r.target_id]): r for r in rows}


def test_expanding_stores_every_edge_with_evidence_and_an_honest_status(db) -> None:  # type: ignore[no-untyped-def]
    wd = RecordedWikidata()
    emaar = knowledge.confirm(db, EMAAR, "Emaar", wd)
    out = knowledge.expand(db, emaar, wd)
    assert out["fetched"] is True and out["statements"] == 17

    edges = _edges(db, emaar)
    listing = edges[("Emaar Properties", "listed_on", "Dubai Financial Market")]
    assert listing.status == "uncertain" and listing.valid_from == date(2000, 1, 1)  # true, but Wikidata cites nothing
    burj = edges[("Burj Khalifa", "owned_by", "Emaar Properties")]
    assert burj.status == "verified"
    burj_json = knowledge.edge_json(db, burj)
    assert burj_json["evidence"][0]["url"] == "http://skyscrapercenter.com/building/burj-khalifa/3"
    assert "Cites:" in burj_json["evidence"][0]["excerpt"]

    # Two statements (Emaar "has subsidiary", Emaar Development "parent organization") -> one edge, two evidence rows
    dev = edges[("Emaar Development", "subsidiary_of", "Emaar Properties")]
    assert len(knowledge.edge_json(db, dev)["evidence"]) == 2

    for edge in edges.values():  # no edge without evidence
        assert db.scalar(select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == edge.id)) is not None
        assert edge.status in ("verified", "uncertain", "historical")

    db.refresh(emaar)
    assert emaar.symbol == "EMAAR.AE" and emaar.country == "United Arab Emirates" and emaar.relations_fetched_at
    assert knowledge.resolve(db, "EMAAR.AE", wd)["entity"]["id"] == emaar.id  # the listing ticker links the Nexis symbol
    kinds = {e.name: e.kind for e in db.scalars(select(FinEntity)).all()}
    assert kinds["Burj Khalifa"] == "other" and kinds["Investment Corporation of Dubai"] == "organization"
    assert kinds["Dubai Financial Market"] == "exchange" and kinds["Emaar Development"] == "company"


def test_expanding_again_uses_the_cache_and_creates_no_duplicates(db) -> None:  # type: ignore[no-untyped-def]
    wd = RecordedWikidata()
    emaar = knowledge.confirm(db, EMAAR, "Emaar", wd)
    knowledge.expand(db, emaar, wd)
    before = len(_edges(db, emaar))
    calls = len(wd.calls)
    knowledge.expand(db, emaar, wd)
    assert len(wd.calls) == calls and len(_edges(db, emaar)) == before


def test_an_ended_statement_is_historical_and_edges_need_evidence(db) -> None:  # type: ignore[no-untyped-def]
    st = {"end": "2019-06-30", "ref_urls": ["https://example.org/annual-report"], "stated_in": []}
    assert knowledge.statement_status(st, today=date(2026, 10, 10)) == "historical"
    assert knowledge.statement_status({**st, "end": "2030-01-01"}, today=date(2026, 10, 10)) == "verified"
    assert knowledge.statement_status({"ref_urls": [], "stated_in": []}) == "uncertain"

    a = knowledge.upsert_entity(db, "Q900000003", "Alpha", None, "company")
    b = knowledge.upsert_entity(db, "Q900000004", "Beta", None, "company")
    with pytest.raises(ValidationFailed):
        knowledge.upsert_edge(db, a, b, "owned_by", "verified", [])
    ev = knowledge.upsert_evidence(db, source_kind="user", title="t", url="javascript:alert(1)", external_id="x1")
    assert ev.url is None  # only web links are stored as links
    with pytest.raises(ValidationFailed):
        knowledge.upsert_edge(db, a, a, "owned_by", "verified", [ev])
    db.rollback()


def test_entity_without_wikidata_id_says_why_nothing_was_fetched(db) -> None:  # type: ignore[no-untyped-def]
    e = FinEntity(kind="commodity", name="Brent crude")
    db.add(e)
    db.flush()
    out = knowledge.expand(db, e, FailingWikidata())
    assert out["fetched"] is False and "no Wikidata id" in out["reason"]
    db.rollback()
