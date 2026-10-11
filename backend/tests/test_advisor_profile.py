"""AI Advisor personalisation: the investor profile (scored by code), the profile in the advisor's context, the
advice log and its automatic review. The AI is mocked; what's tested is what the advisor is given and what is kept."""

from __future__ import annotations

import json

import pytest

from app.core.errors import ValidationFailed
from app.services import advice_log, investor_profile, llm, portfolio_analytics
from tests.helpers import member

LOW = {"drop": "sell_all", "priority": "protect", "income_stability": "unstable", "experience": "none", "share": "most"}
HIGH = {"drop": "buy_more", "priority": "max_growth", "income_stability": "stable", "experience": "experienced", "share": "small"}
PROFILE = {"goals": ["retirement", "income"], "horizon": "over_7y", "monthly_budget_aed": 2000, "savings_band": "50k_250k",
           "constraints": ["sharia"], "answers": HIGH}  # fmt: skip
GOOD = ("For a growth profile with a 7+ year horizon, Emaar is a single cyclical stock... The trade-offs are...\n\n"
        "*Educational guidance tailored to the profile you gave, based on public data — not a recommendation to buy or sell.*")  # fmt: skip


def test_risk_band_is_computed_by_code() -> None:
    assert investor_profile.score(LOW, "over_7y") == (5, "cautious")
    assert investor_profile.score(HIGH, "over_7y") == (25, "adventurous")
    assert investor_profile.score(HIGH, "under_1y") == (25, "balanced")  # money needed soon caps the band
    mid = {**LOW, "drop": "wait", "priority": "balance", "income_stability": "somewhat"}
    assert investor_profile.score(mid, "3_7y") == (11, "balanced")  # 3 + 3 + 3 + 1 + 1
    capacity_only = {
        "drop": "hold_add_small",
        "priority": "growth",
        "income_stability": "stable",
        "experience": "some",
        "share": "small",
    }
    assert investor_profile.score(capacity_only, "over_7y") == (21, "growth")  # high score, no high-risk appetite
    assert investor_profile.score({**capacity_only, "priority": "max_growth"}, "over_7y") == (22, "adventurous")
    with pytest.raises(ValidationFailed):
        investor_profile.score({**HIGH, "drop": "panic"}, "over_7y")


@pytest.mark.parametrize(
    ("answer", "flag"),
    [("You should buy Emaar now.", "tells the member to buy or sell"),
     ("I'd buy some, in stages.", "gives a personal buy/sell call"),
     ("Put 20% of your savings into EMAAR.AE.", "prescribes an amount for a specific holding"),
     ("This is a guaranteed return.", "promises returns"),
     ("Emaar looks fine.", "missing the educational disclaimer")],
)  # fmt: skip
def test_review_flags_instructions_and_missing_disclaimer(answer: str, flag: str) -> None:
    assert flag in advice_log.review(answer)


def test_review_passes_educational_answers() -> None:
    assert advice_log.review(GOOD) == []
    ok = ("Some investors put 10% into a diversified fund first; buying in stages is one way to reduce timing risk. "
          "Educational guidance — not a recommendation to buy or sell.")  # fmt: skip
    assert advice_log.review(ok) == []


def test_profile_routes_are_private_and_validated(client):  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/me/investor-profile").status_code == 401
    assert client.put("/api/me/investor-profile", json=PROFILE).status_code == 401
    assert client.get("/api/me/advice-log").status_code == 401
    member(client, "prof")
    first = client.get("/api/me/investor-profile").json()
    assert first["profile"] is None and len(first["questions"]) == 5 and "cautious" in first["bands"]
    saved = client.put("/api/me/investor-profile", json=PROFILE).json()["profile"]
    assert (saved["risk_score"], saved["risk_band"], saved["constraints"]) == (25, "adventurous", ["sharia"])
    assert (
        client.put("/api/me/investor-profile", json={**PROFILE, "risk_band": "cautious"}).status_code == 422
    )  # server computes it
    assert client.put("/api/me/investor-profile", json={**PROFILE, "horizon": "forever"}).status_code == 422
    assert client.put("/api/me/investor-profile", json={**PROFILE, "answers": {**HIGH, "drop": None}}).status_code == 422


def _mock_llm(monkeypatch, answer: str):  # type: ignore[no-untyped-def]
    seen: list[list[dict]] = []

    def chat_stream(convo, tools, max_tokens=0):  # type: ignore[no-untyped-def]
        seen.append(convo)
        yield ("text", answer)
        yield ("message", {"content": answer, "_provider": "mock", "_model": "mock-1"})

    monkeypatch.setattr(llm, "chat_stream", chat_stream)
    return seen


def test_the_advisor_gets_the_profile_and_every_answer_is_logged(client, monkeypatch):  # type: ignore[no-untyped-def]
    pos = [{"symbol": "EMAAR.AE", "name": "Emaar", "priced": True, "value_usd": 6000.0},
           {"symbol": "AAPL", "name": "Apple", "priced": True, "value_usd": 4000.0}]  # fmt: skip
    monkeypatch.setattr(portfolio_analytics, "_positions", lambda db, user: (pos, {}))
    seen = _mock_llm(monkeypatch, GOOD)
    member(client, "adv")
    client.put("/api/me/investor-profile", json=PROFILE)
    r = client.post("/api/advisor/chat", json={"messages": [{"role": "user", "content": "Should I buy Emaar?"}]})
    assert r.status_code == 200, r.text
    system = seen[-1][0]["content"]
    assert "Risk profile: adventurous (score 25/25" in system and "Sharia-compliant investments only" in system
    assert "AED 2,000 a month" in system and "EMAAR.AE 60%" in system and "Largest position is 60%" in system
    assert (
        "Do\n  NOT tell the member to buy, sell or hold a specific security" in system or "NOT tell the member to buy" in system
    )

    log = client.get("/api/me/advice-log").json()["items"]
    assert log[0]["question"] == "Should I buy Emaar?" and log[0]["answer"].startswith("For a growth profile")
    assert log[0]["profile"]["risk_band"] == "adventurous" and log[0]["mode"] == "ai" and log[0]["flags"] == []

    _mock_llm(monkeypatch, "You should buy Emaar now, it's cheap.")
    client.post("/api/advisor/chat", json={"messages": [{"role": "user", "content": "Is Emaar cheap?"}]})
    drift = client.get("/api/me/advice-log").json()["items"][0]
    assert "tells the member to buy or sell" in drift["flags"] and "missing the educational disclaimer" in drift["flags"]

    member(client, "adv2")  # someone else sees nothing of it
    assert client.get("/api/me/advice-log").json()["items"] == []


def test_without_a_profile_the_advisor_is_told_so(client, monkeypatch):  # type: ignore[no-untyped-def]
    seen = _mock_llm(monkeypatch, GOOD)
    member(client, "noprof")
    client.post("/api/advisor/chat", json={"messages": [{"role": "user", "content": "What is a sukuk?"}]})
    assert "hasn't completed an investor profile" in seen[-1][0]["content"]
    assert client.get("/api/me/advice-log").json()["items"][0]["profile"] is None
    assert json.dumps(seen[-1][0]["content"]).count("Current date and time") == 1


def test_review_flags_judging_the_amount_seen_in_production() -> None:
    # wording from a real production answer (2026-10-11)
    assert "judges the amount for the member" in advice_log.review(
        "500 shares (AED 5,690) would be a meaningful but not extreme position for you. Educational guidance — not a recommendation to buy or sell."
    )
    assert "judges the amount for the member" in advice_log.review(
        "In isolation, that position size is reasonable for a single UAE blue chip. Educational guidance — not a recommendation."
    )
