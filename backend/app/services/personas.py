"""Nexis-generated personas: fictional investors with stable, structured profiles.

Personas are generated, not hand-written. Persona number ``i`` is always built from the same seeded random
draw, so ``ensure(db, n)`` is idempotent and grows the cast from 100 to 1,000+ without changing anyone who
already exists. Each persona is a ``users`` row with ``kind = "persona"`` (no password, cannot sign in) plus a
``personas`` row holding the profile the discussion engine writes from.

Names are fictional combinations shown as "First L." and never imitate a real individual. The interface labels
every persona as Nexis-generated.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Persona, User

KIND = "persona"

# ------------------------------------------------------------------ archetypes
# focus: asset classes / sectors / markets / topics the archetype gravitates to; lens: how it reads events.
ARCHETYPES: dict[str, dict[str, Any]] = {
    "value": {"label": "Long-term value investor", "classes": ["equity"], "sectors": ["banks", "consumer", "energy", "industrials"],
              "markets": ["us", "uae", "europe"], "topics": ["valuation", "earnings", "dividends", "management"],
              "lens": "price paid versus durable earnings power and balance-sheet strength", "risk": "low"},
    "growth": {"label": "Growth investor", "classes": ["equity"], "sectors": ["technology", "ai", "software", "consumer"],
               "markets": ["us"], "topics": ["growth", "revenue", "products", "competition"],
               "lens": "how long revenue can compound and how big the market really is", "risk": "high"},
    "dividend": {"label": "Dividend investor", "classes": ["equity"], "sectors": ["banks", "utilities", "energy", "real_estate"],
                 "markets": ["uae", "us"], "topics": ["dividends", "debt", "rates", "earnings"],
                 "lens": "cash returned to shareholders and whether it is sustainable", "risk": "low"},
    "macro": {"label": "Macro investor", "classes": ["fx", "bond", "commodity", "equity"], "sectors": ["banks", "energy"],
              "markets": ["us", "europe", "em", "uae"], "topics": ["macro", "rates", "energy", "regulation"],
              "lens": "rates, liquidity, currencies and what central banks are signalling", "risk": "medium"},
    "quant": {"label": "Quant investor", "classes": ["equity", "crypto"], "sectors": ["technology", "banks"], "markets": ["us"],
              "topics": ["technicals", "risk", "valuation"], "lens": "base rates, distributions and what the data actually supports",
              "risk": "medium"},
    "technical": {"label": "Technical trader", "classes": ["equity", "crypto", "commodity"], "sectors": ["technology", "ai"],
                  "markets": ["us"], "topics": ["technicals", "risk"], "lens": "price action, levels, momentum and positioning",
                  "risk": "high"},
    "fixed_income": {"label": "Fixed-income specialist", "classes": ["bond"], "sectors": ["banks", "real_estate"],
                     "markets": ["us", "uae", "europe"], "topics": ["rates", "debt", "macro", "risk"],
                     "lens": "yields, credit spreads, duration and refinancing risk", "risk": "low"},
    "uae": {"label": "UAE market investor", "classes": ["equity", "bond"], "sectors": ["banks", "real_estate", "energy", "utilities"],
            "markets": ["uae"], "topics": ["real_estate", "dividends", "rates", "earnings"],
            "lens": "Dubai and Abu Dhabi fundamentals, government links and the dollar peg", "risk": "medium"},
    "us_equities": {"label": "US equities investor", "classes": ["equity"], "sectors": ["technology", "consumer", "banks", "healthcare"],
                    "markets": ["us"], "topics": ["earnings", "valuation", "growth"], "lens": "earnings quality and guidance",
                    "risk": "medium"},
    "tech": {"label": "Technology investor", "classes": ["equity"], "sectors": ["technology", "software", "ai"], "markets": ["us"],
             "topics": ["products", "competition", "ai", "margins"], "lens": "product cycles, platforms and competitive moats",
             "risk": "medium"},
    "ai": {"label": "AI investor", "classes": ["equity"], "sectors": ["ai", "technology"], "markets": ["us"],
           "topics": ["ai", "growth", "competition"], "lens": "AI capex, model economics and who captures the value", "risk": "high"},
    "energy": {"label": "Energy investor", "classes": ["equity", "commodity"], "sectors": ["energy"], "markets": ["us", "uae", "em"],
               "topics": ["energy", "dividends", "macro"], "lens": "supply, demand, capital discipline and the cycle", "risk": "medium"},
    "banking": {"label": "Banking investor", "classes": ["equity", "bond"], "sectors": ["banks"], "markets": ["us", "uae", "europe"],
                "topics": ["rates", "earnings", "regulation", "debt"], "lens": "net interest margins, credit quality and capital",
                "risk": "medium"},
    "fintech": {"label": "Fintech investor", "classes": ["equity", "crypto"], "sectors": ["fintech", "banks", "technology"],
                "markets": ["us", "europe"], "topics": ["growth", "regulation", "competition"],
                "lens": "payment flows, take rates and regulatory pressure", "risk": "high"},
    "contrarian": {"label": "Contrarian investor", "classes": ["equity", "commodity", "crypto"], "sectors": ["energy", "technology", "banks"],
                   "markets": ["us", "em", "europe"], "topics": ["valuation", "risk", "macro"],
                   "lens": "what the consensus is missing and where sentiment is stretched", "risk": "medium"},
    "risk": {"label": "Risk-focused investor", "classes": ["equity", "bond"], "sectors": ["banks", "technology"], "markets": ["us", "uae"],
             "topics": ["risk", "debt", "regulation"], "lens": "downside scenarios, concentration and what could go wrong",
             "risk": "low"},
    "passive": {"label": "Passive investor", "classes": ["etf", "equity"], "sectors": ["technology", "consumer"], "markets": ["us", "europe"],
                "topics": ["macro", "valuation"], "lens": "costs, diversification and staying invested", "risk": "low"},
    "beginner": {"label": "Beginner investor", "classes": ["equity", "etf", "crypto"], "sectors": ["technology", "consumer"],
                 "markets": ["us", "uae"], "topics": ["growth", "products"], "lens": "trying to understand what the news means",
                 "risk": "medium"},
    "institutional": {"label": "Institutional-style analyst", "classes": ["equity", "bond"], "sectors": ["banks", "technology", "energy", "healthcare"],
                      "markets": ["us", "europe", "uae"], "topics": ["earnings", "margins", "valuation", "debt"],
                      "lens": "segment detail, guidance and what changes the model", "risk": "medium"},
    "short_term": {"label": "Short-term trader", "classes": ["equity", "crypto", "fx"], "sectors": ["technology", "ai"], "markets": ["us"],
                   "topics": ["technicals", "earnings"], "lens": "catalysts, reactions and positioning over days and weeks",
                   "risk": "high"},
    "fundamental": {"label": "Fundamental analyst", "classes": ["equity"], "sectors": ["healthcare", "industrials", "consumer", "technology"],
                    "markets": ["us", "europe"], "topics": ["earnings", "revenue", "margins", "management"],
                    "lens": "unit economics, cash conversion and management decisions", "risk": "medium"},
}  # fmt: skip

TRAITS = ["confident", "cautious", "analytical", "skeptical", "optimistic", "curious", "blunt", "measured", "dry humour",
          "detail-oriented", "big-picture", "patient", "impatient with hype", "open to changing their mind"]  # fmt: skip
VOICES = [
    {"length": "short", "style": "terse, one or two sentences, no preamble"},
    {"length": "short", "style": "casual and direct, sometimes starts mid-thought"},
    {"length": "medium", "style": "plain English, a clear point and one supporting reason"},
    {"length": "medium", "style": "conversational, often asks a pointed question"},
    {"length": "medium", "style": "measured, weighs both sides before landing somewhere"},
    {"length": "long", "style": "structured reasoning in a short paragraph or two, no headings"},
    {"length": "long", "style": "analytical, refers back to specifics from the source"},
]  # fmt: skip
VOCABULARY = ["everyday words, avoids jargon", "finance vocabulary used precisely", "some trading slang, used sparingly",
              "formal and careful"]  # fmt: skip
HABITS = ["asks questions more than asserting", "often plays devil's advocate", "likes to point out what to watch next",
          "concedes good points readily", "rarely changes position", "uses analogies", "keeps coming back to valuation",
          "keeps coming back to risk", "focuses on what management actually said", "compares with past cycles"]  # fmt: skip
EXPERIENCE = ["under 2 years", "3–5 years", "5–10 years", "10–20 years", "20+ years", "former sell-side analyst",
              "former bank treasury", "runs a family portfolio"]  # fmt: skip

FIRST = ["Layla", "Omar", "Priya", "Daniel", "Sofia", "Karim", "Hannah", "Rahul", "Maya", "Yusuf", "Elena", "Tariq", "Grace",
         "Arjun", "Noura", "Lukas", "Amira", "Chen", "Fatima", "Marco", "Aisha", "James", "Leila", "Ravi", "Zara", "Tomás",
         "Mei", "Hassan", "Clara", "Imran", "Nadia", "Ethan", "Rania", "Kenji", "Salma", "Victor", "Dina", "Adam", "Hiba",
         "Felix", "Yara", "Samir", "Ingrid", "Bilal", "Lucia", "Ahmed", "Freya", "Kofi", "Mariam", "Oscar", "Reem", "Ivan",
         "Sara", "Nikhil", "Lina", "Jonas", "Huda", "Mateo", "Farah", "Arun"]  # fmt: skip
LAST_INITIALS = list("ABCDEFGHJKLMNPRSTVWZ")
HANDLE_WORDS = ["yield", "ledger", "tape", "moat", "compound", "duration", "carry", "basis", "float", "spread", "margin",
                "cashflow", "dividend", "macro", "beta", "alpha", "value", "growth", "gulf", "dirham", "souk", "harbor",
                "north", "quiet", "slow", "long", "patient", "deep", "plain", "iron"]  # fmt: skip


def _profile(i: int) -> dict[str, Any]:
    rng = random.Random(f"nexis-persona-{i}")
    key = list(ARCHETYPES)[i % len(ARCHETYPES)] if i < len(ARCHETYPES) * 3 else rng.choice(list(ARCHETYPES))
    a = ARCHETYPES[key]
    first, initial = rng.choice(FIRST), rng.choice(LAST_INITIALS)
    style = rng.random()
    if style < 0.45:
        handle = f"{first.lower()}.{initial.lower()}{rng.randint(1, 99) if rng.random() < 0.4 else ''}"
    elif style < 0.8:
        w1, w2 = rng.sample(HANDLE_WORDS, 2)
        handle = f"{w1}{w2}"
    else:
        handle = f"{first.lower()}_{rng.choice(HANDLE_WORDS)}"
    handle = (handle.replace("á", "a").replace("ó", "o") + "")[:24]
    traits = rng.sample(TRAITS, 2)
    voice = dict(rng.choice(VOICES))
    if key in ("beginner",):
        voice = {"length": "medium", "style": "curious and plain-spoken, asks what things mean"}
    profile = {
        "label": a["label"],
        "experience": "under 2 years" if key == "beginner" else rng.choice(EXPERIENCE[1:]),
        "risk_tolerance": a["risk"] if rng.random() < 0.7 else rng.choice(["low", "medium", "high"]),
        "asset_classes": a["classes"],
        "sectors": rng.sample(a["sectors"], min(len(a["sectors"]), rng.randint(1, 3))),
        "markets": rng.sample(a["markets"], min(len(a["markets"]), rng.randint(1, 2))),
        "topics": rng.sample(a["topics"], min(len(a["topics"]), 3)),
        "traits": traits,
        "voice": voice,
        "vocabulary": "everyday words, avoids jargon" if key == "beginner" else rng.choice(VOCABULARY),
        "habit": rng.choice(HABITS),
        "framework": a["lens"],
        "bias": rng.choice(
            [
                "tends to be early",
                "anchors on valuation",
                "trusts management too little",
                "likes companies with net cash",
                "wary of crowded trades",
                "favours what they can understand",
                "overweights recent data",
                "sceptical of forecasts",
            ]
        ),
    }
    bio = _bio(first, profile, rng)
    return {"index": i, "archetype": key, "display_name": f"{first} {initial}.", "handle": handle, "bio": bio, "traits": profile}


def _bio(first: str, p: dict[str, Any], rng: random.Random) -> str:
    focus = {"uae": "UAE markets", "us": "US markets", "europe": "European markets", "em": "emerging markets"}
    sectors = ", ".join("AI" if s == "ai" else s.replace("_", " ") for s in p["sectors"])
    markets = " and ".join(focus.get(m, m) for m in p["markets"])
    lines = [
        f"{p['label']}. Mostly {sectors}, {markets}.",
        f"Investing {p['experience']}."
        if p["experience"][0].isdigit() or p["experience"].startswith("under")
        else f"{p['experience'].capitalize()}.",
        rng.choice(
            [
                "Here to argue in good faith.",
                "Interested in what could go wrong.",
                "Slow money.",
                "Reads the filings.",
                "Asks a lot of questions.",
                "Usually early, occasionally right.",
                "Cash flow over stories.",
            ]
        ),
    ]
    return " ".join(lines)[:300]


def ensure(db: Session, n: int = 100) -> dict[str, int]:
    """Create personas 0..n-1 that don't exist yet. Existing personas are never changed."""
    have = {p.traits.get("index") for p in db.scalars(select(Persona))}
    taken = set(db.scalars(select(User.username)))
    created = 0
    for i in range(n):
        if i in have:
            continue
        prof = _profile(i)
        username = prof["handle"]
        k = 2
        while username in taken:
            username = f"{prof['handle']}{k}"
            k += 1
        taken.add(username)
        u = User(
            username=username,
            display_name=prof["display_name"],
            password_hash=None,
            kind=KIND,
            auth_provider="system",
            bio=prof["bio"],
        )
        db.add(u)
        db.flush()
        db.add(Persona(user_id=u.id, archetype=prof["archetype"], traits={**prof["traits"], "index": i}, memory={}))
        created += 1
    db.commit()
    return {"created": created, "total": len(have) + created}


# ------------------------------------------------------------------ selection

UAE_SUFFIXES = (".AE", ".AD")


def classify(symbol: str) -> dict[str, str]:
    """Rough asset class and market for persona matching (no network calls)."""
    s = symbol.upper()
    if s.endswith(".BOND"):
        return {"class": "bond", "market": "uae"}
    if s.endswith(UAE_SUFFIXES):
        return {"class": "equity", "market": "uae"}
    if s.endswith("-USD"):
        return {"class": "crypto", "market": "global"}
    if s.endswith("=F"):
        return {"class": "commodity", "market": "global"}
    if s.endswith("=X"):
        return {"class": "fx", "market": "global"}
    if s.startswith("^"):
        return {"class": "equity", "market": "us"}
    if "." in s:
        return {"class": "equity", "market": "europe" if s.rsplit(".", 1)[1] in ("L", "PA", "DE", "AS", "MI", "SW") else "em"}
    return {"class": "equity", "market": "us"}


def pick(
    db: Session, sectors: list[str], classes: list[str], markets: list[str], topics: list[str], k: int, seed: str
) -> list[Persona]:
    """Pick ``k`` personas relevant to an event, with different archetypes and a mix of temperaments."""
    rng = random.Random(seed)
    recent = utcnow() - timedelta(hours=6)
    scored: list[tuple[float, Persona]] = []
    for p in db.scalars(select(Persona).where(Persona.active.is_(True))):
        t = p.traits
        s = 3 * len(set(classes) & set(t["asset_classes"])) + 2 * len(set(sectors) & set(t["sectors"]))
        s += 2 * len(set(markets) & set(t["markets"])) + 1.5 * len(set(topics) & set(t["topics"]))
        if s <= 0:
            continue
        if p.last_active_at and p.last_active_at > recent:
            s -= 2  # give others a turn
        scored.append((s + rng.random() * 2.5, p))
    scored.sort(key=lambda x: -x[0])
    out: list[Persona] = []
    seen: set[str] = set()
    for _, p in scored:
        if p.archetype in seen:
            continue
        out.append(p)
        seen.add(p.archetype)
        if len(out) >= k:
            break
    return out


def remember(p: Persona, symbol: str, stance: str, point: str) -> None:
    """Keep a small, structured memory of what this persona argued (latest 25 assets)."""
    mem = dict(p.memory or {})
    mem[symbol] = {"stance": stance, "point": point[:160], "at": utcnow().date().isoformat()}
    if len(mem) > 25:
        for old in sorted(mem, key=lambda k: mem[k].get("at", ""))[: len(mem) - 25]:
            mem.pop(old, None)
    p.memory = mem
    p.last_active_at = utcnow()


def public(p: Persona) -> dict[str, Any]:
    t = p.traits
    return {
        "archetype": t.get("label"), "experience": t.get("experience"), "risk_tolerance": t.get("risk_tolerance"),
        "asset_classes": t.get("asset_classes"), "sectors": t.get("sectors"), "markets": t.get("markets"),
        "topics": t.get("topics"), "framework": t.get("framework"),
    }  # fmt: skip
