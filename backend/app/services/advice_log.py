"""The advice log: every AI Advisor answer a member received, with the profile and data it was based on.

Each entry is reviewed automatically for language that crosses from education into instructions (telling the member to
buy, sell or hold a specific security, or how much to put in). Flags are recorded, not hidden — they show where the
model drifted, so the prompt can be tightened and the cases in ``tests/ai-cases.md`` re-run.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdviceLog, User

MAX_TEXT = 12_000
DIRECTIVE = [
    (r"\byou should (?:buy|sell|purchase|dump|short)\b", "tells the member to buy or sell"),
    (r"\b(?:buy|sell|purchase) (?:it|them|this|now|today)\b(?![^.]{0,40}\bif\b)", "tells the member to buy or sell"),
    (r"\bi(?:'d| would) (?:buy|sell|trim|accumulate|add to)\b", "gives a personal buy/sell call"),
    (r"\b(?:my|our) recommendation is to (?:buy|sell|hold)\b", "gives a buy/sell recommendation"),
    (r"\b(?:put|invest|allocate) (?:\d[\d,.]*\s*%|aed\s*\d[\d,]*|\$\s*\d[\d,]*) (?:of your [a-z]+ )?(?:in|into) (?!a\b|an\b|diversified)",
     "prescribes an amount for a specific holding"),
    (r"\b(?:position|trade|amount)(?: size)? (?:is|would be|looks) (?:a )?(?:reasonable|sensible|appropriate|fine)\b"
     r"|\b(?:reasonable|sensible|appropriate) (?:position )?size\b|\bmeaningful but not extreme\b", "judges the amount for the member"),
    (r"\bguarantee(?:d|s)? (?:return|profit|gain)s?\b", "promises returns"),
]  # fmt: skip


def review(answer: str) -> list[str]:
    t = re.sub(r"\s+", " ", answer or "").lower()
    found = [label for pat, label in DIRECTIVE if re.search(pat, t)]
    if answer and "not a recommendation" not in t and "not personalised" not in t and "educational" not in t:
        found.append("missing the educational disclaimer")
    return list(dict.fromkeys(found))


def record(
    db: Session, user: User, question: str, answer: str, profile: dict[str, Any] | None, meta: dict[str, Any]
) -> AdviceLog:
    ai = meta.get("ai") or {}
    row = AdviceLog(
        user_id=user.id,
        question=(question or "")[:4000],
        answer=(answer or "")[:MAX_TEXT],
        profile=profile,
        symbols=list(meta.get("symbols") or [])[:20],
        tools=[t.get("tool") for t in (meta.get("tools") or []) if isinstance(t, dict)][:30],
        mode="ai" if ai.get("used", meta.get("mode") == "ai") else "data_only",
        model=(ai.get("model") or None) and str(ai.get("model"))[:80],
        flags=review(answer) if ai.get("used", meta.get("mode") == "ai") else [],
    )
    db.add(row)
    db.commit()
    return row


def entries(db: Session, user: User, limit: int = 50, before: int | None = None) -> list[dict[str, Any]]:
    q = select(AdviceLog).where(AdviceLog.user_id == user.id)
    if before:
        q = q.where(AdviceLog.id < before)
    rows = db.scalars(q.order_by(AdviceLog.id.desc()).limit(max(1, min(limit, 100)))).all()
    return [{"id": r.id, "question": r.question, "answer": r.answer, "profile": r.profile, "symbols": r.symbols, "tools": r.tools,
             "mode": r.mode, "model": r.model, "flags": r.flags, "created_at": r.created_at.isoformat() + "Z"} for r in rows]  # fmt: skip
