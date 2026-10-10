"""PDF reports from saved research runs: owner-only, completed runs only, safe with any text."""

from __future__ import annotations

import json

import pytest

from app.services import llm, markets
from app.services import time_machine as tm
from tests.helpers import member
from tests.test_devils_advocate import DETAILS, NEWS, THESIS, ai_answer
from tests.test_time_machine import _scenario, closes


@pytest.fixture
def offline(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tm, "market_closes", lambda db: closes)
    monkeypatch.setattr(markets, "details", lambda db, s: DETAILS)
    monkeypatch.setattr(markets, "news", lambda db, s: NEWS)
    monkeypatch.setattr(
        llm, "chat", lambda *a, **k: {"content": json.dumps(ai_answer()), "_provider": "mock", "_model": "mock-1"}
    )


def _pdf(client, run_id: int) -> bytes:  # type: ignore[no-untyped-def]
    r = client.post(f"/api/saved-research/runs/{run_id}/report")
    assert r.status_code == 201, r.text
    d = client.get(r.json()["download_url"])
    assert d.status_code == 200 and d.content.startswith(b"%PDF")
    return d.content


def test_scenario_and_thesis_reports_are_private_pdfs(client, offline):  # type: ignore[no-untyped-def]
    member(client, "rr")
    run = client.post(
        "/api/time-machine/scenarios", json={"title": "Oil − 20% → test β", "inputs": _scenario(factor_shocks={"oil": -20})}
    ).json()["run"]
    _pdf(client, run["id"])

    tid = client.post("/api/devils-advocate/theses", json={"symbol": "EMAAR.AE", "thesis": THESIS + " إعمار"}).json()["item"][
        "id"
    ]
    trun = client.post(f"/api/devils-advocate/theses/{tid}/analyse").json()["run"]
    rid = client.post(f"/api/saved-research/runs/{trun['id']}/report").json()["id"]

    member(client, "rr2")
    assert client.post(f"/api/saved-research/runs/{trun['id']}/report").status_code == 404
    assert client.get(f"/api/reports/{rid}/download").status_code == 404


def test_failed_runs_are_not_reported(client, offline, monkeypatch):  # type: ignore[no-untyped-def]
    member(client, "rrf")
    tid = client.post("/api/devils-advocate/theses", json={"symbol": "EMAAR.AE", "thesis": THESIS}).json()["item"]["id"]

    def down(*a, **k):  # type: ignore[no-untyped-def]
        raise llm.LLMUnavailable("down")

    monkeypatch.setattr(llm, "chat", down)
    failed = client.post(f"/api/devils-advocate/theses/{tid}/analyse").json()["run"]
    assert client.post(f"/api/saved-research/runs/{failed['id']}/report").status_code == 422
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.post(f"/api/saved-research/runs/{failed['id']}/report").status_code == 401
