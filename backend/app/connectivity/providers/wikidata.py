"""Wikidata: entity search and typed company relationships (ownership, subsidiaries, listing, industry, country).

Official, keyless, free. Data is CC0. The query service allows 60 s of processing per 60 s and 5 parallel queries
per IP, with a 60 s timeout per query, and asks every client to send a descriptive User-Agent. We send one query
at a time, at most one per second, and back off on HTTP 429. Results are cached by the caller.

Each statement comes back with its qualifiers (start/end time, ticker) and references (reference URL, "stated in").
Wikidata is community-edited: a statement with a reference is still only as good as that reference, and a statement
without one is reported as unreferenced — never as verified.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from app.connectivity.base import FinancialConnectionProvider, ProviderSpec
from app.core.config import get_settings
from app.core.errors import ProviderError, ValidationFailed

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
QID = re.compile(r"^Q[1-9]\d{0,11}$")

# property -> (canonical relationship, direction). "out": the subject is the edge's source; "rev": the edge runs
# from the statement's value to the subject, so "Emaar has subsidiary X" is stored as "X subsidiary_of Emaar".
PROPERTIES: dict[str, tuple[str, str, str]] = {
    "P749": ("subsidiary_of", "out", "parent organization"),
    "P355": ("subsidiary_of", "rev", "has subsidiary"),
    "P127": ("owned_by", "out", "owned by"),
    "P1830": ("owned_by", "rev", "owner of"),
    "P414": ("listed_on", "out", "stock exchange"),
    "P452": ("in_industry", "out", "industry"),
    "P17": ("located_in", "out", "country"),
}
INCOMING = ("P749", "P127", "P355", "P1830")  # statements made on *other* items about this one
# What the *other* item is, by property and direction. Owned things are often buildings or brands, so they stay "other";
# owners can be funds, states or people, so "organization" is as far as we go without asking.
OTHER_KIND = {("P414", "out"): "exchange", ("P452", "out"): "industry", ("P17", "out"): "country",
              ("P749", "out"): "company", ("P355", "out"): "company", ("P749", "in"): "company", ("P355", "in"): "company",
              ("P127", "out"): "organization", ("P1830", "in"): "organization"}  # fmt: skip

_lock = threading.Lock()
_last_call = [0.0]


def check_qid(qid: str) -> str:
    q = (qid or "").strip().upper()
    if not QID.match(q):
        raise ValidationFailed("Not a Wikidata item id (expected Q followed by digits).")
    return q


def relations_query(qid: str) -> str:
    # One explicit pattern per property (rather than a generic claim join): the incoming ones start from the value
    # index, which keeps the query cheap enough to stay far inside the service's processing budget.
    blocks = [
        f'{{ wd:{qid} p:{p} ?st . ?st ps:{p} ?other ; wikibase:rank ?rank . BIND(wd:{p} AS ?prop) BIND("out" AS ?dir) }}'
        for p in PROPERTIES
    ]
    blocks += [
        f'{{ ?st ps:{p} wd:{qid} . ?other p:{p} ?st . ?st wikibase:rank ?rank . BIND(wd:{p} AS ?prop) BIND("in" AS ?dir) }}'
        for p in INCOMING
    ]
    union = "\n  UNION ".join(blocks)
    return f"""SELECT ?dir ?prop ?other ?otherLabel ?otherDescription ?st ?start ?end ?ticker ?refUrl ?statedInLabel WHERE {{
  hint:Query hint:optimizer "None" .
  {union}
  FILTER(isIRI(?other) && ?rank != wikibase:DeprecatedRank)
  OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }} OPTIONAL {{ ?st pq:P249 ?ticker }}
  OPTIONAL {{ ?st prov:wasDerivedFrom ?ref . OPTIONAL {{ ?ref pr:P854 ?refUrl }} OPTIONAL {{ ?ref pr:P248 ?statedIn }} }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} LIMIT 400"""


def _v(row: dict[str, Any], key: str) -> str | None:
    cell = row.get(key)
    return cell.get("value") if isinstance(cell, dict) else None


def _qid_of(uri: str | None) -> str | None:
    if not uri:
        return None
    tail = uri.rsplit("/", 1)[-1]
    return tail if QID.match(tail) else None


def parse_relations(qid: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Group SPARQL rows into one record per statement, with every reference URL / source collected."""
    stmts: dict[str, dict[str, Any]] = {}
    for row in (data.get("results") or {}).get("bindings") or []:
        prop = _qid_of_prop(_v(row, "prop"))
        other = _qid_of(_v(row, "other"))
        st = _v(row, "st")
        if not prop or prop not in PROPERTIES or not other or not st or other == qid:
            continue
        direction = _v(row, "dir") or "out"
        rel, sense, label = PROPERTIES[prop]
        rec = stmts.get(st)
        if rec is None:
            # whose statement it is decides which way round the edge goes
            subject_is_source = (direction == "out") == (sense == "out")
            rec = stmts[st] = {
                "statement": st.rsplit("/", 1)[-1],
                "on_item": qid if direction == "out" else other,
                "property": prop,
                "property_label": label,
                "rel_type": rel,
                "source_qid": qid if subject_is_source else other,
                "target_qid": other if subject_is_source else qid,
                "other": {
                    "qid": other,
                    "name": (_v(row, "otherLabel") or other)[:200],
                    "description": (_v(row, "otherDescription") or None),
                    "kind": OTHER_KIND.get((prop, direction), "other"),
                },
                "start": (_v(row, "start") or "")[:10] or None,
                "end": (_v(row, "end") or "")[:10] or None,
                "ticker": _v(row, "ticker"),
                "ref_urls": [],
                "stated_in": [],
            }
        url, stated = _v(row, "refUrl"), _v(row, "statedInLabel")
        if url and url not in rec["ref_urls"]:
            rec["ref_urls"].append(url)
        if stated and stated not in rec["stated_in"] and not QID.match(stated):
            rec["stated_in"].append(stated)
    return list(stmts.values())


def _qid_of_prop(uri: str | None) -> str | None:
    if not uri:
        return None
    tail = uri.rsplit("/", 1)[-1]
    return tail if re.match(r"^P\d+$", tail) else None


class WikidataProvider(FinancialConnectionProvider):
    spec = ProviderSpec(
        key="wikidata",
        name="Wikidata — entities and company relationships",
        category="regulatory",
        description="Company identity, parent/subsidiary and ownership statements, stock-exchange listings, industry and country, with the references editors attached.",
        capabilities=("entity_search", "relationships"),
        auth_type="none",
        requirements=("Internet access", "Descriptive User-Agent (NEXIS_WIKIDATA_USER_AGENT)"),
        docs_url="https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/query_limits",
        implemented=True,
        verification="live",
        data_class="real_external",
        notes="Community-edited, CC0. Unreferenced statements are shown as unreferenced, not as verified facts.",
    )

    def _headers(self, accept: str) -> dict[str, str]:
        return {"User-Agent": get_settings().wikidata_user_agent, "Accept": accept}

    def _get(self, url: str, params: dict[str, Any], accept: str = "application/json") -> Any:
        if not get_settings().external_data_enabled:
            raise ProviderError("Wikidata: external data is switched off on this server")
        with _lock:  # one request at a time, at least a second apart (well inside the published limits)
            wait = 1.0 - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.monotonic()
            resp = self.http_get(url, params=params, headers=self._headers(accept))
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError("Wikidata: invalid JSON response") from exc

    def verify(self) -> dict[str, Any]:
        return {"results": len(self.search("Emaar"))}

    def search(self, text: str, limit: int = 7) -> list[dict[str, Any]]:
        text = (text or "").strip()[:120]
        if not text:
            return []
        data = self._get(
            API,
            {
                "action": "wbsearchentities",
                "search": text,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": max(1, min(limit, 10)),
                "format": "json",
            },
        )
        return parse_search(data)

    def relations(self, qid: str) -> list[dict[str, Any]]:
        qid = check_qid(qid)
        data = self._get(SPARQL, {"query": relations_query(qid), "format": "json"}, accept="application/sparql-results+json")
        return parse_relations(qid, data)


def parse_search(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in data.get("search") or []:
        q = r.get("id")
        if not q or not QID.match(q):
            continue
        out.append({"qid": q, "name": (r.get("label") or q)[:200], "description": (r.get("description") or None)})
    return out
