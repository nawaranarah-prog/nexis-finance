"""Investor profile: a short questionnaire, scored by code, that the AI Advisor tailors its guidance to.

Five risk questions (1–5 points each) give a score of 5–25 and a band:

* 5–9  **cautious**    — capital preservation first; a fall of 10% would be hard to live with.
* 10–14 **balanced**   — some ups and downs accepted for steadier growth.
* 15–19 **growth**     — long horizon, comfortable with sizeable falls along the way.
* 20–25 **adventurous** — seeks higher returns and accepts large, lasting losses.

Two caps keep the label honest: a short horizon caps the band at "balanced" (money needed within a year shouldn't be in
volatile assets, whatever the appetite), and "adventurous" needs an explicitly high-risk answer — buying more after a
20% fall or wanting the highest possible growth — otherwise a high score that comes from *capacity* (stable income, a
small share of savings) rather than *appetite* is capped at "growth". The profile is used to tailor *general* guidance: what to weigh, how an idea fits the stated situation and
what's commonly considered appropriate for a profile like it. It doesn't make Nexis a licensed adviser.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NexisError, ValidationFailed
from app.models import InvestorProfile, User

QUESTIONS: list[dict[str, Any]] = [
    {"key": "drop", "text": "If your investments fell 20% in a few months, you would…",
     "options": [("sell_all", "Sell everything to stop the losses", 1), ("sell_some", "Sell some", 2), ("wait", "Wait and do nothing", 3),
                 ("hold_add_small", "Hold, and maybe add a little", 4), ("buy_more", "Buy more while prices are low", 5)]},
    {"key": "priority", "text": "What matters most to you?",
     "options": [("protect", "Not losing money", 1), ("income", "Steady income", 2), ("balance", "A balance of growth and safety", 3),
                 ("growth", "Growth over time", 4), ("max_growth", "The highest possible growth", 5)]},
    {"key": "income_stability", "text": "How stable is your income?",
     "options": [("unstable", "Irregular or uncertain", 1), ("somewhat", "Somewhat stable", 3), ("stable", "Stable, with savings for emergencies", 5)]},
    {"key": "experience", "text": "Your investing experience",
     "options": [("none", "None yet", 1), ("some", "Some (funds, a few shares)", 3), ("experienced", "Experienced (several years, different assets)", 5)]},
    {"key": "share", "text": "What share of your savings would you invest this way?",
     "options": [("most", "Most of it", 1), ("half", "About half", 3), ("small", "A small part", 5)]},
]  # fmt: skip
HORIZONS = {"under_1y": "under 1 year", "1_3y": "1–3 years", "3_7y": "3–7 years", "over_7y": "over 7 years"}
SAVINGS = {"under_50k": "under AED 50,000", "50k_250k": "AED 50,000–250,000", "250k_1m": "AED 250,000–1 million", "over_1m": "over AED 1 million"}
GOALS = {"retirement": "retirement", "home": "buying a home", "education": "education", "income": "regular income", "wealth": "growing wealth",
         "emergency": "an emergency fund", "other": "other"}  # fmt: skip
CONSTRAINTS = {"sharia": "Sharia-compliant investments only", "no_crypto": "no crypto", "uae_focus": "prefers UAE markets",
               "esg": "prefers sustainable (ESG) investments"}  # fmt: skip
BANDS = [(9, "cautious"), (14, "balanced"), (19, "growth"), (25, "adventurous")]
BAND_TEXT = {
    "cautious": "capital preservation first; large falls would be hard to live with",
    "balanced": "accepts some ups and downs for steadier growth",
    "growth": "long horizon, comfortable with sizeable falls along the way",
    "adventurous": "seeks higher returns and accepts large, lasting losses",
}


def score(answers: dict[str, str], horizon: str) -> tuple[int, str]:
    total = 0
    for q in QUESTIONS:
        pts = {k: p for k, _, p in q["options"]}
        if answers.get(q["key"]) not in pts:
            raise ValidationFailed(f"Please answer: {q['text']}")
        total += pts[answers[q["key"]]]
    band = next(b for limit, b in BANDS if total <= limit)
    if band == "adventurous" and not (answers["drop"] == "buy_more" or answers["priority"] == "max_growth"):
        band = "growth"  # a high score from capacity alone isn't an appetite for large, lasting losses
    if horizon == "under_1y" and band in ("growth", "adventurous"):
        band = "balanced"  # money needed soon: the band is capped whatever the appetite
    return total, band


def public_questions() -> dict[str, Any]:
    return {
        "questions": [{"key": q["key"], "text": q["text"], "options": [{"key": k, "label": lbl} for k, lbl, _ in q["options"]]} for q in QUESTIONS],
        "horizons": HORIZONS, "savings": SAVINGS, "goals": GOALS, "constraints": CONSTRAINTS, "bands": BAND_TEXT,
    }  # fmt: skip


def profile_json(p: InvestorProfile | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {"goals": p.goals, "horizon": p.horizon, "monthly_budget_aed": p.monthly_budget_aed, "savings_band": p.savings_band,
            "experience": p.experience, "constraints": p.constraints, "answers": p.answers, "risk_score": p.risk_score,
            "risk_band": p.risk_band, "updated_at": p.updated_at.isoformat() + "Z"}  # fmt: skip


def get(db: Session, user: User) -> InvestorProfile | None:
    return db.scalar(select(InvestorProfile).where(InvestorProfile.user_id == user.id))


def save(db: Session, user: User, data: dict[str, Any]) -> InvestorProfile:
    horizon = data.get("horizon")
    if horizon not in HORIZONS:
        raise ValidationFailed("Choose your time horizon.")
    goals = [g for g in dict.fromkeys(data.get("goals") or []) if g in GOALS][:4]
    if not goals:
        raise ValidationFailed("Choose at least one goal.")
    constraints = [c for c in dict.fromkeys(data.get("constraints") or []) if c in CONSTRAINTS]
    savings = data.get("savings_band")
    if savings is not None and savings not in SAVINGS:
        raise ValidationFailed("Unknown savings band.")
    budget = data.get("monthly_budget_aed")
    if budget is not None and not 0 <= int(budget) <= 10_000_000:
        raise ValidationFailed("Monthly budget must be between AED 0 and 10,000,000.")
    answers = {q["key"]: data.get("answers", {}).get(q["key"]) for q in QUESTIONS}
    total, band = score(answers, horizon)
    p = get(db, user) or InvestorProfile(user_id=user.id)
    p.goals, p.horizon, p.savings_band, p.constraints, p.answers = goals, horizon, savings, constraints, answers
    p.monthly_budget_aed = int(budget) if budget is not None else None
    p.experience, p.risk_score, p.risk_band = answers["experience"], total, band
    db.add(p)
    db.commit()
    return p


def _holdings_text(db: Session, user: User) -> str | None:
    from app.services import portfolio_analytics

    try:
        pos, _ = portfolio_analytics._positions(db, user)
    except NexisError:
        return None
    priced = [p for p in pos if p["priced"] and p["value_usd"] > 0]
    total = sum(p["value_usd"] for p in priced)
    if not total:
        return None
    rows = sorted(priced, key=lambda p: -p["value_usd"])[:10]
    parts = [f"{p['symbol']} {p['value_usd'] / total:.0%}" for p in rows]
    top = rows[0]["value_usd"] / total
    note = f" Largest position is {top:.0%} of the tracked portfolio." if top >= 0.25 else ""
    return f"Tracked holdings in My Nexis (by value, about USD {total:,.0f}): {', '.join(parts)}.{note}"


def context(db: Session, user: User) -> tuple[str, dict[str, Any] | None]:
    """The text the Advisor gets about this member, and the profile snapshot for the advice log."""
    p = get(db, user)
    if p is None:
        return ("The member hasn't completed an investor profile. Give general guidance and, where it matters, suggest they "
                "complete their profile (Advisor → Your profile) so guidance can reflect their goals, horizon and risk."), None  # fmt: skip
    lines = [
        f"Risk profile: {p.risk_band} (score {p.risk_score}/25: {BAND_TEXT[p.risk_band]}).",
        f"Goals: {', '.join(GOALS[g] for g in p.goals)}. Horizon: {HORIZONS[p.horizon]}. Experience: {p.experience}.",
    ]
    if p.monthly_budget_aed is not None:
        lines.append(f"Can invest about AED {p.monthly_budget_aed:,} a month.")
    if p.savings_band:
        lines.append(f"Savings: {SAVINGS[p.savings_band]}.")
    if p.constraints:
        lines.append("Preferences: " + "; ".join(CONSTRAINTS[c] for c in p.constraints) + ".")
    holdings = _holdings_text(db, user)
    if holdings:
        lines.append(holdings)
    return "Member's investor profile (stated by them):\n- " + "\n- ".join(lines), profile_json(p)
