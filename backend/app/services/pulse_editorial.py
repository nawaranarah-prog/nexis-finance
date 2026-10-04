"""Nexis editorial discussions: context about what the market is debating, kept current as news arrives.

Two kinds of editorial discussion, each identified by a permanent ``editorial_key``:

* ``asset:<SYMBOL>`` — built from the asset's evidence-based market debate (``market_pulse``): what happened, where
  the debate splits, bull / bear / neutral points that cite their sources, and open questions.
* ``theme:<key>`` — a market-wide theme (rates, oil, AI infrastructure, UAE markets, US equities) built from recent
  sourced events tagged with that theme.

:func:`publish` is the only writer. It stores the sources, compares the new content with what was there, and
appends a timestamped ``PulseDiscussionUpdate`` for each material change (a new development, a new bullish or
bearish argument, a new open question). Editorial text evolves; community comments under it are never touched.

When there is not enough sourced coverage, nothing is published — an empty debate is worse than none.
Everything here is labelled Nexis; nothing is attributed to a person, and nothing creates comments or engagement.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NexisError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import MarketCache, PulseDiscussion, PulseDiscussionSource, PulseDiscussionUpdate, SourceEvent
from app.services import llm, market_pulse, pulse, pulse_analysis

log = get_logger(__name__)

MAX_SOURCES = 16
# How each kind of update is introduced (headlines are stored without it).
UPDATE_LABELS = {"opened": "Opened", "development": "New development", "what_changed": "What changed", "new_bull": "New bullish argument",
                 "new_bear": "New bearish argument", "open_question": "Open question", "correction": "Correction"}
MAX_UPDATES_PER_SYNC = 4

THEMES: dict[str, dict[str, Any]] = {
    "ai-infrastructure": {
        "title": "Is the AI infrastructure boom entering a new phase?", "label": "AI infrastructure",
        "match": ("ai",), "rx": r"\b(ai|data cent(er|re)s?|gpus?|chips?|semiconductor\w*|hyperscalers?|capex|nvidia)\b",
        "topics": ["ai", "semiconductors", "technology"], "symbols": ["NVDA", "MSFT", "AMD", "AVGO", "GOOGL", "AMZN"],
        "questions": ["Can AI infrastructure spending keep growing at its current pace?",
                      "Will returns on AI investment show up in company earnings soon enough to justify the spending?"],
    },
    "rates": {
        "title": "Interest rates: how much easing is the market expecting?", "label": "Interest rates",
        "match": ("rates",), "rx": r"\b(fed|federal reserve|interest rates?|rate cuts?|rate hikes?|central banks?|yields?|treasur(y|ies)|inflation)\b",
        "topics": ["rates", "inflation", "macro"], "symbols": ["^GSPC", "JPM", "FAB.AD", "EMIRATESNBD.AE"],
        "questions": ["Will inflation data allow central banks to keep easing?", "How sensitive are bank margins to lower rates?"],
    },
    "oil": {
        "title": "Oil: supply discipline versus demand worries", "label": "Oil and energy",
        "match": ("energy",), "rx": r"\b(oil|opec\+?|brent|crude|lng|natural gas)\b",
        "topics": ["energy", "macro"], "symbols": ["BZ=F", "ADNOCGAS.AD"],
        "questions": ["Will producers keep supply tight if prices soften?", "How much of the demand outlook depends on China?"],
    },
    "uae-markets": {
        "title": "UAE markets: what is driving ADX and DFM right now?", "label": "UAE markets",
        "match": (), "rx": r"\b(uae|dubai|abu dhabi|adx|dfm|emirates|emaar|aldar|adnoc|fab|first abu dhabi)\b",
        "topics": ["uae_markets", "banks", "real_estate"], "symbols": ["EMAAR.AE", "FAB.AD", "ALDAR.AD", "EMIRATESNBD.AE", "ADNOCGAS.AD"],
        "questions": ["Can property and bank earnings keep supporting UAE indices?", "How exposed are UAE markets to a change in oil prices?"],
    },
    "us-equities": {
        "title": "US stocks: how much good news is already priced in?", "label": "US equities",
        "match": (), "rx": r"\b(s&p ?500|s&p|nasdaq|dow jones|wall street|us stocks|stock market|equities)\b",
        "topics": ["us_markets", "valuation", "earnings"], "symbols": ["^GSPC", "^IXIC"],
        "questions": ["Are earnings growing fast enough to support current valuations?", "What would it take to break the current trend?"],
    },
}  # fmt: skip


# ------------------------------------------------------------------ the single writer


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _first_sentence(text: str | None, n: int = 220) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    s = m.group(1) if m else t
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _http(url: str | None) -> bool:
    return bool(url) and str(url).startswith(("https://", "http://"))


def publish(
    db: Session,
    key: str,
    *,
    title: str,
    what_happened: str,
    debate: str | None,
    bull: list[dict[str, Any]],
    bear: list[dict[str, Any]],
    neutral: list[dict[str, Any]] | None = None,
    open_questions: list[str] | None = None,
    sources: list[dict[str, Any]],
    symbols: list[str],
    asset_name: str | None = None,
    topics: list[str] | None = None,
    ai_assisted: bool = False,
    model: str | None = None,
    what_changed: str | None = None,
    notify: bool = True,
) -> tuple[PulseDiscussion, list[PulseDiscussionUpdate]]:
    """Create or update one editorial discussion. ``bull``/``bear``/``neutral`` points cite ``sources`` by their ``key``.

    Returns the discussion and the updates appended by this call (empty when nothing material changed).
    """
    now = utcnow()
    d = db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key == key)).first()
    created = d is None
    if d is None:
        d = PulseDiscussion(public_id=pulse.new_public_id(db), kind="editorial", editorial_key=key, title=title[:200], body="",
                            status="visible", created_at=now, last_activity_at=now, content_updated_at=now)  # fmt: skip
        db.add(d)
        db.flush()
    old_bull = {_norm(p.get("point", "")) for p in d.bull_case or []}
    old_bear = {_norm(p.get("point", "")) for p in d.bear_case or []}
    old_questions = {_norm(q) for q in d.open_questions or []}
    have = {s.url: s for s in db.scalars(select(PulseDiscussionSource).where(PulseDiscussionSource.discussion_id == d.id))}
    fresh_sources: list[PulseDiscussionSource] = []
    ids: dict[str, int] = {}
    for s in sources[:MAX_SOURCES]:
        if not _http(s.get("url")):
            continue
        row = have.get(s["url"])
        if row is None:
            row = PulseDiscussionSource(discussion_id=d.id, title=str(s.get("title") or s["url"])[:500], url=str(s["url"])[:1500],
                                        publisher=(s.get("publisher") or None) and str(s["publisher"])[:160], published_at=s.get("published_at"),
                                        retrieved_at=now, event_id=s.get("event_id"))  # fmt: skip
            db.add(row)
            db.flush()
            have[row.url] = row
            fresh_sources.append(row)
        ids[str(s["key"])] = row.id

    def cite(points: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return [{"point": str(p["point"])[:400], "sources": [ids[str(r)] for r in p.get("refs") or [] if str(r) in ids][:4]}
                for p in points or [] if p.get("point")]  # fmt: skip

    bull_c, bear_c, neutral_c = cite(bull), cite(bear), cite(neutral)
    questions = [q.strip()[:240] for q in open_questions or [] if q and q.strip()][:4]
    content = {"title": title[:200], "what_happened": what_happened, "debate": debate, "bull": bull_c, "bear": bear_c,
               "neutral": neutral_c, "questions": questions}  # fmt: skip
    digest = hashlib.sha1(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
    changed = created or digest != d.evidence_hash

    updates: list[PulseDiscussionUpdate] = []

    def add(kind: str, headline: str, body: str | None = None, src: list[int] | None = None) -> None:
        if len(updates) < MAX_UPDATES_PER_SYNC:
            updates.append(PulseDiscussionUpdate(discussion_id=d.id, kind=kind, headline=headline[:300], body=body, source_ids=src or [], created_at=now))

    if created:
        add("opened", _first_sentence(what_happened), None, [s.id for s in fresh_sources[:3]])
    elif changed:
        newest = sorted(fresh_sources, key=lambda s: s.published_at or datetime.min, reverse=True)
        for s in newest[:2]:
            add("development", s.title, s.publisher, [s.id])
        if what_changed:
            add("what_changed", _first_sentence(what_changed, 280), what_changed if len(what_changed) > 280 else None)
        for p in bull_c:
            if _norm(p["point"]) not in old_bull:
                add("new_bull", p["point"], None, p["sources"])
                break
        for p in bear_c:
            if _norm(p["point"]) not in old_bear:
                add("new_bear", p["point"], None, p["sources"])
                break
        for q in questions:
            if _norm(q) not in old_questions:
                add("open_question", q)
                break
    if changed:
        d.title, d.what_happened, d.debate = title[:200], what_happened, debate
        d.bull_case, d.bear_case, d.neutral_case, d.open_questions = bull_c, bear_c, neutral_c, questions
        d.ai_assisted, d.model = ai_assisted, model
        d.evidence_hash = digest
        d.primary_symbol = symbols[0] if len(symbols) == 1 else d.primary_symbol
        d.asset_name = asset_name or d.asset_name
        d.symbols = list(dict.fromkeys(symbols))[:10]
        pulse._set_assets(db, d, d.symbols)
        pulse._set_topics(db, d, [t for t in (topics or pulse.detect_topics(f"{title} {what_happened}", symbols)) if t in pulse.TOPICS][:3])
    if updates:
        d.content_updated_at = now
        d.last_activity_at = max(d.last_activity_at, now)
        db.add_all(updates)
    db.commit()
    if notify and d.status == "visible":
        from app.services import alerts

        if created:
            alerts.notify_new_discussion(db, d)
        elif updates:
            alerts.notify_update(db, d, updates[0])  # one note per sync, never one per line
    return d, updates


# ------------------------------------------------------------------ assets (from the evidence-based market debate)


def sync_asset(db: Session, symbol: str, refresh: bool = False) -> dict[str, Any]:
    try:
        md = market_pulse.get(db, symbol, refresh=refresh)
    except NexisError as exc:
        return {"key": f"asset:{symbol}", "skipped": exc.message}
    if md.confidence == "none" or not (md.bull_case or md.bear_case):
        return {"key": f"asset:{md.symbol}", "skipped": "not enough sourced coverage to describe a debate"}
    ev = {e["key"]: e for e in md.evidence or []}
    st = md.stats or {}
    cited = list(dict.fromkeys(r for p in [*md.bull_case, *md.bear_case, *(st.get("neutral") or [])] for r in p.get("refs") or []))
    picks = [ev[k] for k in cited if k in ev and _http(ev[k].get("url"))]
    picks += [e for e in ev.values() if e["key"] not in cited and _http(e.get("url")) and e["kind"] != "market_data"][: max(0, 10 - len(picks))]
    sources = [{"key": e["key"], "title": e["title"], "url": e["url"], "publisher": e.get("publisher"), "event_id": e.get("id"),
                "published_at": datetime.fromisoformat(e["published_at"].rstrip("Z")) if e.get("published_at") else None} for e in picks]  # fmt: skip
    deb = md.debate or {}
    title = deb.get("question") or md.title or f"{md.name} — market debate"
    debate = md.split or (" ".join(x for x in (deb.get("bull_side"), deb.get("bear_side")) if x) or None)
    themes = [t["key"] for t in md.themes or [] if t["key"] in pulse.TOPICS]
    wc = md.what_changed or {}
    d, ups = publish(
        db, f"asset:{md.symbol}", title=title, what_happened=md.summary or "", debate=debate, bull=md.bull_case, bear=md.bear_case,
        neutral=st.get("neutral") or [], open_questions=st.get("open_questions") or [], sources=sources, symbols=[md.symbol],
        asset_name=md.name, topics=list(dict.fromkeys([*themes[:2], *pulse.symbol_topics(md.symbol)]))[:3],
        ai_assisted=md.method == "ai", model=md.model, what_changed=wc.get("summary") if isinstance(wc, dict) else None,
    )  # fmt: skip
    return {"key": d.editorial_key, "id": d.public_id, "updates": [u.kind for u in ups]}


# ------------------------------------------------------------------ themes (from recent sourced events)

THEME_SYSTEM = (
    "You are the Nexis editorial desk. You turn a numbered list of recent, sourced headlines about one market theme into "
    "a neutral summary of what the market is debating. Use only the EVIDENCE: never add facts, figures, dates, quotes or "
    "views that are not in it, and only use numbers that appear in it. Describe arguments, never people: no investors, "
    "users, accounts or first person. Every point cites the evidence numbers it rests on. Never tell readers to buy, sell "
    "or hold. Output valid JSON only."
)
THEME_USER = """THEME: {label}
EVIDENCE (newest first)
{lines}

Return JSON:
{{"what_happened": "2-3 sentences: the developments driving this theme now",
  "debate": "1-2 sentences: what the two sides disagree about",
  "bull": [{{"point": "one sentence", "refs": [1]}}], "bear": [{{"point": "one sentence", "refs": [2]}}],
  "neutral": [{{"point": "one sentence", "refs": [3]}}],
  "open_questions": ["a question the evidence leaves unresolved?"]}}
At most 3 bull, 3 bear, 2 neutral points and 3 questions. Leave a list empty rather than inventing."""


def _theme_events(db: Session, theme: dict[str, Any], days: int = 14) -> list[SourceEvent]:
    rx = re.compile(theme["rx"], re.I)
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(select(SourceEvent).where(SourceEvent.published_at >= since, SourceEvent.provider.not_in(["nexis", "market_data"]))
                      .order_by(SourceEvent.published_at.desc()).limit(600))  # fmt: skip
    out, seen = [], set()
    for e in rows:
        if not _http(e.url):
            continue
        if not (set(theme["match"]) & set(e.topics or []) or rx.search(e.title or "")):
            continue
        k = _norm(e.title)[:90]
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out[:30]


def _theme_rules(theme: dict[str, Any], evs: list[SourceEvent]) -> dict[str, Any]:
    read = [(e, pulse_analysis.lexicon(e.title)["label"]) for e in evs]
    pos = [e for e, lab in read if lab == "positive"][:3]
    neg = [e for e, lab in read if lab == "negative"][:3]
    lead = evs[:3]
    happened = (f"Recent coverage of {theme['label'].lower()} has centred on "
                + "; ".join(f"“{e.title}” ({e.publisher or 'source'})" for e in lead) + ".")  # fmt: skip
    debate = None
    if pos and neg:
        debate = (f"Of the {len(read)} recent headlines Nexis matched to this theme, {sum(1 for _, x in read if x == 'positive')} read "
                  f"positive and {sum(1 for _, x in read if x == 'negative')} negative, so coverage points both ways.")  # fmt: skip
    return {
        "what_happened": happened, "debate": debate,
        "bull": [{"point": f"Supportive coverage: “{e.title}” ({e.publisher or 'source'}).", "refs": [str(e.id)]} for e in pos],
        "bear": [{"point": f"Cautious coverage: “{e.title}” ({e.publisher or 'source'}).", "refs": [str(e.id)]} for e in neg],
        "neutral": [], "open_questions": list(theme["questions"]), "ai": False, "model": None,
    }


def _theme_ai(db: Session, theme: dict[str, Any], evs: list[SourceEvent]) -> dict[str, Any] | None:
    numbered = evs[:25]
    lines = "\n".join(f"[{n}] {e.published_at:%Y-%m-%d} · {e.publisher or e.provider} · {e.title}" for n, e in enumerate(numbered, 1))
    allowed = {x.replace(",", "").lstrip("$").rstrip("%") for x in market_pulse._NUMBER.findall(lines)}
    market_pulse._log_ai_call(db)
    try:
        from app.core.config import get_settings

        models = [m.strip() for m in get_settings().pulse_model.split(",") if m.strip()]
        msg = llm.chat([{"role": "system", "content": THEME_SYSTEM}, {"role": "user", "content": THEME_USER.format(label=theme["label"], lines=lines)}],
                       max_tokens=1200, temperature=0.2, models=models)  # fmt: skip
    except llm.LLMUnavailable as exc:
        log.warning("theme synthesis unavailable for %s: %s", theme["label"], exc.reason)
        return None
    m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        out = None
    if not isinstance(out, dict):
        return None

    def side(k: str, n: int) -> list[dict[str, Any]]:
        pts = []
        for p in (out.get(k) or [])[:n]:
            if not isinstance(p, dict):
                continue
            text = market_pulse._clean(p.get("point"), 320, allowed)
            refs = [str(numbered[int(x) - 1].id) for x in p.get("refs") or [] if isinstance(x, int | float) and 1 <= int(x) <= len(numbered)]
            if text and refs:
                pts.append({"point": text, "refs": refs[:4]})
        return pts

    happened = market_pulse._clean(out.get("what_happened"), 900, allowed)
    if not happened:
        return None
    qs = [q for q in (market_pulse._clean(x, 220, allowed) for x in (out.get("open_questions") or [])[:3] if isinstance(x, str)) if q and q.endswith("?")]
    return {"what_happened": happened, "debate": market_pulse._clean(out.get("debate"), 400, allowed), "bull": side("bull", 3),
            "bear": side("bear", 3), "neutral": side("neutral", 2), "open_questions": qs or list(theme["questions"]), "ai": True,
            "model": msg.get("_model")}  # fmt: skip


def sync_theme(db: Session, key: str) -> dict[str, Any]:
    theme = THEMES[key]
    evs = _theme_events(db, theme)
    publishers = {(e.publisher or "").lower() for e in evs if e.publisher}
    if len(evs) < 4 or len(publishers) < 2:
        return {"key": f"theme:{key}", "skipped": "not enough sourced coverage of this theme in the last 14 days"}
    ref = {str(e.id): e for e in evs}
    # Only rewrite (and spend a model call) when the matched evidence changed since the last run.
    stamp_key = f"pulse-theme:{key}"
    stamp = hashlib.sha1(",".join(sorted(ref)).encode()).hexdigest()
    row = db.get(MarketCache, stamp_key)
    d = db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key == f"theme:{key}")).first()
    if d is not None and row is not None and row.payload.get("v") == stamp:
        return {"key": d.editorial_key, "id": d.public_id, "updates": [], "unchanged": True}
    body = _theme_ai(db, theme, evs) if market_pulse._ai_available(db) else None
    body = body or _theme_rules(theme, evs)
    sources = [{"key": k, "title": e.title, "url": e.url, "publisher": e.publisher, "published_at": e.published_at, "event_id": e.id}
               for k, e in ref.items()][:MAX_SOURCES]  # fmt: skip
    d, ups = publish(
        db, f"theme:{key}", title=theme["title"], what_happened=body["what_happened"], debate=body["debate"], bull=body["bull"],
        bear=body["bear"], neutral=body["neutral"], open_questions=body["open_questions"], sources=sources, symbols=theme["symbols"],
        topics=theme["topics"], ai_assisted=body["ai"], model=body["model"],
    )  # fmt: skip
    if row is None:
        db.add(MarketCache(key=stamp_key, payload={"v": stamp}, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = {"v": stamp}, utcnow()
    db.commit()
    return {"key": d.editorial_key, "id": d.public_id, "updates": [u.kind for u in ups]}


def due_themes(db: Session, limit: int = 1) -> list[str]:
    """Themes never published first, then the one updated longest ago."""
    rows = dict(db.execute(select(PulseDiscussion.editorial_key, PulseDiscussion.content_updated_at)
                           .where(PulseDiscussion.editorial_key.like("theme:%"))).all())  # fmt: skip
    order = sorted(THEMES, key=lambda k: (f"theme:{k}" in rows, rows.get(f"theme:{k}") or datetime.min))
    return order[:limit]
