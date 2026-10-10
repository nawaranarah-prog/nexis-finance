"""World Graph and saved-research routes, offline (Wikidata answered from recorded responses)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services import world_graph
from tests.helpers import member

FIX = Path(__file__).parent / "fixtures"
EMAAR, ENBD = "Q1153463", "Q5372575"


@pytest.fixture(autouse=True)
def _recorded_wikidata(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "wikidata_fixtures_dir", str(FIX))


def _entity(client, qid: str) -> dict:  # type: ignore[no-untyped-def]
    r = client.post("/api/world-graph/entities", json={"qid": qid})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/world-graph/resolve?q=Emaar", None),
        ("POST", "/api/world-graph/entities", {"qid": EMAAR}),
        ("GET", "/api/world-graph/entities/1/graph", None),
        ("GET", "/api/world-graph/shared?a=1&b=2", None),
        ("GET", "/api/world-graph/path?a=1&b=2", None),
        ("GET", "/api/world-graph/edges/1", None),
        ("GET", "/api/saved-research", None),
        ("POST", "/api/saved-research", {"kind": "graph", "title": "x"}),
        ("GET", "/api/saved-research/1", None),
        ("DELETE", "/api/saved-research/1", None),
    ],
)
def test_visitors_get_401(client, method, path, body):  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.request(method, path, json=body).status_code == 401


def test_search_offers_choices_then_the_graph_is_evidence_backed(client):  # type: ignore[no-untyped-def]
    member(client, "wg")
    r = client.get("/api/world-graph/resolve", params={"q": "Emaar"}).json()
    assert r["status"] in ("ambiguous", "resolved")
    if r["status"] == "ambiguous":
        assert EMAAR in [c["qid"] for c in r["choices"]]
    e = _entity(client, EMAAR)
    g = client.get(f"/api/world-graph/entities/{e['id']}/graph").json()
    assert g["center"]["name"] == "Emaar Properties" and g["center"]["symbol"] == "EMAAR.AE"
    assert len(g["nodes"]) == 17 and g["nodes"][0]["id"] == e["id"] and g["truncated"] == 0  # 16 distinct + centre
    assert len(g["edges"]) == 16  # two Wikidata statements say Emaar Development is a subsidiary: one edge, two evidence
    assert all(edge["evidence"] for edge in g["edges"])
    assert g["counts"]["verified"] == 1 and g["counts"]["uncertain"] == len(g["edges"]) - 1
    verified = client.get("/api/world-graph/entities/{}/graph".format(e["id"]), params={"status": "verified"}).json()
    assert [x["label"] for x in verified["edges"]] == ["owned by"] and len(verified["nodes"]) == 2
    only_listing = client.get(f"/api/world-graph/entities/{e['id']}/graph", params={"types": "listed_on"}).json()
    assert [n["name"] for n in only_listing["nodes"]] == ["Emaar Properties", "Dubai Financial Market"]
    one = g["edges"][0]
    assert client.get(f"/api/world-graph/edges/{one['id']}").json()["evidence"] == one["evidence"]
    bad = client.get(f"/api/world-graph/entities/{e['id']}/graph", params={"status": "rumour"})
    assert bad.status_code == 422


def test_expanding_a_neighbour_brings_its_relationships(client):  # type: ignore[no-untyped-def]
    member(client, "wgx")
    e = _entity(client, EMAAR)
    g = client.get(f"/api/world-graph/entities/{e['id']}/graph").json()
    icd = next(n for n in g["nodes"] if n["name"] == "Investment Corporation of Dubai")
    assert icd["expandable"] and not icd["expanded"]
    g2 = client.get(f"/api/world-graph/entities/{e['id']}/graph", params={"expanded": str(icd["id"])}).json()
    names = {n["name"]: n for n in g2["nodes"]}
    assert {"ENOC", "The Emirates Group", "Dubai World Trade Centre"} <= set(names)
    assert names["ENOC"]["level"] == 2 and names["Investment Corporation of Dubai"]["expanded"]


def test_the_node_cap_is_respected_and_reported(client, monkeypatch):  # type: ignore[no-untyped-def]
    member(client, "wgc")
    e = _entity(client, EMAAR)
    monkeypatch.setattr(world_graph, "MAX_NODES", 5)
    g = client.get(f"/api/world-graph/entities/{e['id']}/graph").json()
    assert len(g["nodes"]) == 5 and g["truncated"] == 12
    # better evidence keeps its place: the one referenced edge survives the cut
    assert any(n["name"] == "Burj Khalifa" for n in g["nodes"])


def test_shared_connections_and_paths_only_use_unreferenced_links_when_asked(client):  # type: ignore[no-untyped-def]
    member(client, "wgp")
    emaar = _entity(client, EMAAR)
    enbd = _entity(client, ENBD)
    strict = client.get("/api/world-graph/shared", params={"a": emaar["id"], "b": enbd["id"]}).json()
    assert strict["shared"] == [] and strict["statuses"] == ["verified"]  # Emaar's country is unreferenced on Wikidata
    loose = client.get("/api/world-graph/shared", params={"a": emaar["id"], "b": enbd["id"], "unreferenced": True}).json()
    assert [s["entity"]["name"] for s in loose["shared"]] == ["United Arab Emirates"]
    assert loose["shared"][0]["via_b"][0]["status"] == "verified" and loose["shared"][0]["via_a"][0]["status"] == "uncertain"

    assert client.get("/api/world-graph/path", params={"a": emaar["id"], "b": enbd["id"]}).json()["found"] is False
    p = client.get("/api/world-graph/path", params={"a": emaar["id"], "b": enbd["id"], "unreferenced": True}).json()
    assert p["found"] and [s["to"]["name"] for s in p["steps"]] == ["United Arab Emirates", "Emirates NBD"]
    same = client.get("/api/world-graph/path", params={"a": emaar["id"], "b": emaar["id"]})
    assert same.status_code == 422


def test_saved_investigations_are_private_and_notice_changes(client):  # type: ignore[no-untyped-def]
    member(client, "wgs1")
    e = _entity(client, EMAAR)
    g = client.get(f"/api/world-graph/entities/{e['id']}/graph").json()
    body = {"kind": "graph", "title": "Emaar ownership", "entity_id": e["id"],
            "state": {"depth": 1, "expanded": [], "signature": g["signature"]}}  # fmt: skip
    item = client.post("/api/saved-research", json=body).json()
    assert item["title"] == "Emaar ownership" and item["entity"]["id"] == e["id"]
    assert [i["id"] for i in client.get("/api/saved-research", params={"kind": "graph"}).json()] == [item["id"]]
    same = client.get(f"/api/world-graph/entities/{e['id']}/graph", params={"saved": item["id"]}).json()
    assert same["since_saved"] == {"changed": False, "added": 0, "removed": 0, "status_changed": 0}
    old = {**body["state"], "signature": g["signature"][1:]}  # pretend one relationship appeared since saving
    client.patch(f"/api/saved-research/{item['id']}", json={"state": old, "notes": "check ICD stake"})
    later = client.get(f"/api/world-graph/entities/{e['id']}/graph", params={"saved": item["id"]}).json()
    assert later["since_saved"]["changed"] and later["since_saved"]["added"] == 1

    member(client, "wgs2")  # someone else
    assert client.get(f"/api/saved-research/{item['id']}").status_code == 404
    assert client.patch(f"/api/saved-research/{item['id']}", json={"title": "mine now"}).status_code == 404
    assert client.delete(f"/api/saved-research/{item['id']}").status_code == 404
    assert client.get(f"/api/world-graph/entities/{e['id']}/graph", params={"saved": item["id"]}).status_code == 404
    assert client.get("/api/saved-research").json() == []


def test_saved_items_reject_oversized_state_and_unknown_fields(client):  # type: ignore[no-untyped-def]
    member(client, "wgv")
    big = {"kind": "graph", "title": "x", "state": {"blob": "a" * 30_000}}
    assert client.post("/api/saved-research", json=big).status_code == 422
    assert client.post("/api/saved-research", json={"kind": "graph", "title": "x", "owner": 1}).status_code == 422
    assert client.post("/api/saved-research", json={"kind": "graph", "title": "x", "entity_id": 999999}).status_code == 404
    item = client.post("/api/saved-research", json={"kind": "graph", "title": "ok"}).json()
    assert client.delete(f"/api/saved-research/{item['id']}").status_code == 204
    assert client.get(f"/api/saved-research/{item['id']}").status_code == 404
