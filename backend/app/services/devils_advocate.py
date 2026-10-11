"""Devil's Advocate: challenge a member's investment thesis using stored evidence only.

Pipeline (one AI call per analysis; everything else is deterministic code):

1. **Evidence** is gathered and stored as bounded evidence records: market data (price, valuation, profitability,
   dividend, analyst consensus — as reported by the data provider), recent headlines, Nexis source events, and the
   company's World Graph relationships. Each item gets a short label (E1, E2, …) for the AI.
2. **The AI** splits the thesis into claims (first run only — the claims are then kept so their status can be
   tracked across runs) and argues against it: assessments per claim, the strongest counter-case, risks, signals that
   would change the view, and data gaps. Every argument must cite evidence by label **with a verbatim quote**.
3. **The validator** drops any citation whose quote isn't found in that evidence item, drops counter-case points
   and risks left without a valid citation, recomputes each claim's status from the surviving citations, and flags
   numbers that appear in neither the evidence nor the thesis.
4. If the AI is unavailable or returns nothing usable, the run is stored as **failed** and nothing is shown as a
   result.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NexisError, ValidationFailed
from app.db.base import utcnow
from app.models import EvidenceRecord, FinEntity, FinRelationship, ResearchItem, SourceEvent
from app.services import knowledge, llm, markets

MODEL_VERSION = "da-1.1"
MAX_TOKENS = 8000
MAX_EVIDENCE = 28
MIN_QUOTE = 8
STATUSES = ("supported", "contradicted", "mixed", "untested")

SYSTEM = """You are a rigorous devil's advocate for an investment research tool. Your job is to challenge the user's
thesis, not to agree with it, and never to give buy/sell advice.

Rules you must follow exactly:
- Use ONLY the evidence items provided (labelled E1, E2, ...). Do not use outside knowledge, do not invent facts.
- Every citation is {"evidence": "E<n>", "quote": "<exact words copied from that evidence item>"}. The quote must be
  copied character-for-character from the item's text (8 to 200 characters). Citations that don't match are deleted.
- Do not state numbers that are not in the evidence or the thesis.
- If the evidence doesn't address something, say so in "gaps" instead of guessing.
- Output one JSON object only, no prose around it."""

SCHEMA = """{
  "claims": [{"id": "C1", "text": "one testable claim from the thesis", "kind": "fact|assumption|prediction"}],
  "assessments": [{"claim": "C1", "for": [CITATION], "against": [CITATION], "comment": "short, neutral"}],
  "counter_case": [{"point": "the strongest argument against the thesis", "citations": [CITATION]}],
  "risks": [{"risk": "a specific risk to the thesis", "citations": [CITATION]}],
  "signals": [{"signal": "an observable development that would weaken or strengthen the thesis", "direction": "weakens|strengthens"}],
  "gaps": ["what the evidence cannot tell us"]
}"""


# ---------------------------------------------------------------------------------------------- evidence


def _f(v: Any, pct: bool = False, d: int = 2) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return f"{x * 100:.1f}%" if pct else f"{x:,.{d}f}"


def _join(parts: list[tuple[str, str | None]]) -> str | None:
    kept = [f"{k} {v}" for k, v in parts if v is not None]
    return "; ".join(kept) + "." if kept else None


def market_evidence(db: Session, symbol: str) -> list[EvidenceRecord]:
    """Reported numbers for the company, as separate evidence items (so a quote points at one topic)."""
    try:
        d = markets.details(db, symbol)
    except NexisError:
        return []
    q, v, fin, dv, an = (
        d.get("quote") or {},
        d.get("valuation") or {},
        d.get("financials") or {},
        d.get("dividends") or {},
        d.get("analysts") or {},
    )
    ccy, asof = d.get("currency") or "", (q.get("market_time") or utcnow().isoformat())[:10]
    name = d.get("name") or symbol
    items = [
        ("price", "Price and range", _join([("Price", _f(q.get("price")) and f"{_f(q.get('price'))} {ccy}"),
                                            ("52-week low", _f(q.get("week52_low"))), ("52-week high", _f(q.get("week52_high"))),
                                            ("50-day average", _f(q.get("ma50"))), ("200-day average", _f(q.get("ma200")))])),
        ("valuation", "Valuation multiples", _join([("Trailing P/E", _f(v.get("pe_trailing"))), ("Forward P/E", _f(v.get("pe_forward"))),
                                                    ("Price/book", _f(v.get("price_to_book"))), ("EV/EBITDA", _f(v.get("ev_to_ebitda"))),
                                                    ("Market cap", _f(v.get("market_cap"), d=0))])),
        ("fundamentals", "Profitability, growth and debt", _join([("Revenue growth", _f(fin.get("revenue_growth"), pct=True)),
                                                                  ("Earnings growth", _f(fin.get("earnings_growth"), pct=True)),
                                                                  ("Profit margin", _f(fin.get("profit_margin"), pct=True)),
                                                                  ("Return on equity", _f(fin.get("return_on_equity"), pct=True)),
                                                                  ("Debt/equity", _f(fin.get("debt_to_equity_pct")) and f"{_f(fin.get('debt_to_equity_pct'))}%"),
                                                                  ("Free cash flow", _f(fin.get("free_cash_flow"), d=0))])),
        ("dividend", "Dividend", _join([("Dividend yield", _f(dv.get("yield"), pct=True)), ("Annual dividend", _f(dv.get("rate"))),
                                        ("Payout ratio", _f(dv.get("payout_ratio"), pct=True)),
                                        ("5-year average yield", _f(dv.get("five_year_avg_yield_pct")) and f"{_f(dv.get('five_year_avg_yield_pct'))}%")])),
        ("analysts", "Analyst consensus", _join([("Analysts", _f(an.get("count"), d=0)), ("Recommendation", an.get("recommendation")),
                                                 ("Mean target", _f(an.get("target_mean"))), ("Low target", _f(an.get("target_low"))),
                                                 ("High target", _f(an.get("target_high")))])),
    ]  # fmt: skip
    out = []
    for key, label, text in items:
        if text:
            out.append(knowledge.upsert_evidence(
                db, source_kind="market_data", external_id=f"md:{symbol}:{key}:{asof}", title=f"{name}: {label} (as of {asof})",
                publisher="Nexis market data (delayed, from the data provider)", excerpt=text,
            ))  # fmt: skip
    return out


def news_evidence(db: Session, symbol: str, limit: int = 10) -> list[EvidenceRecord]:
    """Recent headlines (headline text only — the article itself isn't stored, so it can't be quoted)."""
    out: list[EvidenceRecord] = []
    try:
        items = markets.news(db, symbol).get("items") or []
    except NexisError:
        items = []
    for n in items[:limit]:
        title = (n.get("title") or "").strip()
        if not title:
            continue
        out.append(knowledge.upsert_evidence(
            db, source_kind="news", url=n.get("url"), external_id=None, title=title[:500],
            publisher=n.get("publisher") or n.get("source"), excerpt=f"Headline: {title}",
        ))  # fmt: skip
    since = utcnow() - timedelta(days=120)
    events = db.scalars(
        select(SourceEvent).where(SourceEvent.published_at >= since).order_by(SourceEvent.published_at.desc()).limit(400)
    ).all()
    for ev in events:
        if symbol not in (ev.assets or []):
            continue
        summary = (ev.facts or {}).get("summary") or ""
        out.append(knowledge.upsert_evidence(
            db, source_kind="news", url=ev.url, external_id=f"se:{ev.external_key}", title=ev.title[:500], publisher=ev.publisher,
            published_at=ev.published_at, excerpt=(f"Headline: {ev.title}. {summary}")[:600],
        ))  # fmt: skip
        if len(out) >= limit + 6:
            break
    return out


def graph_evidence(db: Session, symbol: str) -> list[EvidenceRecord]:
    e = db.scalar(select(FinEntity).where(FinEntity.symbol == symbol))
    if e is None:
        return []
    edges = db.scalars(
        select(FinRelationship).where((FinRelationship.source_id == e.id) | (FinRelationship.target_id == e.id)).limit(20)
    ).all()
    ids = []
    for edge in edges:
        ids += [ev["id"] for ev in knowledge.edge_json(db, edge)["evidence"]]
    return list(db.scalars(select(EvidenceRecord).where(EvidenceRecord.id.in_(ids))).all()) if ids else []


def gather(db: Session, symbol: str) -> list[EvidenceRecord]:
    items = market_evidence(db, symbol) + news_evidence(db, symbol) + graph_evidence(db, symbol)
    seen, out = set(), []
    for ev in items:
        if ev.id not in seen and ev.excerpt:
            seen.add(ev.id)
            out.append(ev)
    db.commit()
    return out[:MAX_EVIDENCE]


# ---------------------------------------------------------------------------------------------- validation


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[‘’“”]", "'", s or "")).strip().lower()


_NUM = re.compile(r"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?%?")


def numbers(text: str) -> set[str]:
    """Numbers in ``text``, normalised (no sign, %, thousands separators or trailing decimal zeros). Years (1900–2100)
    are left out: a date is not a figure that needs a source."""
    out = set()
    for raw in _NUM.findall(text or ""):
        n = raw.replace(",", "").replace("−", "-").lstrip("+-").rstrip("%")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        if n and not (len(n) == 4 and n.isdigit() and 1900 <= int(n) <= 2100):
            out.add(n)
    return out


def check_citations(cites: Any, labels: dict[str, dict[str, Any]], stats: dict[str, int]) -> list[dict[str, Any]]:
    """Keep only citations whose quote appears verbatim (ignoring case/whitespace/quote style) in that evidence item."""
    kept = []
    for c in cites if isinstance(cites, list) else []:
        stats["citations"] += 1
        if not isinstance(c, dict):
            stats["dropped"] += 1
            continue
        ev = labels.get(str(c.get("evidence", "")).strip().upper())
        quote = str(c.get("quote", "")).strip().strip('"')
        hay = norm(f"{ev['title']} {ev['excerpt']}") if ev else ""
        if ev is None or len(quote) < MIN_QUOTE or norm(quote) not in hay:
            stats["dropped"] += 1
            continue
        kept.append({"evidence_id": ev["id"], "label": ev["label"], "quote": quote[:300]})
    return kept


def validate(
    raw: dict[str, Any], labels: dict[str, dict[str, Any]], thesis: str, fixed_claims: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Turn the AI's JSON into a result that only contains checked citations, with statuses computed by code."""
    stats = {"citations": 0, "dropped": 0, "points_dropped": 0}
    allowed = numbers(thesis)
    for ev in labels.values():
        allowed |= numbers(f"{ev['title']} {ev['excerpt']}")

    def flag(text: str) -> list[str]:
        return sorted(numbers(text) - allowed)

    if fixed_claims:
        claims = fixed_claims
    else:
        claims = []
        for i, c in enumerate(raw.get("claims") or [], 1):
            if isinstance(c, dict) and str(c.get("text", "")).strip():
                kind = c.get("kind") if c.get("kind") in ("fact", "assumption", "prediction") else "assumption"
                claims.append({"id": f"C{i}", "text": str(c["text"]).strip()[:400], "kind": kind})
        claims = claims[:8]
    if not claims:
        raise ValueError("no claims")
    by_id = {c["id"]: c for c in claims}
    assessed: dict[str, dict[str, Any]] = {}
    for a in raw.get("assessments") or []:
        if not isinstance(a, dict) or a.get("claim") not in by_id:
            continue
        cur = assessed.setdefault(a["claim"], {"for": [], "against": [], "comment": ""})
        cur["for"] += check_citations(a.get("for"), labels, stats)
        cur["against"] += check_citations(a.get("against"), labels, stats)
        cur["comment"] = str(a.get("comment") or "")[:500]
    out_claims = []
    for c in claims:
        a = assessed.get(c["id"], {"for": [], "against": [], "comment": ""})
        status = (
            "mixed" if a["for"] and a["against"] else "supported" if a["for"] else "contradicted" if a["against"] else "untested"
        )
        out_claims.append({**c, "status": status, "for": a["for"], "against": a["against"], "comment": a["comment"],
                           "unverified_numbers": flag(a["comment"])})  # fmt: skip

    def cited(items: Any, key: str) -> list[dict[str, Any]]:
        res = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict) or not str(it.get(key, "")).strip():
                continue
            cs = check_citations(it.get("citations"), labels, stats)
            if not cs:
                stats["points_dropped"] += 1
                continue
            text = str(it[key]).strip()[:600]
            res.append({"text": text, "citations": cs, "unverified_numbers": flag(text)})
        return res[:6]

    signals = []
    for s in raw.get("signals") or []:
        if isinstance(s, dict) and str(s.get("signal", "")).strip():
            text = str(s["signal"]).strip()[:400]
            signals.append({"text": text, "direction": s.get("direction") if s.get("direction") in ("weakens", "strengthens") else "weakens",
                            "unverified_numbers": flag(text)})  # fmt: skip
    gaps = [str(g).strip()[:400] for g in (raw.get("gaps") or []) if str(g).strip()][:8]
    return {
        "claims": out_claims,
        "counter_case": cited(raw.get("counter_case"), "point"),
        "risks": cited(raw.get("risks"), "risk"),
        "signals": signals[:8],
        "gaps": gaps,
        "validation": stats,
    }


# ---------------------------------------------------------------------------------------------- an analysis


def parse_json(content: str) -> dict[str, Any]:
    """The first complete JSON object in the AI's reply (code fences and stray text around it are ignored)."""
    text = re.sub(r"```(?:json)?", "", content or "")
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer" + (" (empty answer)" if not text.strip() else ""))
    try:
        raw, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid or cut-off JSON at character {exc.pos} of {len(text) - start}") from exc
    if not isinstance(raw, dict):
        raise ValueError("the JSON is not an object")
    return raw


def check_input(symbol: str, thesis: str) -> tuple[str, str]:
    sym = markets.clean_symbol(symbol)
    t = re.sub(r"\s+", " ", thesis or "").strip()
    if not 30 <= len(t) <= 3000:
        raise ValidationFailed("Write the thesis in 30 to 3,000 characters.")
    return sym, t


def _prompt(
    symbol: str, thesis: str, horizon: str | None, labels: dict[str, dict[str, Any]], claims: list[dict[str, Any]] | None
) -> str:
    ev = "\n".join(
        f"[{k}] ({v['source_kind']}; {v['publisher'] or 'unknown publisher'}) {v['title']} — {v['excerpt']}"
        for k, v in labels.items()
    )
    claim_part = (
        'Use exactly these claims (do not add or rewrite claims; return them unchanged in "claims"):\n'
        + "\n".join(f"{c['id']}: {c['text']}" for c in claims)
        if claims
        else "First split the thesis into 2 to 8 testable claims."
    )
    return (
        f'Company: {symbol}\nInvestment horizon: {horizon or "not stated"}\nThesis:\n"""{thesis}"""\n\n{claim_part}\n\n'
        f'Evidence items:\n{ev}\n\nReturn JSON in this shape (CITATION = {{"evidence": "E1", "quote": "exact words"}}):\n{SCHEMA}'
    )


def analyse(db: Session, item: ResearchItem) -> dict[str, Any]:
    """Run one analysis for a saved thesis. Returns ``{inputs, snapshot, output, model_version, status}``."""
    st = item.state or {}
    symbol, thesis = check_input(st.get("symbol", ""), st.get("thesis", ""))
    evidence = gather(db, symbol)
    labels = {f"E{i}": {"id": ev.id, "label": f"E{i}", "source_kind": ev.source_kind, "title": ev.title, "excerpt": ev.excerpt or "",
                        "publisher": ev.publisher, "url": ev.url,
                        "published_at": ev.published_at.isoformat() + "Z" if ev.published_at else None,
                        "retrieved_at": ev.retrieved_at.isoformat() + "Z"}
              for i, ev in enumerate(evidence, 1)}  # fmt: skip
    inputs = {"symbol": symbol, "thesis": thesis, "horizon": st.get("horizon"), "claims": st.get("claims")}
    snapshot = {"evidence": list(labels.values()), "computed_at": utcnow().isoformat() + "Z"}
    base = {"inputs": inputs, "snapshot": snapshot, "model_version": MODEL_VERSION}
    if len(labels) < 2:
        return {
            **base,
            "status": "failed",
            "output": {"error": "Not enough evidence could be gathered for this company to challenge the thesis."},
        }
    convo = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": _prompt(symbol, thesis, st.get("horizon"), labels, st.get("claims"))},
    ]
    diagnostics: list[dict[str, Any]] = []
    result = None
    msg: dict[str, Any] = {}
    for attempt in range(2):  # one retry, asking for valid JSON only, if the first answer can't be read
        try:
            # reasoning models spend part of max_tokens thinking: leave plenty of room for the JSON itself
            msg = llm.chat(convo, max_tokens=MAX_TOKENS, temperature=0.1)
        except llm.LLMUnavailable:
            return {**base, "status": "failed",
                    "output": {"error": "The analysis could not be completed: the AI service is unavailable. Nothing was concluded.",
                               "diagnostics": diagnostics}}  # fmt: skip
        content = msg.get("content") or ""
        try:
            raw = parse_json(content)
            result = validate(raw, labels, thesis, st.get("claims"))
            break
        except ValueError as exc:
            diagnostics.append(
                {"attempt": attempt + 1, "finish_reason": msg.get("_finish"), "chars": len(content), "problem": str(exc)[:200]}
            )
            convo = [*convo, {"role": "assistant", "content": content[:4000]},
                     {"role": "user", "content": "That wasn't a single valid JSON object in the requested shape. Reply with only the JSON object, "
                                                 "shorter if needed (fewer, tighter points), and nothing else."}]  # fmt: skip
    if result is None:
        return {**base, "status": "failed",
                "output": {"error": "The analysis could not be completed: the AI's answer couldn't be checked. Nothing was concluded.",
                           "diagnostics": diagnostics}}  # fmt: skip
    result["diagnostics"] = diagnostics
    result["ai"] = {"provider": msg.get("_provider"), "model": msg.get("_model")}
    return {**base, "status": "completed", "output": result}


def compare(prev: dict[str, Any], cur: dict[str, Any]) -> list[dict[str, Any]]:
    """Claim statuses then and now (claims are fixed after the first run, so ids line up)."""
    before = {c["id"]: c["status"] for c in prev.get("claims") or []}
    return [{"id": c["id"], "text": c["text"], "before": before.get(c["id"]), "now": c["status"],
             "changed": before.get(c["id"]) not in (None, c["status"])} for c in cur.get("claims") or []]  # fmt: skip
