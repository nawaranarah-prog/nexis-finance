"""Devil's Advocate: evidence gathering, the citation validator, failure handling and the member-only routes.
The AI is mocked; what's tested is that only verifiable citations survive and nothing is shown when the AI fails."""

from __future__ import annotations

import json

import pytest

from app.services import devils_advocate as da
from app.services import llm, markets
from tests.helpers import member

THESIS = "Emaar will keep rising because Dubai property sales stay strong and the dividend yield of 5.2% is safe."
DETAILS = {
    "name": "Emaar Properties", "currency": "AED",
    "quote": {"price": 14.85, "week52_low": 11.2, "week52_high": 15.9, "ma50": 14.1, "ma200": 13.6, "market_time": "2026-10-09T12:00:00Z"},
    "valuation": {"pe_trailing": 9.4, "pe_forward": 8.8, "price_to_book": 1.6, "ev_to_ebitda": 6.1, "market_cap": 131_000_000_000},
    "financials": {"revenue_growth": 0.31, "earnings_growth": -0.04, "profit_margin": 0.37, "return_on_equity": 0.18, "debt_to_equity_pct": 21.0},
    "dividends": {"yield": 5.2, "rate": 0.75, "payout_ratio": 0.48},
    "analysts": {"count": 11, "recommendation": "buy", "target_mean": 17.2, "target_low": 12.0, "target_high": 20.5},
}  # fmt: skip
NEWS = {"items": [
    {"title": "Dubai property transactions hit a record in September", "publisher": "Gulf News", "url": "https://example.org/a"},
    {"title": "Analysts warn of oversupply as Dubai launches surge", "publisher": "Reuters", "url": "https://example.org/b"},
]}  # fmt: skip


def ai_answer(claims_fixed: bool = False) -> dict:
    return {
        "claims": [{"id": "C1", "text": "Dubai property sales stay strong", "kind": "assumption"},
                   {"id": "C2", "text": "The dividend yield of 5.2% is safe", "kind": "prediction"},
                   {"id": "C3", "text": "Emaar's earnings keep growing", "kind": "prediction"}],
        "assessments": [
            {"claim": "C1", "for": [{"evidence": "E6", "quote": "Dubai property transactions hit a record"}],
             "against": [{"evidence": "E7", "quote": "Analysts warn of oversupply"}], "comment": "Mixed signals."},
            {"claim": "C2", "for": [{"evidence": "E4", "quote": "Payout ratio 48.0%"}],
             "against": [{"evidence": "E4", "quote": "the board plans to cut the dividend"}], "comment": "Payout 48% leaves room."},  # fabricated quote
            {"claim": "C3", "for": [], "against": [{"evidence": "E3", "quote": "Earnings growth -4.0%"}], "comment": "Earnings fell 4%."},
        ],
        "counter_case": [
            {"point": "Supply could outrun demand and cap prices.", "citations": [{"evidence": "E7", "quote": "warn of oversupply as Dubai launches surge"}]},
            {"point": "Rates will hit 9% next year.", "citations": [{"evidence": "E99", "quote": "rates will rise sharply"}]},  # no such item
            {"point": "Margins will halve to 18%.", "citations": [{"evidence": "E3", "quote": "Profit"}]},  # quote too short
        ],
        "risks": [{"risk": "Earnings already falling (−4.0%) while the price sits near its 52-week high of 15.90, with 23% downside.",
                   "citations": [{"evidence": "E3", "quote": "Earnings growth -4.0%"}, {"evidence": "E1", "quote": "52-week high 15.90"}]}],
        "signals": [{"signal": "Monthly Dubai transaction volumes falling for 3 months", "direction": "weakens"}],
        "gaps": ["Headlines only — the articles themselves were not available."],
    }  # fmt: skip


@pytest.fixture
def offline(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(markets, "details", lambda db, s: DETAILS)
    monkeypatch.setattr(markets, "news", lambda db, s: NEWS)
    calls: list[list[dict]] = []

    def chat(messages, **kw):  # type: ignore[no-untyped-def]
        calls.append(messages)
        return {"content": "Here you go:\n" + json.dumps(ai_answer()), "_provider": "mock", "_model": "mock-1"}

    monkeypatch.setattr(llm, "chat", chat)
    return calls


# ---------------------------------------------------------------------------------------------- unit


def test_numbers_are_normalised_without_mangling_whole_numbers() -> None:
    assert da.numbers("120 and 12") == {"120", "12"}
    assert da.numbers("7.20 AED, +5.2%, 1,250.0 and −4.0%") == {"7.2", "5.2", "1250", "4"}
    assert da.numbers("in 2026 and 2027") == set()  # years aren't figures


def _labels(db) -> dict:  # type: ignore[no-untyped-def]
    evs = da.gather(db, "EMAAR.AE")
    return {f"E{i}": {"id": e.id, "label": f"E{i}", "source_kind": e.source_kind, "title": e.title, "excerpt": e.excerpt or "",
                      "publisher": e.publisher, "url": e.url, "published_at": None, "retrieved_at": ""} for i, e in enumerate(evs, 1)}  # fmt: skip


def test_evidence_is_gathered_as_separate_bounded_items(client, offline) -> None:  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as db:
        labels = _labels(db)
    kinds = [v["source_kind"] for v in labels.values()]
    assert kinds[:5] == ["market_data"] * 5 and kinds.count("news") == 2
    assert "Dividend yield 5.20%" in labels["E4"]["excerpt"] and "Payout ratio 48.0%" in labels["E4"]["excerpt"]
    assert labels["E6"]["excerpt"] == "Headline: Dubai property transactions hit a record in September"
    assert all(len(v["excerpt"]) <= 600 for v in labels.values())


def test_only_verbatim_citations_survive_and_statuses_come_from_them(client, offline) -> None:  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as db:
        labels = _labels(db)
    out = da.validate(ai_answer(), labels, THESIS, None)
    c = {x["id"]: x for x in out["claims"]}
    assert c["C1"]["status"] == "mixed"
    assert c["C2"]["status"] == "supported" and c["C2"]["against"] == []  # the invented "cut the dividend" quote is gone
    assert c["C3"]["status"] == "contradicted"
    assert [p["text"] for p in out["counter_case"]] == ["Supply could outrun demand and cap prices."]
    assert out["validation"] == {"citations": 10, "dropped": 3, "points_dropped": 2}  # 2+2+1 on claims, 3 counter-case, 2 risk
    assert out["risks"][0]["unverified_numbers"] == ["23"]  # "23% downside" appears in no evidence
    assert c["C2"]["unverified_numbers"] == []  # 48 is in the evidence


def test_fixed_claims_are_kept_across_runs(client, offline) -> None:  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    fixed = [{"id": "C1", "text": "Dubai property sales stay strong", "kind": "assumption"}]
    with db_session.SessionLocal() as db:
        out = da.validate(ai_answer(), _labels(db), THESIS, fixed)
    assert [x["id"] for x in out["claims"]] == ["C1"]  # the AI's extra claims are ignored


# ---------------------------------------------------------------------------------------------- routes


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/api/devils-advocate/theses"), ("GET", "/api/devils-advocate/theses/1"), ("PATCH", "/api/devils-advocate/theses/1"),
     ("POST", "/api/devils-advocate/theses/1/analyse"), ("GET", "/api/devils-advocate/runs/1")],
)  # fmt: skip
def test_visitors_get_401(client, method, path):  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.request(method, path, json={"symbol": "EMAAR.AE", "thesis": THESIS}).status_code == 401


def test_a_thesis_is_challenged_tracked_and_private(client, offline):  # type: ignore[no-untyped-def]
    member(client, "da")
    t = client.post("/api/devils-advocate/theses", json={"symbol": "emaar.ae", "thesis": THESIS, "horizon": "3 years"})
    assert t.status_code == 201, t.text
    tid = t.json()["item"]["id"]
    assert t.json()["item"]["state"]["symbol"] == "EMAAR.AE" and t.json()["runs"] == []

    first = client.post(f"/api/devils-advocate/theses/{tid}/analyse").json()
    assert first["run"]["status"] == "completed" and first["run"]["model_version"] == da.MODEL_VERSION
    assert first["item"]["state"]["claims"][0] == {"id": "C1", "text": "Dubai property sales stay strong", "kind": "assumption"}
    assert len(first["run"]["snapshot"]["evidence"]) == 7  # the run keeps the evidence it used

    second = client.post(f"/api/devils-advocate/theses/{tid}/analyse").json()
    assert "Use exactly these claims" in offline[-1][1]["content"]
    assert [c["changed"] for c in second["changes_since_previous"]] == [False, False, False]
    assert len(second["runs"]) == 2

    patched = client.patch(f"/api/devils-advocate/theses/{tid}", json={"thesis": THESIS + " Also, rents keep rising."}).json()
    assert patched["item"]["state"]["claims"] is None  # rewritten thesis -> claims extracted afresh next time

    member(client, "da2")
    assert client.get(f"/api/devils-advocate/theses/{tid}").status_code == 404
    assert client.post(f"/api/devils-advocate/theses/{tid}/analyse").status_code == 404
    assert client.get(f"/api/devils-advocate/runs/{first['run']['id']}").status_code == 404


def test_when_the_ai_fails_nothing_is_concluded(client, offline, monkeypatch):  # type: ignore[no-untyped-def]
    member(client, "daf")
    tid = client.post("/api/devils-advocate/theses", json={"symbol": "EMAAR.AE", "thesis": THESIS}).json()["item"]["id"]

    def down(*a, **k):  # type: ignore[no-untyped-def]
        raise llm.LLMUnavailable("all providers down")

    monkeypatch.setattr(llm, "chat", down)
    r = client.post(f"/api/devils-advocate/theses/{tid}/analyse").json()["run"]
    assert r["status"] == "failed" and "could not be completed" in r["output"]["error"] and "claims" not in r["output"]

    monkeypatch.setattr(llm, "chat", lambda *a, **k: {"content": "I think it's a great stock!"})
    r = client.post(f"/api/devils-advocate/theses/{tid}/analyse").json()
    assert r["run"]["status"] == "failed" and r["item"]["state"]["claims"] is None

    monkeypatch.setattr(markets, "details", lambda db, s: (_ for _ in ()).throw(markets.ProviderError("down")))
    monkeypatch.setattr(markets, "news", lambda db, s: {"items": []})
    tid2 = client.post("/api/devils-advocate/theses", json={"symbol": "NODATA.AE", "thesis": THESIS}).json()["item"]["id"]
    r = client.post(f"/api/devils-advocate/theses/{tid2}/analyse").json()["run"]
    assert r["status"] == "failed" and "Not enough evidence" in r["output"]["error"]


def test_inputs_and_daily_limit(client, offline, monkeypatch):  # type: ignore[no-untyped-def]
    from app.api.routes import devils_advocate as route

    member(client, "dal")
    assert client.post("/api/devils-advocate/theses", json={"symbol": "EMAAR.AE", "thesis": "too short"}).status_code == 422
    assert (
        client.post("/api/devils-advocate/theses", json={"symbol": "EMAAR.AE", "thesis": THESIS, "owner": 1}).status_code == 422
    )
    tid = client.post("/api/devils-advocate/theses", json={"symbol": "EMAAR.AE", "thesis": THESIS}).json()["item"]["id"]
    monkeypatch.setattr(route, "DAILY", 2)
    assert [client.post(f"/api/devils-advocate/theses/{tid}/analyse").status_code for _ in range(3)] == [201, 201, 429]


def test_a_cut_off_answer_is_retried_once_and_diagnosed(client, offline, monkeypatch):  # type: ignore[no-untyped-def]
    """Production showed a reasoning model running out of tokens mid-JSON: retry once, and record why."""
    member(client, "dar")
    tid = client.post("/api/devils-advocate/theses", json={"symbol": "EMAAR.AE", "thesis": THESIS}).json()["item"]["id"]
    full = json.dumps(ai_answer())
    replies = iter(
        [{"content": full[: len(full) // 2], "_finish": "length"}, {"content": "```json\n" + full + "\n```", "_finish": "stop"}]
    )
    seen: list = []
    monkeypatch.setattr(llm, "chat", lambda messages, **k: seen.append((messages, k)) or next(replies))
    run = client.post(f"/api/devils-advocate/theses/{tid}/analyse").json()["run"]
    assert run["status"] == "completed" and len(seen) == 2 and seen[0][1]["max_tokens"] == da.MAX_TOKENS
    assert (
        run["output"]["diagnostics"][0]["finish_reason"] == "length"
        and "cut-off JSON" in run["output"]["diagnostics"][0]["problem"]
    )
    assert "only the JSON object" in seen[1][0][-1]["content"]

    monkeypatch.setattr(llm, "chat", lambda messages, **k: {"content": "", "_finish": "length"})
    failed = client.post(f"/api/devils-advocate/theses/{tid}/analyse").json()["run"]
    assert (
        failed["status"] == "failed"
        and [d["problem"] for d in failed["output"]["diagnostics"]] == ["no JSON object in the answer (empty answer)"] * 2
    )


def test_parse_json_finds_the_object_in_any_wrapping() -> None:
    assert da.parse_json('Sure!\n```json\n{"a": 1}\n```\nHope that helps {x}') == {"a": 1}
    with pytest.raises(ValueError, match="cut-off"):
        da.parse_json('{"a": [1, 2')
