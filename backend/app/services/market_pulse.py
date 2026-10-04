"""Nexis Pulse market debate: what the market is discussing about an asset, built from real evidence.

Pipeline for one asset (:func:`compute`):

1. **gather** — every connected connector in ``pulse_connectors`` (market data, news, filings);
2. **normalise** — each item becomes an ``EvidenceItem`` with its link, publisher and real publication time;
3. **asset matching / relevance** — news must actually name the asset (ticker, company name or a known alias);
4. **deduplicate** — by source key, then by normalised headline across feeds; stored as ``SourceEvent`` rows
   linked to the asset (``source_event_assets``), so the same story from two feeds counts once;
5. **topics and sentiment** — ``pulse_analysis`` (word lists; a stored AI classification when a model is set up);
6. **score** — the transparent formula in ``pulse_analysis``; no score when coverage is too thin;
7. **bull / bear arguments and the debate** — written by the configured AI model from the numbered evidence,
   with every point citing its evidence and numbers checked against the sources; without a model (or when it
   fails) a deterministic template built from the same evidence;
8. **cache** — one ``MarketDiscussion`` row per asset and one ``PulseSnapshot`` per asset per day.

Results are cached: a page view recomputes only when the analysis is older than ``pulse_fresh_minutes``, and the
AI narrative is rewritten at most every ``pulse_ai_refresh_minutes`` per asset, only when the evidence changed,
and within ``pulse_ai_daily_limit`` calls a day. Nothing here is attributed to a person: Pulse describes the
debate in neutral terms ("the bullish argument centers on…") and links every claim to its source.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError, NotFoundError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import MarketCache, MarketDiscussion, PulseSnapshot, SourceEvent, SourceEventAsset
from app.services import events, llm, markets, pulse_analysis, pulse_connectors
from app.services.pulse_connectors import AssetRef, EvidenceItem, title_key

log = get_logger(__name__)

PROVIDER_KIND = {"newsfeed": "news", "news_search": "news", "news_api": "news", "sec_edgar": "filing", "reddit": "social", "x": "social"}
# Nexis Research notes are opinions, and price-move events are covered live by the market-data connector.
EXCLUDED_PROVIDERS = ("nexis", "market_data")
CONTENT_TYPES = ("market_debate", "market_update", "earnings_debate", "valuation_debate", "sector_debate", "macro_debate", "event_analysis")
MAX_EVIDENCE = 80
AI_LOG_KEY = "market-pulse:ai-calls"
# Words that would make a generic first word match unrelated companies ("Emirates" airline vs Emirates NBD).
_GENERIC = {"emirates", "first", "dubai", "abu", "dhabi", "national", "international", "american", "general", "united", "bank",
            "gulf", "saudi", "al", "union", "commercial", "islamic", "global", "china", "new", "advanced", "the"}  # fmt: skip
_NOISE = {"inc", "corp", "corporation", "co", "plc", "ltd", "limited", "group", "holdings", "company", "pjsc", "psc", "and",
          "class", "sa", "ag", "nv", "the", "usd"}  # fmt: skip
_NUMBER = re.compile(r"(?<![A-Za-z])\$?\d[\d,]*(?:\.\d+)?%?")
_PERSONAL = re.compile(r"@\w|\b(?:I|I'm|I've|my|we think|our view)\b")


# ------------------------------------------------------------------ asset and evidence


def resolve(db: Session, symbol: str) -> AssetRef:
    """The asset as the market-data providers know it. Unknown symbols are refused, never invented."""
    sym = markets.clean_symbol(symbol)
    try:
        q = (markets.quotes(db, [sym]) or [None])[0]
    except NexisError:
        q = None
    md = db.scalars(select(MarketDiscussion).where(MarketDiscussion.symbol == sym)).first()
    if q is None and md is None:
        raise NotFoundError(f"No market data found for {sym}. Pick the asset from the search (Dubai shares end in .AE, Abu Dhabi in .AD).")
    name = (q or {}).get("name") or (md.name if md else None) or sym
    return AssetRef(symbol=sym, name=str(name)[:160], quote=q)


def _aliases(ref: AssetRef) -> list[str]:
    from app.services.newsfeed import NAME_TO_SYMBOL

    out = [k for k, v in NAME_TO_SYMBOL.items() if v == ref.symbol]
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&'.-]+", ref.name) if w.lower().strip(".") not in _NOISE]
    if words:
        first = words[0].strip(".")
        if first.lower() in _GENERIC and len(words) > 1:
            out.append(f"{first} {words[1].strip('.')}")
        elif len(first) >= 3 and first.lower() not in _GENERIC:
            out.append(first)
    return list(dict.fromkeys(a for a in out if a))


def relevant(ref: AssetRef, text: str) -> bool:
    """True when the text names the asset: its ticker as a word, a cashtag, its name or a known alias."""
    base = re.sub(r"[.\-=^].*$", "", ref.symbol.lstrip("^"))
    if len(base) >= 3 and re.search(rf"(?<![\w$])\$?{re.escape(base)}(?!\w)", text):
        return True
    if base and re.search(rf"\${re.escape(base)}\b", text, re.I):
        return True
    return any(re.search(rf"(?<!\w){re.escape(a)}(?!\w)", text, re.I) for a in _aliases(ref))


def _gather(db: Session, ref: AssetRef) -> tuple[list[EvidenceItem], dict[str, dict[str, Any]]]:
    items: list[EvidenceItem] = []
    status: dict[str, dict[str, Any]] = {}
    for c in pulse_connectors.active():
        try:
            got = c.fetch(db, ref)
        except Exception as exc:  # one source failing must not take Pulse down
            db.rollback()
            log.warning("pulse connector %s failed for %s: %s", c.key, ref.symbol, exc)
            status[c.key] = {"ok": False, "label": c.label, "error": getattr(exc, "message", None) or exc.__class__.__name__}
            continue
        kept = [i for i in got if i.kind != "news" or relevant(ref, i.title)]
        status[c.key] = {"ok": True, "label": c.label, "found": len(got), "relevant": len(kept)}
        items += kept
    return items, status


def _importance(item: EvidenceItem, kind: str) -> int:
    if item.kind == "filing":
        return 3 if any("earnings" in i for i in item.facts.get("items") or []) else 2
    return 3 if kind in ("earnings", "deal", "dividend", "rates", "regulation") else 2


def _store(db: Session, ref: AssetRef, items: list[EvidenceItem]) -> None:
    """Insert new evidence (deduplicated by key, then by headline) and link everything to the asset."""
    persist = [i for i in items if i.persist]
    if not persist:
        return
    keys = list({i.external_key[:80] for i in persist})
    existing = {e.external_key: e for e in db.scalars(select(SourceEvent).where(SourceEvent.external_key.in_(keys)))}
    since = utcnow() - timedelta(days=pulse_analysis.WINDOW_DAYS + 15)
    linked = db.scalars(
        select(SourceEvent).join(SourceEventAsset, SourceEventAsset.event_id == SourceEvent.id)
        .where(SourceEventAsset.symbol == ref.symbol, SourceEvent.published_at >= since)
    )  # fmt: skip
    by_title = {title_key(e.title): e for e in linked}
    for it in persist:
        e = existing.get(it.external_key[:80]) or by_title.get(title_key(it.title))
        if e is None:
            c = events.classify_text(f"{it.title} {it.summary or ''}")
            kind = "filing" if it.kind == "filing" else str(c["kind"])
            if it.kind == "filing" and any("earnings" in i for i in it.facts.get("items") or []):
                kind = "earnings"
            e = SourceEvent(
                kind=kind, provider=it.provider, title=it.title[:500], url=it.url, publisher=(it.publisher or None) and it.publisher[:160],
                published_at=it.published_at, facts={"headline": it.title, **({"summary": it.summary} if it.summary else {}), **it.facts,
                                                     "names": {ref.symbol: ref.name}},
                assets=[ref.symbol], topics=list(c["topics"]), importance=_importance(it, kind), external_key=it.external_key[:80],
            )  # fmt: skip
            db.add(e)
            db.flush()
            existing[e.external_key] = e
            by_title[title_key(e.title)] = e
        events.link_assets(db, e, [ref.symbol])
    db.commit()


def _evidence(db: Session, symbol: str) -> list[SourceEvent]:
    now = utcnow()
    q = (
        select(SourceEvent).join(SourceEventAsset, SourceEventAsset.event_id == SourceEvent.id)
        .where(SourceEventAsset.symbol == symbol, SourceEvent.provider.not_in(EXCLUDED_PROVIDERS),
               SourceEvent.published_at >= now - timedelta(days=pulse_analysis.WINDOW_DAYS),
               SourceEvent.published_at <= now + timedelta(hours=1))
        .order_by(SourceEvent.published_at.desc()).limit(MAX_EVIDENCE)
    )  # fmt: skip
    rows, seen = [], set()
    for e in db.scalars(q):
        k = title_key(e.title)
        if k in seen:  # the same headline stored by two providers before linking existed
            continue
        seen.add(k)
        rows.append(e)
    return rows


def _read(db: Session, evs: list[SourceEvent]) -> None:
    """Store a word-list reading for evidence that has none yet (AI readings, once stored, are kept)."""
    changed = False
    for e in evs:
        kind = PROVIDER_KIND.get(e.provider, "news")
        if e.analysis is None:
            r = pulse_analysis.item_reading(kind, e.title, (e.facts or {}).get("summary"), e.facts or {}, None)
            e.analysis = {k: r[k] for k in ("score", "label", "method", "topics") if k in r} | {"terms": r.get("terms", [])}
            e.sentiment = r["label"]
            changed = True
    if changed:
        db.commit()


def _items(evs: list[SourceEvent], live: list[EvidenceItem]) -> list[dict[str, Any]]:
    out = []
    for e in evs:
        kind = PROVIDER_KIND.get(e.provider, "news")
        r = pulse_analysis.item_reading(kind, e.title, (e.facts or {}).get("summary"), e.facts or {}, e.analysis)
        out.append({"key": str(e.id), "ref": str(e.id), "id": e.id, "kind": kind, "provider": e.provider, "title": e.title,
                    "url": e.url, "publisher": e.publisher, "published_at": e.published_at, "summary": (e.facts or {}).get("summary"),
                    "score": r["score"], "label": r["label"], "topics": r.get("topics") or [], "method": r.get("method"),
                    "facts": {k: v for k, v in (e.facts or {}).items() if k in ("form", "items")}})  # fmt: skip
    for it in live:
        r = pulse_analysis.item_reading(it.kind, it.title, None, it.facts, None)
        out.append({"key": it.external_key, "ref": it.external_key, "id": None, "kind": it.kind, "provider": it.provider,
                    "title": it.title, "url": it.url, "publisher": it.publisher, "published_at": it.published_at,
                    "score": r["score"], "label": r["label"], "topics": r["topics"], "method": r["method"], "facts": it.facts})  # fmt: skip
    return out


# ------------------------------------------------------------------ narrative: rules


def _side_point(t: dict[str, Any], items: list[dict[str, Any]], side: str) -> dict[str, Any] | None:
    its = sorted((i for i in items if t["key"] in i["topics"] and i["label"] == side and i["kind"] != "market_data"),
                 key=lambda i: -i["weight"] * abs(i["score"]))  # fmt: skip
    if not its:
        return None
    lead = its[0]
    n = len(its)
    tone = "positive" if side == "positive" else "negative"
    text = (f"{t['label']}: {n} recent {'item reads' if n == 1 else 'items read'} {tone}, "
            f"led by “{lead['title']}” ({lead['publisher'] or 'source'}).")  # fmt: skip
    return {"point": text, "refs": [i["key"] for i in its[:4]]}


def _rules(ref: AssetRef, items: list[dict[str, Any]], sc: dict[str, Any], th: list[dict[str, Any]]) -> dict[str, Any]:
    name = ref.name
    pos = sorted((t for t in th if t["net"] > 0.05 and t["positive"]), key=lambda t: -t["net"])
    neg = sorted((t for t in th if t["net"] < -0.05 and t["negative"]), key=lambda t: t["net"])
    bull = [p for p in (_side_point(t, items, "positive") for t in pos) if p][:3]
    bear = [p for p in (_side_point(t, items, "negative") for t in neg) if p][:3]
    month = next((i for i in items if i["key"].endswith(":1mo")), None)
    if month and abs(month["facts"]["change_pct"]) >= 5:
        p = {"point": f"Price action: {month['title']} (delayed market data).", "refs": [month["key"]]}
        (bull if month["facts"]["change_pct"] > 0 else bear).append(p)
    day = next((i for i in items if i["key"].endswith(":1d")), None)
    move = f" {day['title']} (delayed data)." if day and abs(day["facts"]["change_pct"]) >= 2 else ""
    d = sc["distribution"]
    if not sc["available"]:
        summary = (f"There is not enough recent market discussion about {name} to describe a debate: "
                   f"{sc['items']} relevant {'item' if sc['items'] == 1 else 'items'} in the last {sc['window_days']} days.{move}")  # fmt: skip
    else:
        focus = ", ".join(t["label"] for t in th[:3]) or "general market news"
        summary = (f"Recent coverage of {name} has focused on {focus}. Of {sc['items']} items analysed from the last "
                   f"{sc['window_days']} days, {d['positive']} read positive, {d['negative']} negative and {d['neutral']} neutral.{move}")  # fmt: skip
    p0, n0 = (pos[0]["label"] if pos else None), (neg[0]["label"] if neg else None)
    debate = None
    if p0 and n0 and sc["available"]:
        debate = {"question": f"Do the positives around {p0} outweigh the concerns about {n0}?",
                  "bull_side": f"The bullish argument centers on {p0}.", "bear_side": f"The bearish argument centers on {n0}."}  # fmt: skip
    if not sc["available"]:
        split = None
    elif p0 and n0:
        split = f"Market discussion is divided between {p0} on the positive side and {n0} on the negative side."
    elif p0:
        split = "Recent evidence is largely one-sided: little negative coverage appears in the items analysed, so there is no clear opposing argument yet."
    elif n0:
        split = "Recent evidence is largely one-sided: little positive coverage appears in the items analysed, so there is no clear opposing argument yet."
    else:
        split = "The evidence analysed is mostly neutral in tone; no clear disagreement stands out."
    title = f"{name} — {p0} vs {n0}" if p0 and n0 else f"{name} — {(p0 or n0)} in focus" if (p0 or n0) else f"{name} — market update"
    analysis = None
    if sc["available"]:
        analysis = (f"Nexis Pulse reads {sc['value']}/100 ({sc['label'].lower()}) from {sc['items']} items across "
                    f"{sc['publishers']} {'publisher' if sc['publishers'] == 1 else 'publishers'}, weighted toward the last week. "
                    f"{sc['coverage_text']} This summary was assembled automatically from headline wording and market data — "
                    "no AI model wrote it — so check the linked sources before relying on any of it.")  # fmt: skip
    questions = []
    if debate:
        questions.append(debate["question"])
    if n0:
        questions.append(f"Will the concerns around {n0.lower()} for {name} ease or intensify?")
    elif p0:
        questions.append(f"Can {p0.lower()} keep supporting the case for {name}?")
    return {"title": title[:200], "summary": summary, "bull": bull, "bear": bear, "neutral": [], "open_questions": questions[:2],
            "debate": debate, "split": split, "analysis": analysis, "content_type": _content_type(ref, items, th, bool(bull and bear)),
            "method": "rules", "model": None}  # fmt: skip


def _content_type(ref: AssetRef, items: list[dict[str, Any]], th: list[dict[str, Any]], two_sided: bool) -> str:
    day = next((i for i in items if i["key"].endswith(":1d")), None)
    if day and abs(day["facts"]["change_pct"]) >= 5:
        return "event_analysis"
    top = th[0]["key"] if th else None
    if ref.symbol.startswith("^") or ref.symbol.endswith(("=F", "=X")) or top in ("macro", "rates"):
        return "macro_debate"
    if top == "earnings":
        return "earnings_debate"
    if top == "valuation":
        return "valuation_debate"
    return "market_debate" if two_sided else "market_update"


# ------------------------------------------------------------------ narrative: AI

SYNTH_SYSTEM = (
    "You are the Nexis Pulse research desk. You turn a numbered list of recent, sourced market information about one "
    "asset into a structured, neutral summary of the market debate. Use only the EVIDENCE: never add facts, figures, "
    "dates, events, quotes or analyst views that are not in it, and only use numbers that appear in it. Describe the "
    "debate, never a person: write 'The bullish argument centers on…', 'One side of the debate argues…', 'Recent "
    "coverage has focused on…'. Never invent or name individual investors, users, accounts or handles, never write in "
    "the first person, and never present text as a quote from someone. Every bull or bear point cites the evidence "
    "numbers it rests on. This is not advice: never tell readers to buy, sell or hold. Output valid JSON only."
)
SYNTH_USER = """ASSET: {name} ({symbol}){exchange}
NEXIS PULSE (computed by Nexis from the evidence below): {score_line}
TOP THEMES: {themes}

EVIDENCE (newest first)
{lines}

Return JSON:
{{"classifications": [{{"n": 1, "sentiment": "positive|neutral|negative", "topics": ["earnings"]}}],
  "title": "a short debate headline, e.g. '{name} — growth vs valuation' (max 80 characters)",
  "content_type": "one of {types}",
  "whats_happening": "2-3 sentences: what is currently driving attention around the asset",
  "bull": [{{"point": "one sentence", "refs": [1, 2]}}],
  "bear": [{{"point": "one sentence", "refs": [3]}}],
  "debate": {{"question": "the central question the two sides disagree on", "bull_side": "one sentence", "bear_side": "one sentence"}},
  "split": "one sentence: where market discussion is divided",
  "neutral": [{{"point": "a consideration that cuts both ways or is not yet clear either way", "refs": [4]}}],
  "open_questions": ["a question the evidence leaves unresolved, ending with a question mark"],
  "analysis": "3-5 sentences of neutral synthesis: what the evidence supports, what remains uncertain, what to watch next"}}
Classify every item of kind news or social: whether it reads positive, negative or neutral for the asset's investment case.
Topics only from: {topics}. At most 3 bull, 3 bear and 2 neutral points, and at most 3 open questions. If the evidence
does not support one side, return an empty list for it — do not invent a counter-argument."""


def _ai_calls_today(db: Session) -> list[str]:
    row = db.get(MarketCache, AI_LOG_KEY)
    cutoff = (utcnow() - timedelta(hours=24)).isoformat()
    return [t for t in (row.payload.get("v", []) if row else []) if t >= cutoff]


def _log_ai_call(db: Session) -> None:
    calls = [*_ai_calls_today(db), utcnow().isoformat()]
    row = db.get(MarketCache, AI_LOG_KEY)
    if row is None:
        db.add(MarketCache(key=AI_LOG_KEY, payload={"v": calls}, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = {"v": calls}, utcnow()
    db.commit()


def _ai_available(db: Session) -> bool:
    return bool(llm.status().get("configured")) and len(_ai_calls_today(db)) < get_settings().pulse_ai_daily_limit


def _numbers_ok(text: str, allowed: set[str]) -> bool:
    for n in _NUMBER.findall(text):
        v = n.replace(",", "").lstrip("$").rstrip("%")
        if v in allowed:
            continue
        try:
            if float(v) <= 10 and "." not in v and "%" not in n and "$" not in n:
                continue  # counting words ("two quarters")
        except ValueError:
            pass
        return False
    return True


def _clean(text: Any, limit: int, allowed: set[str]) -> str | None:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or _PERSONAL.search(t) or not _numbers_ok(t, allowed):
        return None
    return t[:limit]


def _synthesize(db: Session, ref: AssetRef, items: list[dict[str, Any]], sc: dict[str, Any], th: list[dict[str, Any]],
                evs: list[SourceEvent]) -> dict[str, Any] | None:  # fmt: skip
    """One model call: per-item classification (stored) plus the narrative. Returns None when it can't be trusted."""
    numbered = sorted(items, key=lambda i: i["published_at"], reverse=True)[:50]
    lines = []
    for n, i in enumerate(numbered, 1):
        extra = f" — {str(i['summary'])[:240]}" if i.get("summary") else ""
        lines.append(f"[{n}] {i['published_at']:%Y-%m-%d} · {i['publisher'] or i['provider']} · {i['kind']} · {i['title']}{extra}")
    text_all = "\n".join(lines)
    allowed = {n.replace(",", "").lstrip("$").rstrip("%") for n in _NUMBER.findall(text_all)}
    from app.services.pulse import TOPICS

    score_line = f"{sc['value']}/100 ({sc['label']}), coverage {sc['coverage']}, {sc['items']} items" if sc["available"] else "not enough evidence"
    prompt = SYNTH_USER.format(name=ref.name, symbol=ref.symbol, exchange=f" · {(ref.quote or {}).get('exchange')}" if (ref.quote or {}).get("exchange") else "",
                               score_line=score_line, themes=", ".join(t["label"] for t in th[:6]) or "none",
                               lines=text_all, types=", ".join(CONTENT_TYPES), topics=", ".join([*TOPICS, *pulse_analysis.EXTRA_THEMES]))  # fmt: skip
    models = [m.strip() for m in get_settings().pulse_model.split(",") if m.strip()]
    _log_ai_call(db)
    try:
        msg = llm.chat([{"role": "system", "content": SYNTH_SYSTEM}, {"role": "user", "content": prompt}], max_tokens=1800,
                       temperature=0.2, models=models)  # fmt: skip
    except llm.LLMUnavailable as exc:
        log.warning("pulse synthesis unavailable for %s: %s", ref.symbol, exc.reason)
        return None
    m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        out = None
    if not isinstance(out, dict):
        return None
    model = msg.get("_model")

    # 1. classifications → stored on the evidence (the score is then recomputed from stored readings)
    by_id = {e.id: e for e in evs}
    topic_keys = {*TOPICS, *pulse_analysis.EXTRA_THEMES}
    for c in out.get("classifications") or []:
        if not isinstance(c, dict) or not isinstance(c.get("n"), int | float) or c.get("sentiment") not in ("positive", "neutral", "negative"):
            continue
        k = int(c["n"])
        if not 1 <= k <= len(numbered) or numbered[k - 1]["kind"] not in pulse_analysis.DISCUSSION_KINDS:
            continue
        e = by_id.get(numbered[k - 1]["id"])
        if e is not None:
            topics = [t for t in (c.get("topics") or []) if t in topic_keys][:4]
            e.analysis = {"label": c["sentiment"], "method": "ai", "topics": topics, "model": model,
                          "lexicon": (e.analysis or {}).get("score")}  # fmt: skip
            e.sentiment = c["sentiment"]
    db.commit()

    # 2. the narrative, validated point by point
    def refs(raw: Any) -> list[str]:
        return list(dict.fromkeys(numbered[int(x) - 1]["key"] for x in (raw or []) if isinstance(x, int | float) and 1 <= int(x) <= len(numbered)))[:5]

    def side(key: str) -> list[dict[str, Any]]:
        pts = []
        for p in (out.get(key) or [])[:3]:
            if not isinstance(p, dict):
                continue
            text, r = _clean(p.get("point"), 320, allowed), refs(p.get("refs"))
            if text and r:
                pts.append({"point": text, "refs": r})
        return pts

    deb = out.get("debate") if isinstance(out.get("debate"), dict) else {}
    debate = {k: _clean(deb.get(k), 300, allowed) for k in ("question", "bull_side", "bear_side")}
    ctype = out.get("content_type") if out.get("content_type") in CONTENT_TYPES else None
    questions = [q for q in (_clean(x, 220, allowed) for x in (out.get("open_questions") or [])[:3] if isinstance(x, str)) if q and q.endswith("?")]
    return {
        "title": _clean(out.get("title"), 120, allowed), "summary": _clean(out.get("whats_happening"), 900, allowed),
        "bull": side("bull"), "bear": side("bear"), "neutral": side("neutral")[:2], "open_questions": questions,
        "debate": debate if all(debate.values()) else None,
        "split": _clean(out.get("split"), 400, allowed), "analysis": _clean(out.get("analysis"), 1400, allowed),
        "content_type": ctype, "method": "ai", "model": model,
    }  # fmt: skip


# ------------------------------------------------------------------ history


def _snapshots(db: Session, symbol: str, days: int = 120) -> list[PulseSnapshot]:
    since = utcnow().date() - timedelta(days=days)
    return list(db.scalars(select(PulseSnapshot).where(PulseSnapshot.symbol == symbol, PulseSnapshot.day >= since).order_by(PulseSnapshot.day)))


def _near(snaps: list[PulseSnapshot], target: date, tolerance: int) -> PulseSnapshot | None:
    best = min(snaps, key=lambda s: abs((s.day - target).days), default=None)
    return best if best is not None and abs((best.day - target).days) <= tolerance and best.score is not None else None


def _what_changed(prev: PulseSnapshot | None, sc: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    if prev is None:
        return {"available": False, "reason": "Not enough historical data yet. Pulse records one reading per day from the first day an asset is analysed."}
    if prev.score is None or not sc["available"]:
        return {"available": False, "since": prev.day.isoformat(),
                "reason": "There isn't a reliable score on both days to compare."}  # fmt: skip
    old = set(map(str, prev.source_ids or []))
    new = [i for i in items if i["id"] is not None and str(i["id"]) not in old and i["kind"] in pulse_analysis.DISCUSSION_KINDS]
    by_theme: dict[str, dict[str, Any]] = {}
    for i in new:
        for t in i["topics"] or ["general"]:
            a = by_theme.setdefault(t, {"key": t, "label": pulse_analysis.theme_label(t) if t != "general" else "General news",
                                        "points": 0.0, "evidence": []})  # fmt: skip
            a["points"] += i["contribution"]
            if len(a["evidence"]) < 4:
                a["evidence"].append(i["key"])
    for a in by_theme.values():
        a["points"] = round(a["points"], 1)
    up = sorted((a for a in by_theme.values() if a["points"] >= 0.3), key=lambda a: -a["points"])[:4]
    down = sorted((a for a in by_theme.values() if a["points"] <= -0.3), key=lambda a: a["points"])[:4]
    delta = sc["value"] - prev.score
    since = f"{prev.day.day} {prev.day:%b}"
    if delta == 0:
        head = f"Pulse is unchanged at {sc['value']} since {since}."
    else:
        head = f"Pulse {'rose' if delta > 0 else 'fell'} from {prev.score} to {sc['value']} since {since}."
    if not new:
        tail = " No new evidence arrived since then; the change comes from older items carrying less weight as they age." if delta else ""
    else:
        parts = []
        if up:
            parts.append("new coverage of " + ", ".join(a["label"] for a in up[:2]) + " read positive")
        if down:
            parts.append(", ".join(a["label"] for a in down[:2]) + " weighed on it")
        tail = (" " + "; ".join(parts).capitalize() + ".") if parts else f" {len(new)} new {'item' if len(new) == 1 else 'items'} arrived, mostly neutral in tone."
    return {"available": True, "from": prev.score, "to": sc["value"], "change": delta, "since": prev.day.isoformat(),
            "new_items": len(new), "positive": up, "negative": down, "summary": head + tail}  # fmt: skip


def _record(db: Session, symbol: str, sc: dict[str, Any], th: list[dict[str, Any]], source_ids: list[int]) -> None:
    today = utcnow().date()
    snap = db.scalars(select(PulseSnapshot).where(PulseSnapshot.symbol == symbol, PulseSnapshot.day == today)).first()
    if snap is None:
        snap = PulseSnapshot(symbol=symbol, day=today)
        db.add(snap)
    snap.score, snap.sentiment, snap.confidence, snap.items = sc["value"], sc["sentiment"], sc["coverage"], sc["items"]
    snap.distribution = sc["distribution"]
    snap.themes = [{"key": t["key"], "label": t["label"], "count": t["count"], "net": t["net"]} for t in th[:6]]
    snap.source_ids = source_ids
    snap.recorded_at = utcnow()


# ------------------------------------------------------------------ trending signals


def _trend(items: list[dict[str, Any]], sc: dict[str, Any], shift7: int | None, bull: list, bear: list) -> dict[str, Any]:
    now = utcnow()
    disc = [i for i in items if i["kind"] in pulse_analysis.DISCUSSION_KINDS]
    last24 = [i for i in disc if now - i["published_at"] <= timedelta(hours=24)]
    prior = [i for i in disc if timedelta(hours=24) < now - i["published_at"] <= timedelta(days=8)]
    daily = len(prior) / 7
    pubs48 = {(i["publisher"] or "").lower() for i in disc if now - i["published_at"] <= timedelta(hours=48) and i["publisher"]}
    day = next((i for i in items if i["key"].endswith(":1d")), None)
    move = day["facts"]["change_pct"] if day else None
    filings = [i for i in items if i["kind"] == "filing" and now - i["published_at"] <= timedelta(days=3)]
    reasons = []
    if len(last24) >= 3 and len(last24) >= 2 * max(daily, 0.5):
        reasons.append({"key": "rising_interest", "label": "Rising interest",
                        "detail": f"{len(last24)} new items in 24 hours (about {daily:.1f} a day the week before)"})  # fmt: skip
    if shift7 is not None and abs(shift7) >= 8:
        reasons.append({"key": "sentiment_shift", "label": "Sentiment shift", "detail": f"Pulse {shift7:+d} over 7 days"})
    if move is not None and abs(move) >= 3:
        reasons.append({"key": "price_move", "label": "Price move", "detail": f"{move:+.1f}% in the latest session (delayed)"})
    if filings:
        reasons.append({"key": "new_event", "label": "New market event", "detail": filings[0]["title"]})
    earnings = [i for i in disc if "earnings" in i["topics"] and now - i["published_at"] <= timedelta(days=3)]
    if len(earnings) >= 2:
        reasons.append({"key": "earnings", "label": "Earnings in focus", "detail": f"{len(earnings)} earnings items in 3 days"})
    if bull and bear and sc["available"] and min(sc["distribution"]["positive"], sc["distribution"]["negative"]) >= 2:
        reasons.append({"key": "biggest_debate", "label": "Divided debate",
                        "detail": f"{sc['distribution']['positive']} positive vs {sc['distribution']['negative']} negative items"})  # fmt: skip
    score = (1.5 * len(last24) + 0.5 * len(pubs48) + 0.25 * abs(shift7 or 0) + 0.6 * abs(move or 0)
             + (3 if filings else 0) + (2 if any(r["key"] == "biggest_debate" for r in reasons) else 0))  # fmt: skip
    return {"score": round(score, 2), "reasons": reasons, "items_24h": len(last24), "publishers_48h": len(pubs48)}


# ------------------------------------------------------------------ compute


def _evidence_hash(evs: list[SourceEvent]) -> str:
    return hashlib.sha1(json.dumps(sorted(e.id for e in evs)).encode()).hexdigest()


def compute(db: Session, symbol: str, force_ai: bool = False) -> MarketDiscussion:
    """Run the whole pipeline for one asset and cache the result."""
    s = get_settings()
    ref = resolve(db, symbol)
    found, status = _gather(db, ref)
    try:
        _store(db, ref, found)
    except IntegrityError:  # a concurrent run stored the same item first
        db.rollback()
        _store(db, ref, found)
    evs = _evidence(db, ref.symbol)
    _read(db, evs)
    live = [i for i in found if not i.persist]
    now = utcnow()
    items = _items(evs, live)
    sc = pulse_analysis.score(items, now)
    th = pulse_analysis.themes(items)

    md = db.scalars(select(MarketDiscussion).where(MarketDiscussion.symbol == ref.symbol)).first()
    ehash = _evidence_hash(evs)
    narrative: dict[str, Any] | None = None
    due = md is None or md.method != "ai" or md.synthesized_at is None or now - md.synthesized_at >= timedelta(minutes=s.pulse_ai_refresh_minutes)
    changed = md is None or md.evidence_hash != ehash or md.method != "ai"
    if sc["available"] and changed and (due or force_ai) and _ai_available(db):
        narrative = _synthesize(db, ref, items, sc, th, evs)
        if narrative is not None:  # rescore with the readings the model stored
            items = _items(evs, live)
            sc = pulse_analysis.score(items, now)
            th = pulse_analysis.themes(items)
            fallback = _rules(ref, items, sc, th)
            narrative = {k: (v if v not in (None, []) or k in ("bull", "bear", "debate") else fallback.get(k)) for k, v in narrative.items()}
            narrative["content_type"] = narrative.get("content_type") or fallback["content_type"]
    keep_ai = narrative is None and md is not None and md.method == "ai" and sc["available"]
    if narrative is None and not keep_ai:
        narrative = _rules(ref, items, sc, th)

    snaps = _snapshots(db, ref.symbol)
    prev = next((x for x in reversed(snaps) if x.day < now.date()), None)
    d7 = _near(snaps, now.date() - timedelta(days=7), 1)
    changed_info = _what_changed(prev, sc, items)
    shift7 = sc["value"] - d7.score if (d7 and sc["available"]) else None

    if md is None:
        md = MarketDiscussion(symbol=ref.symbol, computed_at=now, updated_at=now)
        db.add(md)
    q = ref.quote or {}
    md.name, md.exchange, md.asset_type = ref.name, (q.get("exchange") or md.exchange), (q.get("type") or md.asset_type)
    if narrative is not None:
        md.title, md.summary, md.split, md.analysis = narrative["title"], narrative["summary"], narrative["split"], narrative["analysis"]
        md.bull_case, md.bear_case, md.debate = narrative["bull"], narrative["bear"], narrative["debate"]
        md.content_type, md.method, md.model = narrative["content_type"], narrative["method"], narrative["model"]
        if narrative["method"] == "ai":
            md.synthesized_at, md.evidence_hash = now, ehash
        else:
            md.synthesized_at, md.evidence_hash = None, ehash
    md.themes, md.pulse_score, md.sentiment, md.confidence = th, sc["value"], sc["sentiment"], sc["coverage"]
    md.what_changed = changed_info
    md.source_ids = [e.id for e in evs]
    evidence = sorted(items, key=lambda i: i["published_at"], reverse=True)
    md.evidence = [
        {"key": i["key"], "id": i["id"], "kind": i["kind"], "provider": i["provider"], "title": i["title"], "url": i["url"],
         "publisher": i["publisher"], "published_at": i["published_at"].isoformat() + "Z", "sentiment": i["label"],
         "score": i["score"], "weight": i["weight"], "contribution": i["contribution"], "topics": i["topics"], "method": i["method"],
         "facts": i.get("facts") or {}}
        for i in evidence
    ]  # fmt: skip
    # Neutral considerations and open questions travel with the narrative that produced them.
    extra = ({"neutral": narrative.get("neutral") or [], "open_questions": narrative.get("open_questions") or []} if narrative is not None
             else {k: (md.stats or {}).get(k) or [] for k in ("neutral", "open_questions")})  # fmt: skip
    md.stats = {
        "score": {k: v for k, v in sc.items()}, "quote": {k: q.get(k) for k in ("price", "change", "change_pct", "currency", "market_time", "exchange", "type")} if q else None,
        "connectors": status, "trend": _trend(items, sc, shift7, md.bull_case, md.bear_case), "shift_7d": shift7,
        "newest_evidence_at": max((i["published_at"] for i in items if i["kind"] != "market_data"), default=None), **extra,
    }  # fmt: skip
    if md.stats["newest_evidence_at"] is not None:
        md.stats["newest_evidence_at"] = md.stats["newest_evidence_at"].isoformat() + "Z"
    md.computed_at = md.updated_at = now
    _record(db, ref.symbol, sc, th, md.source_ids)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalars(select(MarketDiscussion).where(MarketDiscussion.symbol == ref.symbol)).first()
        if existing is None:
            raise
        return existing
    return md


def get(db: Session, symbol: str, refresh: bool = False) -> MarketDiscussion:
    """The cached analysis, recomputed when missing, older than ``pulse_fresh_minutes`` or when asked to."""
    sym = markets.clean_symbol(symbol)
    md = db.scalars(select(MarketDiscussion).where(MarketDiscussion.symbol == sym)).first()
    fresh = md is not None and utcnow() - md.computed_at < timedelta(minutes=get_settings().pulse_fresh_minutes)
    if md is not None and fresh and not refresh:
        return md
    try:
        return compute(db, sym, force_ai=refresh)
    except NotFoundError:
        raise
    except NexisError:
        if md is not None:  # serve the last analysis, marked by its own timestamps, rather than nothing
            return md
        raise


# ------------------------------------------------------------------ output


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() + "Z" if d else None


def history(db: Session, symbol: str) -> dict[str, Any]:
    snaps = _snapshots(db, symbol, days=365)
    today = utcnow().date()
    cur = snaps[-1] if snaps else None
    d7, d30 = _near(snaps, today - timedelta(days=7), 1), _near(snaps, today - timedelta(days=30), 3)
    points = [{"day": s.day.isoformat(), "score": s.score, "sentiment": s.sentiment, "coverage": s.confidence, "items": s.items}
              for s in snaps]  # fmt: skip
    scored = [p for p in points if p["score"] is not None]
    return {
        "points": points, "first_recorded": points[0]["day"] if points else None,
        "current": cur.score if cur else None,
        "d7": {"score": d7.score, "day": d7.day.isoformat()} if d7 else None,
        "d30": {"score": d30.score, "day": d30.day.isoformat()} if d30 else None,
        "change_7d": (cur.score - d7.score) if (cur and d7 and cur.score is not None) else None,
        "change_30d": (cur.score - d30.score) if (cur and d30 and cur.score is not None) else None,
        "enough": len(scored) >= 2,
        "note": None if len(scored) >= 2 else "Not enough historical data. Pulse records one reading per asset per day, starting the first day it is analysed — no history is back-filled.",
    }  # fmt: skip


def serialize(db: Session, md: MarketDiscussion, live_quote: bool = True) -> dict[str, Any]:
    st = md.stats or {}
    sc = st.get("score") or {}
    quote = st.get("quote")
    if live_quote:
        try:
            q = (markets.quotes(db, [md.symbol]) or [None])[0]
        except NexisError:
            q = None
        if q:
            quote = {k: q.get(k) for k in ("price", "change", "change_pct", "currency", "market_time", "exchange", "type")}
    ev = {e["key"]: e for e in md.evidence or []}

    def cite(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = [{"point": p["point"], "evidence": [ev[k] for k in p.get("refs", []) if k in ev]} for p in points or []]
        return [p for p in out if p["evidence"]]  # a point whose sources have all aged out is no longer shown

    from app.services.events import PROVIDERS

    for e in ev.values():
        e["provider_label"] = PROVIDERS[e["provider"]].label if e["provider"] in PROVIDERS else e["provider"]
    return {
        "symbol": md.symbol, "name": md.name, "exchange": md.exchange or (quote or {}).get("exchange"), "asset_type": md.asset_type,
        "quote": quote, "content_type": md.content_type, "title": md.title,
        "score": {"available": md.pulse_score is not None, "value": md.pulse_score, "label": sc.get("label"), "sentiment": md.sentiment,
                  "coverage": md.confidence, "coverage_text": sc.get("coverage_text") or pulse_analysis.COVERAGE_TEXT.get(md.confidence),
                  "items": sc.get("items", 0), "publishers": sc.get("publishers", 0), "distribution": sc.get("distribution"),
                  "window_days": sc.get("window_days", pulse_analysis.WINDOW_DAYS), "disclaimer": pulse_analysis.DISCLAIMER},
        "summary": md.summary, "bull": cite(md.bull_case), "bear": cite(md.bear_case), "debate": md.debate, "split": md.split,
        "what_changed": md.what_changed, "analysis": md.analysis, "themes": md.themes,
        "evidence": list(ev.values()), "history": history(db, md.symbol), "trend": st.get("trend"),
        "narrative": {"method": md.method, "model": md.model, "written_at": _iso(md.synthesized_at)},
        "freshness": {"computed_at": _iso(md.computed_at), "written_at": _iso(md.synthesized_at),
                      "newest_evidence_at": st.get("newest_evidence_at"), "quote_time": (quote or {}).get("market_time"),
                      "window_days": pulse_analysis.WINDOW_DAYS},
        "connectors": st.get("connectors") or {},
    }  # fmt: skip


def card(md: MarketDiscussion) -> dict[str, Any]:
    st = md.stats or {}
    return {
        "symbol": md.symbol, "name": md.name, "exchange": md.exchange, "title": md.title, "summary": md.summary,
        "content_type": md.content_type, "score": md.pulse_score, "sentiment": md.sentiment, "label": (st.get("score") or {}).get("label"),
        "coverage": md.confidence, "debate": md.debate, "themes": [{"key": t["key"], "label": t["label"]} for t in (md.themes or [])[:4]],
        "trend": st.get("trend"), "shift_7d": st.get("shift_7d"), "quote": st.get("quote"), "computed_at": _iso(md.computed_at),
        "items": (st.get("score") or {}).get("items", 0),
    }  # fmt: skip


def public_symbols(db: Session) -> set[str]:
    """Assets that may appear in public lists: the core universe and assets someone has opened in Pulse.

    Holdings and watchlists are private and are never used to decide what appears publicly.
    """
    return set(events.CORE_UNIVERSE) | set(db.scalars(select(MarketDiscussion.symbol)))


def trending(db: Session, limit: int = 8) -> dict[str, Any]:
    since = utcnow() - timedelta(hours=36)
    rows = list(db.scalars(select(MarketDiscussion).where(MarketDiscussion.computed_at >= since)))
    live = [r for r in rows if r.confidence != "none"]
    live.sort(key=lambda r: -((r.stats or {}).get("trend") or {}).get("score", 0))
    shifts = sorted((r for r in live if (r.stats or {}).get("shift_7d") not in (None, 0)), key=lambda r: -abs(r.stats["shift_7d"]))
    themes: dict[str, dict[str, Any]] = {}
    for r in live:
        for t in r.themes or []:
            a = themes.setdefault(t["key"], {"key": t["key"], "label": t["label"], "items": 0, "assets": []})
            a["items"] += t["count"]
            if len(a["assets"]) < 4:
                a["assets"].append(r.symbol)
    pub = public_symbols(db)
    evs = [e for e in events.important(db, limit=40) if e.provider not in ("nexis",) and set(e.assets or []) & pub][:8]
    return {
        "debates": [card(r) for r in live[:limit]],
        "shifts": [card(r) for r in shifts[:6]],
        "themes": sorted(themes.values(), key=lambda a: -a["items"])[:8],
        "events": [events.serialize(e) for e in evs],
        "analysed_assets": len(rows), "insufficient": [card(r) for r in rows if r.confidence == "none"][:6],
        "updated_at": _iso(max((r.computed_at for r in rows), default=None)),
        "disclaimer": pulse_analysis.DISCLAIMER,
    }  # fmt: skip


def context_text(db: Session, symbol: str) -> str:
    """The Pulse analysis written out for the AI advisor: score, sides, themes, history and the sources behind them."""
    d = serialize(db, get(db, symbol), live_quote=False)
    sc = d["score"]
    lines = [f"[NEXIS PULSE CONTEXT for {d['name']} ({d['symbol']}) — computed {d['freshness']['computed_at']} from stored, sourced evidence]"]
    if sc["available"]:
        lines.append(f"Pulse score: {sc['value']}/100 ({sc['label']}); coverage: {sc['coverage']} — {sc['coverage_text']} "
                     f"Items: {sc['items']} from {sc['publishers']} publishers over {sc['window_days']} days; "
                     f"distribution: {sc['distribution']}. The score measures the tone of market discussion, not a price prediction.")  # fmt: skip
    else:
        lines.append(f"Pulse score: not available — {sc['coverage_text']}")
    q = d.get("quote") or {}
    if q.get("price") is not None:
        lines.append(f"Quote (delayed): {q['price']} {q.get('currency') or ''}, {((q.get('change_pct') or 0) * 100):+.2f}% on the day.")
    if d["summary"]:
        lines.append(f"What's happening: {d['summary']}")
    for side in ("bull", "bear"):
        for p in d[side]:
            src = "; ".join(f"{e['publisher'] or e['provider_label']}, {e['published_at'][:10]}: {e['title']}" for e in p["evidence"][:3])
            lines.append(f"{'Bull' if side == 'bull' else 'Bear'} case: {p['point']} [sources: {src}]")
    if d["debate"]:
        lines.append(f"Central question: {d['debate']['question']}")
    if d["split"]:
        lines.append(f"Where it splits: {d['split']}")
    if d["themes"]:
        lines.append("Themes: " + ", ".join(f"{t['label']} ({t['count']} items, net {t['net']:+} pts)" for t in d["themes"][:6]))
    wc = d["what_changed"] or {}
    lines.append(f"What changed: {wc.get('summary') or wc.get('reason') or 'n/a'}")
    h = d["history"]
    lines.append(f"History: 7 days ago {h['d7']['score'] if h['d7'] else 'no reading'}, 30 days ago {h['d30']['score'] if h['d30'] else 'no reading'}"
                 f"{'' if h['enough'] else ' (not enough historical data)'}.")  # fmt: skip
    lines.append("Evidence (newest first):")
    for e in d["evidence"][:25]:
        lines.append(f"- {e['published_at'][:10]} · {e['publisher'] or e['provider_label']} · {e['kind']} · {e['sentiment']} · {e['title']}"
                     + (f" · {e['url']}" if (e.get("url") or "").startswith("http") else ""))  # fmt: skip
    lines.append("[End of Pulse context. Use only this and tool results; say so when the evidence is thin.]")
    return "\n".join(lines)
