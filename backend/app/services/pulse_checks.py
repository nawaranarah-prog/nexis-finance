"""Contradiction checks for Nexis editorial stories, against Nexis's own market data.

The first check covers the direction of interest rates, which editorial text often takes for granted ("as rates
fall…", "how much easing…"). The data used is the US 10-year Treasury yield and the 13-week bill rate (the dirham is
pegged to the dollar, so US rates drive UAE rates too), compared over the last 26 weeks:

* both up by at least 20bp → rates are **rising**;
* both down by at least 20bp → rates are **falling**;
* otherwise → mixed (nothing is held).

A visible editorial story that presents the opposite direction as the current situation is **held for review** (not
deleted). An entry goes into the moderation log with the numbers, so a moderator can approve it again in one click if
the check was wrong.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import NexisError, ProviderError
from app.db.base import utcnow
from app.models import ModerationAction, PulseDiscussion
from app.services import markets

WEEKS = 26
MIN_BP = 20
RATE = r"(?:interest\s+)?(?:rates?|yields?|borrowing\s+costs?)"
FALLING = [
    rf"\b(?:falling|declining|lower(?:ing)?|easing|softening)\s+{RATE}",
    rf"\b{RATE}\s+(?:are\s+|have\s+been\s+|keep\s+|continue\s+to\s+)?(?:falling|declining|coming\s+down|easing|decline|fall)\b",
    rf"\bas\s+{RATE}\s+(?:decline|fall|ease|come\s+down)\b",
    r"\b(?:rate|policy)\s+cuts?\s+(?:are|is)\s+(?:under\s*way|coming|here)\b",
    r"\bhow\s+much\s+(?:more\s+)?easing\b",
    r"\b(?:monetary|policy)\s+easing\s+(?:cycle|has\s+begun|is\s+under\s*way)\b",
    r"\beasing\s+cycle\b",
]
RISING = [
    rf"\b(?:rising|climbing|higher|tightening)\s+{RATE}",
    rf"\b{RATE}\s+(?:are\s+|have\s+been\s+|keep\s+|continue\s+to\s+)?(?:rising|climbing|going\s+up|rise|climb)\b",
    rf"\bas\s+{RATE}\s+(?:rise|climb|go\s+up)\b",
    r"\b(?:rate|policy)\s+hikes?\s+(?:are|is)\s+(?:under\s*way|coming|here)\b",
    r"\btightening\s+cycle\b",
]


def _change_bp(db: Session, symbol: str) -> tuple[float, str, str] | None:
    try:
        h = markets.history(db, symbol, "1d", (utcnow() - timedelta(weeks=WEEKS + 2)).date(), None)
    except NexisError:
        return None
    bars = [b for b in h["bars"] if b.get("close") is not None]
    if len(bars) < 60:
        return None
    end = bars[-1]
    cutoff = (utcnow() - timedelta(weeks=WEEKS)).date().isoformat()
    start = next((b for b in bars if b["t"][:10] >= cutoff), bars[0])
    return (end["close"] - start["close"]) * 100, start["t"][:10], end["t"][:10]


def rates_trend(db: Session) -> dict[str, Any]:
    """Direction of US rates over the last 26 weeks, from Nexis market data (cached for 6 hours)."""

    def compute() -> dict[str, Any]:
        ten, bill = _change_bp(db, "^TNX"), _change_bp(db, "^IRX")
        if ten is None or bill is None:
            raise ProviderError("rates data unavailable")  # not cached: the next tick tries again
        direction = (
            "rising"
            if ten[0] >= MIN_BP and bill[0] >= MIN_BP
            else "falling"
            if ten[0] <= -MIN_BP and bill[0] <= -MIN_BP
            else "mixed"
        )
        return {"direction": direction, "us10y_bp": round(ten[0]), "us13w_bp": round(bill[0]), "from": ten[1], "to": ten[2]}

    try:
        value, _ = markets.cached(db, "pulse-check:rates", timedelta(hours=6), compute)
    except NexisError:
        return {"direction": "unknown"}
    return value


def _text(d: PulseDiscussion) -> str:
    parts = [d.title, d.body, d.what_happened or "", d.debate or ""]
    for case in (d.bull_case, d.bear_case, d.neutral_case):
        parts += [str(p.get("point", "")) for p in case or [] if isinstance(p, dict)]
    parts += [str(q) for q in d.open_questions or []]
    return " ".join(parts)


def claims_direction(text: str) -> set[str]:
    t = re.sub(r"\s+", " ", text or "").lower()
    out = set()
    if any(re.search(p, t) for p in FALLING):
        out.add("falling")
    if any(re.search(p, t) for p in RISING):
        out.add("rising")
    return out


def contradiction(d: PulseDiscussion, trend: dict[str, Any]) -> str | None:
    if trend.get("direction") not in ("rising", "falling"):
        return None
    said = claims_direction(_text(d))
    opposite = "falling" if trend["direction"] == "rising" else "rising"
    if opposite in said and trend["direction"] not in said:
        return (f"Says rates are {opposite}, but Nexis data shows US rates {trend['direction']} over the last {WEEKS} weeks "
                f"(10-year {trend['us10y_bp']:+d}bp, 13-week bill {trend['us13w_bp']:+d}bp, {trend['from']} to {trend['to']}).")  # fmt: skip
    return None


def review(db: Session, days: int = 120) -> list[dict[str, Any]]:
    """Hold every visible editorial story that contradicts the rates data. Returns what was held."""
    trend = rates_trend(db)
    if trend.get("direction") not in ("rising", "falling"):
        return []
    since = utcnow() - timedelta(days=days)
    held = []
    stories = db.scalars(select(PulseDiscussion).where(PulseDiscussion.kind == "editorial", PulseDiscussion.status == "visible",
                                                       or_(PulseDiscussion.content_updated_at >= since, PulseDiscussion.created_at >= since))).all()  # fmt: skip
    for d in stories:
        reason = contradiction(d, trend)
        if reason is None:
            continue
        d.status, d.flagged = "held", True
        db.add(ModerationAction(target_type="discussion", target_id=d.id, action="auto_hold", reason=reason[:300], actor_id=None,
                                details={"check": "rates_direction", "trend": trend}))  # fmt: skip
        held.append({"id": d.public_id, "title": d.title, "reason": reason})
    if held:
        db.commit()
    return held
