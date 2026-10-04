"""Real events that Pulse discussions and personal alerts are built on.

Providers turn verifiable information into ``SourceEvent`` rows with the facts exactly as the source gave them:

* ``newsfeed``    — articles already imported by the news pages (publisher RSS / Google News), with their links.
* ``market_data`` — large daily price moves from the existing market-data providers (delayed quotes).
* ``nexis``       — older Nexis Research editorial notes (no longer collected; kept so stored rows still label).
* ``news_search`` — per-asset headlines found by the Nexis Pulse news connector (Yahoo Finance, Google News).
* ``sec_edgar``   — company filings from SEC EDGAR, found by the Nexis Pulse filing connector.

Social sources (Reddit, X) and a licensed news API are registered with ``connected = False`` and are never
called; see ``app/services/pulse_connectors.py`` for how a connector is added once legitimate access exists.
Every event is linked to its assets in ``source_event_assets`` (:func:`link_assets`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import NexisError
from app.db.base import utcnow
from app.models import Post, SourceEvent, SourceEventAsset, TopicFollow, User, UserHolding
from app.services import markets


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    kind: str  # news | social | market | filings | editorial
    connected: bool
    note: str


PROVIDERS: dict[str, Provider] = {p.key: p for p in (
    Provider("newsfeed", "Publisher news (RSS and Google News)", "news", True, "Headlines and links from the publishers the news pages follow."),
    Provider("market_data", "Market data (Yahoo Finance, TradingView)", "market", True, "Delayed daily quotes; used for large price moves."),
    Provider("nexis", "Nexis Research", "editorial", True, "Editorial notes written by the Nexis Research team."),
    Provider("news_search", "Financial news (Yahoo Finance, Google News)", "news", True, "Headlines and links found per asset; full articles are never copied."),
    Provider("sec_edgar", "SEC EDGAR filings", "filings", True, "Free public SEC API (US-listed issuers); filing type, date and link."),
    Provider("reddit", "Reddit", "social", False, "Needs an approved Reddit API application. Not connected."),
    Provider("x", "X", "social", False, "Needs a paid X API plan with search access. Not connected."),
    Provider("news_api", "Licensed news API", "news", False, "Needs an API key for a licensed news provider. Not connected."),
)}  # fmt: skip

# ------------------------------------------------------------------ classification

_KINDS = [
    ("earnings", r"\b(earnings|results|quarter(ly)?|q[1-4]\b|profit|revenue|guidance|eps|beats?|miss(es)?)\b"),
    ("dividend", r"\b(dividends?|payout|buyback|share repurchase)\b"),
    ("rates", r"\b(fed|federal reserve|rate (cut|hike|decision)|interest rates?|central bank|ecb|bank of england|cbuae|treasur(y|ies)|bond yields?)\b"),
    ("macro", r"\b(inflation|cpi|gdp|payrolls?|jobs report|unemployment|recession|tariffs?|economy|pmi)\b"),
    ("deal", r"\b(ipo|listing|acquir\w*|acquisition|merger|takeover|buyout|stake|deal)\b"),
    ("regulation", r"\b(regulat\w*|antitrust|lawsuit|probe|investigation|fined?|sanction\w*|ban)\b"),
]  # fmt: skip
_TOPICS = [
    ("earnings", r"\b(earnings|results|quarter(ly)?|profits?|eps|guidance)\b"), ("revenue", r"\b(revenue|sales)\b"),
    ("margins", r"\bmargins?\b"), ("growth", r"\b(growth|expan\w+|record)\b"), ("dividends", r"\b(dividends?|payout|buyback)\b"),
    ("ai", r"\b(ai|artificial intelligence|chips?|gpus?|data cent(er|re)s?|semiconductor\w*)\b"),
    ("products", r"\b(launch\w*|product|iphone|model|platform)\b"), ("competition", r"\b(rival|competit\w+|market share)\b"),
    ("management", r"\b(ceo|cfo|chair\w*|executive|management)\b"), ("regulation", r"\b(regulat\w*|antitrust|lawsuit|probe|sec)\b"),
    ("macro", r"\b(inflation|gdp|economy|recession|tariffs?|jobs)\b"),
    ("rates", r"\b(fed|rates?|yields?|central bank|treasur\w+)\b"), ("risk", r"\b(risk|warn\w*|slump|plunge|selloff|sell-off)\b"),
    ("debt", r"\b(debt|bonds?|sukuk|credit|refinanc\w*)\b"), ("deals", r"\b(ipo|acqui\w+|merger|takeover|stake|deal)\b"),
    ("real_estate", r"\b(property|real estate|developer|housing|mortgage\w*|off-plan)\b"),
    ("energy", r"\b(oil|opec|brent|crude|gas|lng|energy)\b"),
]  # fmt: skip
_SECTORS = [
    ("banks", r"\b(banks?|lender|bank\w*)\b"), ("energy", r"\b(oil|gas|opec|energy|crude|lng)\b"),
    ("technology", r"\b(tech\w*|software|cloud|apple|microsoft|google|alphabet|meta|amazon)\b"),
    ("ai", r"\b(ai|nvidia|chips?|semiconductor\w*|gpus?)\b"), ("real_estate", r"\b(property|real estate|developer|housing)\b"),
    ("fintech", r"\b(payments?|fintech|visa|mastercard|paypal|stablecoin)\b"), ("healthcare", r"\b(pharma\w*|drug|health\w*|biotech)\b"),
    ("consumer", r"\b(retail\w*|consumer|shoppers?)\b"), ("utilities", r"\b(utility|utilities|electricity|water)\b"),
]  # fmt: skip
SECTOR_BY_SYMBOL = {
    "NVDA": "ai", "AMD": "ai", "AVGO": "ai", "MSFT": "technology", "AAPL": "technology", "GOOGL": "technology", "GOOG": "technology",
    "META": "technology", "AMZN": "technology", "TSLA": "technology", "NFLX": "technology", "JPM": "banks", "BAC": "banks",
    "V": "fintech", "MA": "fintech", "XOM": "energy", "CVX": "energy", "BZ=F": "energy", "CL=F": "energy",
    "EMAAR.AE": "real_estate", "ALDAR.AD": "real_estate", "EMIRATESNBD.AE": "banks", "FAB.AD": "banks", "ADCB.AD": "banks",
    "DIB.AE": "banks", "ADNOCGAS.AD": "energy", "DEWA.AE": "utilities", "SALIK.AE": "utilities",
}  # fmt: skip
MACRO_KINDS = {"rates", "macro"}


def classify_text(text: str) -> dict[str, list[str] | str]:
    low = text.lower()
    kind = next((k for k, rx in _KINDS if re.search(rx, low)), "news")
    topics = [t for t, rx in _TOPICS if re.search(rx, low)][:4]
    sectors = [s for s, rx in _SECTORS if re.search(rx, low)][:3]
    return {"kind": kind, "topics": topics, "sectors": sectors}


def sectors_for(assets: list[str], text: str) -> list[str]:
    out = [SECTOR_BY_SYMBOL[a] for a in assets if a in SECTOR_BY_SYMBOL]
    return list(dict.fromkeys(out + list(classify_text(text)["sectors"])))[:4]


def link_assets(db: Session, event: SourceEvent, symbols: list[str] | None = None) -> None:
    """Record which assets an event is about (call after the event has an id). Existing links are kept."""
    wanted = list(dict.fromkeys(symbols if symbols is not None else (event.assets or [])))
    if not wanted or event.id is None:
        return
    have = set(db.scalars(select(SourceEventAsset.symbol).where(SourceEventAsset.event_id == event.id)))
    db.add_all(SourceEventAsset(event_id=event.id, symbol=s[:32]) for s in wanted if s and s not in have)


# ------------------------------------------------------------------ providers that are connected


def _from_news(db: Session, since_hours: int = 72) -> int:
    since = utcnow() - timedelta(hours=since_hours)
    page_ids = select(User.id).where(User.kind == "page")
    q = select(Post).where(
        Post.user_id.in_(page_ids), Post.created_at >= since, Post.link_url.is_not(None), Post.external_key.is_not(None)
    )
    have = set(
        db.scalars(select(SourceEvent.external_key).where(SourceEvent.provider == "newsfeed", SourceEvent.published_at >= since))
    )
    added = 0
    fresh = [p for p in db.scalars(q.order_by(Post.created_at.desc()).limit(400)) if f"news:{p.external_key}"[:80] not in have]
    names = _names(db, {s for p in fresh for s in (p.symbols or [])})
    for p in fresh:
        key = f"news:{p.external_key}"[:80]
        headline = (p.link_title or p.body.split("\n")[0]).replace("▶ ", "").strip()
        summary_parts = [x for x in p.body.split("\n\n")[1:] if not re.fullmatch(r"([#$][\w.=^-]+\s*)+", x.strip())]
        summary = re.sub(r"\s+", " ", " ".join(summary_parts)).strip()[:900]
        c = classify_text(f"{headline} {summary}")
        # Keep only assets the article actually names (publisher "related tickers" are often loose).
        assets = [s for s in (p.symbols or []) if mentioned(s, names.get(s), f"{headline} {summary}")][:3]
        if not assets and c["kind"] in MACRO_KINDS:
            assets = [MACRO_ASSET[c["kind"]]]
        elif not assets and "energy" in c["topics"] and re.search(r"\b(oil|brent|crude|opec)\b", headline.lower()):
            assets = ["BZ=F"]
        importance = 1 + (2 if c["kind"] in ("earnings", "rates", "deal", "dividend") else 0) + (1 if assets else 0)
        ev = SourceEvent(
            kind=c["kind"], provider="newsfeed", title=headline[:500], url=p.link_url, publisher=p.link_source,
            published_at=p.created_at,
            facts={"headline": headline, "summary": summary, "post_id": p.id, "names": {s: names.get(s) for s in assets if names.get(s)}},
            assets=assets, topics=c["topics"], importance=importance, external_key=key,
        )  # fmt: skip
        db.add(ev)
        db.flush()
        link_assets(db, ev)
        have.add(key)
        added += 1
    db.commit()
    return added


# Where a rates or macro story without a company belongs on Pulse.
MACRO_ASSET = {"rates": "^TNX", "macro": "^GSPC"}
_NOISE = {"inc", "corp", "corporation", "co", "plc", "ltd", "limited", "group", "holdings", "company", "the", "pjsc", "and", "class"}


def _names(db: Session, symbols: set[str]) -> dict[str, str]:
    if not symbols:
        return {}
    out: dict[str, str] = {}
    syms = sorted(symbols)
    for i in range(0, len(syms), 40):
        try:
            out |= {q["symbol"]: q.get("name") or "" for q in markets.quotes(db, syms[i : i + 40])}
        except NexisError:
            continue
    return out


def mentioned(symbol: str, name: str | None, text: str) -> bool:
    """True when the text names the asset: its ticker as a word, or the distinctive part of its name."""
    base = re.sub(r"[.\-=^].*$", "", symbol.lstrip("^"))
    # Short tickers (V, MA) must match exactly; longer ones may be written as words ("Emaar").
    flags = re.I if len(base) >= 4 else 0
    if len(base) >= 2 and re.search(rf"(?<![\w$])\$?{re.escape(base)}(?!\w)", text, flags):
        return True
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&'-]+", name or "") if w.lower() not in _NOISE]
    return bool(words) and len(words[0]) >= 3 and re.search(rf"\b{re.escape(words[0])}\b", text, re.I) is not None


CORE_UNIVERSE = ["^GSPC", "^IXIC", "NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "JPM", "V", "MA",
                 "BTC-USD", "ETH-USD", "GC=F", "BZ=F", "EMAAR.AE", "EMIRATESNBD.AE", "FAB.AD", "ALDAR.AD", "ADNOCGAS.AD", "ADCB.AD"]  # fmt: skip


def tracked_symbols(db: Session) -> set[str]:
    held = set(db.scalars(select(UserHolding.symbol)))
    followed = set(db.scalars(select(TopicFollow.value).where(TopicFollow.kind == "symbol")))
    return held | followed


def _from_moves(db: Session) -> int:
    universe = list(dict.fromkeys(CORE_UNIVERSE + sorted(tracked_symbols(db))))[:80]
    added = 0
    today = utcnow().date().isoformat()
    for i in range(0, len(universe), 40):
        try:
            quotes = markets.quotes(db, universe[i : i + 40])
        except NexisError:
            continue
        for q in quotes:
            pct = q.get("change_pct")
            if pct is None or q.get("price") is None:
                continue
            limit = 0.06 if str(q["symbol"]).endswith("-USD") else 0.04
            if abs(pct) < limit:
                continue
            key = f"move:{q['symbol']}:{today}"
            if db.scalar(select(SourceEvent.id).where(SourceEvent.external_key == key)) is not None:
                continue
            name = q.get("name") or q["symbol"]
            direction = "rose" if pct > 0 else "fell"
            fact = (f"{name} ({q['symbol']}) {direction} {abs(pct) * 100:.1f}% in the latest session to {q['price']:,.2f}"
                    f"{' ' + q['currency'] if q.get('currency') else ''} (delayed market data).")  # fmt: skip
            ev = SourceEvent(
                kind="price_move", provider="market_data", title=f"{name} {direction} {abs(pct) * 100:.1f}%", url=None,
                publisher="Market data (delayed)", published_at=utcnow(),
                facts={"headline": fact, "price": q["price"], "change_pct": round(pct * 100, 2), "currency": q.get("currency"),
                       "market_time": q.get("market_time")},
                assets=[q["symbol"]], topics=["risk"] if pct < 0 else ["growth"], importance=2 + (1 if abs(pct) >= 0.08 else 0),
                external_key=key,
            )  # fmt: skip
            db.add(ev)
            db.flush()
            link_assets(db, ev)
            added += 1
    db.commit()
    return added


def ingest(db: Session) -> dict[str, int]:
    """Collect new events from every connected provider. Cheap to call repeatedly (deduplicated)."""
    out: dict[str, int] = {}
    # Nexis editorial discussions are not evidence; they notify followers directly (``alerts.notify_new_discussion``).
    for key, fn in (("newsfeed", _from_news), ("market_data", _from_moves)):
        try:
            out[key] = fn(db)
        except Exception as exc:  # one provider failing must not stop the others
            db.rollback()
            out[key] = 0
            out[f"{key}_error"] = str(exc)[:200]  # type: ignore[assignment]
    return out


def serialize(e: SourceEvent) -> dict[str, Any]:
    return {
        "id": e.id, "kind": e.kind, "provider": e.provider, "provider_label": PROVIDERS[e.provider].label if e.provider in PROVIDERS else e.provider,
        "title": e.title, "url": e.url, "publisher": e.publisher, "published_at": e.published_at.isoformat() + "Z",
        "summary": (e.facts or {}).get("summary") or None, "facts": (e.facts or {}).get("headline"),
        "assets": e.assets, "topics": e.topics, "importance": e.importance,
    }  # fmt: skip


def recent(
    db: Session, symbols: list[str] | None = None, days: int = 7, limit: int = 20, min_importance: int = 1
) -> list[SourceEvent]:
    q = select(SourceEvent).where(
        SourceEvent.published_at >= utcnow() - timedelta(days=days), SourceEvent.importance >= min_importance
    )
    rows = list(db.scalars(q.order_by(SourceEvent.published_at.desc()).limit(600)))
    if symbols is not None:
        wanted = set(symbols)
        rows = [e for e in rows if wanted & set(e.assets or [])]
    return rows[:limit]


def important(db: Session, limit: int = 8) -> list[SourceEvent]:
    """Market events worth surfacing: high importance first, then newest."""
    q = select(SourceEvent).where(
        SourceEvent.published_at >= utcnow() - timedelta(days=3),
        or_(SourceEvent.importance >= 3, SourceEvent.kind.in_(["rates", "macro", "earnings", "price_move"])),
    )
    rows = list(db.scalars(q.order_by(SourceEvent.importance.desc(), SourceEvent.published_at.desc()).limit(limit)))
    return rows
