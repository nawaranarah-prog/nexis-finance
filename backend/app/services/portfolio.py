"""Personal portfolio, watchlist and investment intelligence.

* Holdings are private: every function takes the signed-in member and only ever reads that member's rows.
* Values use live (delayed) quotes from the existing market-data providers. When a quote is missing the holding
  is shown at cost with "price unavailable" — no value is ever estimated. A combined total is converted to USD
  only with real FX quotes; holdings that can't be converted are listed as excluded.
* The watchlist is the existing "follow a ticker" feature (``TopicFollow`` with kind ``symbol``).
* Intelligence groups real ``SourceEvent`` developments and Pulse discussions by tracked asset. The AI brief is
  generated on request from those facts only and never gives buy/sell/hold advice.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NexisError, NotFoundError, ProviderError, ValidationFailed
from app.db.base import utcnow
from app.models import TopicFollow, User, UserHolding
from app.services import events, llm, markets, personas, pulse, social

ASSET_TYPES = ("stock", "etf", "fund", "bond", "crypto", "other")
MAX_HOLDINGS = 200


def _guess_type(symbol: str, quote: dict[str, Any] | None) -> str:
    qt = (quote or {}).get("type") or ""
    if qt in ("etf",):
        return "etf"
    if qt in ("mutualfund", "fund"):
        return "fund"
    c = personas.classify(symbol)["class"]
    return {"bond": "bond", "crypto": "crypto"}.get(c, "stock" if qt in ("equity", "") else "other")


def _holding(h: UserHolding) -> dict[str, Any]:
    return {
        "id": h.id, "portfolio": h.portfolio, "symbol": h.symbol, "name": h.name, "asset_type": h.asset_type, "quantity": h.quantity,
        "purchase_price": h.purchase_price, "purchase_date": h.purchase_date.isoformat() if h.purchase_date else None,
        "currency": h.currency, "note": h.note,
    }  # fmt: skip


def _mine(db: Session, user: User, hid: int) -> UserHolding:
    h = db.get(UserHolding, hid)
    if h is None or h.user_id != user.id:  # someone else's holding looks exactly like a missing one
        raise NotFoundError("holding not found")
    return h


def add_holding(db: Session, user: User, data: dict[str, Any]) -> dict[str, Any]:
    if db.query(UserHolding).filter(UserHolding.user_id == user.id).count() >= MAX_HOLDINGS:
        raise ValidationFailed(f"a portfolio can hold up to {MAX_HOLDINGS} positions")
    sym = markets.clean_symbol(data["symbol"])
    try:
        quote = (markets.quotes(db, [sym]) or [None])[0]
    except NexisError:
        quote = None
    h = UserHolding(user_id=user.id, symbol=sym)
    _apply(h, data, quote)
    db.add(h)
    db.commit()
    return _holding(h)


def _apply(h: UserHolding, data: dict[str, Any], quote: dict[str, Any] | None) -> None:
    qty = data.get("quantity", h.quantity)
    if qty is None or not (0 < float(qty) < 1e12):
        raise ValidationFailed("quantity must be a positive number")
    price = data.get("purchase_price", h.purchase_price)
    if price is not None and not (0 <= float(price) < 1e12):
        raise ValidationFailed("purchase price must be zero or more")
    when = data.get("purchase_date", h.purchase_date)
    if isinstance(when, str):
        try:
            when = date.fromisoformat(when)
        except ValueError as exc:
            raise ValidationFailed("purchase date must be YYYY-MM-DD") from exc
    if when and when > utcnow().date():
        raise ValidationFailed("purchase date can't be in the future")
    kind = data.get("asset_type") or h.asset_type or _guess_type(h.symbol, quote)
    if kind not in ASSET_TYPES:
        raise ValidationFailed("unknown asset type")
    name = re.sub(r"[\x00-\x1f]", "", str(data.get("portfolio") or h.portfolio or "Main")).strip()[:60] or "Main"
    note = data.get("note", h.note)
    h.quantity, h.purchase_price, h.purchase_date, h.asset_type, h.portfolio = (
        float(qty),
        None if price is None else float(price),
        when,
        kind,
        name,
    )
    h.note = (str(note).strip()[:300] or None) if note is not None else None
    if quote:
        h.name = h.name or (quote.get("name") or "")[:160] or None
        h.currency = h.currency or quote.get("currency")


def update_holding(db: Session, user: User, hid: int, data: dict[str, Any]) -> dict[str, Any]:
    h = _mine(db, user, hid)
    _apply(h, data, None)
    db.commit()
    return _holding(h)


def delete_holding(db: Session, user: User, hid: int) -> None:
    db.delete(_mine(db, user, hid))
    db.commit()


def _usd_rates(db: Session, currencies: set[str]) -> dict[str, float]:
    """USD per unit of each currency, from real FX quotes. Missing currencies are simply absent."""
    out = {"USD": 1.0}
    need = [c for c in currencies if c and c != "USD"]
    if not need:
        return out
    try:
        quotes = {q["symbol"]: q for q in markets.quotes(db, [f"{c}USD=X" for c in need])}
    except NexisError:
        quotes = {}
    for c in need:
        q = quotes.get(f"{c}USD=X")
        if q and q.get("price"):
            out[c] = float(q["price"])
    return out


def summary(db: Session, user: User) -> dict[str, Any]:
    rows = list(
        db.scalars(select(UserHolding).where(UserHolding.user_id == user.id).order_by(UserHolding.portfolio, UserHolding.symbol))
    )
    syms = list(dict.fromkeys(h.symbol for h in rows))
    try:
        quotes = {q["symbol"]: q for q in markets.quotes(db, syms)} if syms else {}
    except NexisError:
        quotes = {}
    rates = _usd_rates(db, {(quotes.get(h.symbol) or {}).get("currency") or h.currency or "" for h in rows})
    out, total_usd, cost_usd, basis_usd, day_usd, excluded = [], 0.0, 0.0, 0.0, 0.0, []
    for h in rows:
        q = quotes.get(h.symbol) or {}
        price, ccy = q.get("price"), q.get("currency") or h.currency
        value = price * h.quantity if price is not None else None
        cost = h.purchase_price * h.quantity if h.purchase_price is not None else None
        gain = value - cost if value is not None and cost is not None else None
        day = (q.get("change") or 0) * h.quantity if price is not None and q.get("change") is not None else None
        rate = rates.get(ccy or "")
        if value is not None and rate:
            total_usd += value * rate
            day_usd += (day or 0) * rate
            if cost is not None:
                cost_usd += cost * rate
                basis_usd += value * rate  # gain only compares holdings that have a purchase price
        elif value is None or not rate:
            excluded.append(h.symbol)
        out.append({
            **_holding(h), "currency": ccy, "price": price, "change_pct": q.get("change_pct"), "market_time": q.get("market_time"),
            "value": value, "cost": cost, "gain": gain, "gain_pct": (gain / cost) if gain is not None and cost else None,
            "day_change": day, "value_usd": value * rate if value is not None and rate else None,
            "price_status": "live" if price is not None else "unavailable",
        })  # fmt: skip
    for r in out:
        r["weight"] = (r["value_usd"] / total_usd) if r["value_usd"] and total_usd else None
    alloc: dict[str, float] = {}
    for r in out:
        if r["value_usd"]:
            alloc[r["asset_type"]] = alloc.get(r["asset_type"], 0) + r["value_usd"]
    return {
        "holdings": out,
        "portfolios": sorted({h.portfolio for h in rows}),
        "totals": {
            "value_usd": total_usd if total_usd else None,
            "cost_usd": cost_usd or None,
            "gain_usd": (basis_usd - cost_usd) if cost_usd else None,
            "gain_pct": (basis_usd - cost_usd) / cost_usd if cost_usd else None,
            "day_change_usd": day_usd if total_usd else None,
            "excluded": excluded,
        },
        "allocation": [
            {"asset_type": k, "value_usd": v, "weight": v / total_usd} for k, v in sorted(alloc.items(), key=lambda kv: -kv[1])
        ],
        "note": "Prices are delayed quotes from public market data. Totals are converted to USD with live FX quotes.",
    }


# ------------------------------------------------------------------ watchlist (existing ticker follows)


def watchlist(db: Session, user: User) -> dict[str, Any]:
    syms = list(
        db.scalars(
            select(TopicFollow.value).where(TopicFollow.user_id == user.id, TopicFollow.kind == "symbol").order_by(TopicFollow.id)
        )
    )
    try:
        quotes = {q["symbol"]: q for q in markets.quotes(db, syms)} if syms else {}
    except NexisError:
        quotes = {}
    return {
        "items": [
            {"symbol": s, **{k: (quotes.get(s) or {}).get(k) for k in ("name", "price", "change_pct", "currency", "market_time")}}
            for s in syms
        ]
    }


def watch(db: Session, user: User, symbol: str, on: bool) -> dict[str, Any]:
    sym = markets.clean_symbol(symbol)
    exists = db.scalars(
        select(TopicFollow).where(TopicFollow.user_id == user.id, TopicFollow.kind == "symbol", TopicFollow.value == sym)
    ).first()
    if bool(exists) != on:
        social.toggle_topic(db, user, "symbol", sym)
    return {"symbol": sym, "watching": on}


def tracked(db: Session, user: User) -> dict[str, str]:
    """symbol → "holding" or "watchlist" (holding wins)."""
    out = {
        s: "watchlist"
        for s in db.scalars(select(TopicFollow.value).where(TopicFollow.user_id == user.id, TopicFollow.kind == "symbol"))
    }
    out.update({s: "holding" for s in db.scalars(select(UserHolding.symbol).where(UserHolding.user_id == user.id))})
    return out


def status_for(db: Session, user: User | None, symbol: str) -> dict[str, Any]:
    if user is None:
        return {"signed_in": False, "holding": False, "watching": False}
    t = tracked(db, user).get(markets.clean_symbol(symbol))
    sym = markets.clean_symbol(symbol)
    watching = (
        db.scalars(
            select(TopicFollow.id).where(TopicFollow.user_id == user.id, TopicFollow.kind == "symbol", TopicFollow.value == sym)
        ).first()
        is not None
    )
    return {"signed_in": True, "holding": t == "holding", "watching": watching}


# ------------------------------------------------------------------ intelligence


def intelligence(db: Session, user: User, days: int = 7) -> dict[str, Any]:
    tr = tracked(db, user)
    if not tr:
        return {"tracked": 0, "developments": 0, "assets": []}
    evs = events.recent(db, list(tr), days=days, limit=200)
    per: dict[str, list[dict[str, Any]]] = {}
    for e in evs:
        for a in e.assets or []:
            if a in tr:
                per.setdefault(a, []).append(events.serialize(e))
    assets = []
    for sym in sorted(tr, key=lambda s: (-len(per.get(s, [])), s)):
        disc = pulse.listing(db, user, symbol=sym, limit=2)["items"]
        assets.append({"symbol": sym, "relation": tr[sym], "developments": per.get(sym, [])[:6], "discussions": disc})
    important = sum(
        1 for items in per.values() for e in items if e["importance"] >= 3 or e["kind"] in ("earnings", "dividend", "price_move")
    )
    return {
        "tracked": len(tr),
        "developments": sum(len(v) for v in per.values()),
        "important": important,
        "assets": assets,
        "days": days,
    }


def asset_intelligence(db: Session, user: User | None, symbol: str) -> dict[str, Any]:
    sym = markets.clean_symbol(symbol)
    evs = [events.serialize(e) for e in events.recent(db, [sym], days=30, limit=15)]
    upcoming: list[dict[str, Any]] = []
    try:
        d = markets.details(db, sym)
        if d.get("next_earnings"):
            upcoming.append({"kind": "earnings", "date": str(d["next_earnings"]), "source": "Market data (company calendar)"})
        div = d.get("dividends") or {}
        if isinstance(div, dict) and div.get("ex_date"):
            upcoming.append({"kind": "ex-dividend", "date": str(div["ex_date"]), "source": "Market data"})
        if (d.get("bond") or {}).get("maturity"):
            upcoming.append({"kind": "maturity", "date": str(d["bond"]["maturity"]), "source": "Bond terms"})
    except NexisError:
        pass
    position = None
    if user is not None:
        rows = [h for h in db.scalars(select(UserHolding).where(UserHolding.user_id == user.id, UserHolding.symbol == sym))]
        if rows:
            position = {"quantity": sum(h.quantity for h in rows), "lots": [_holding(h) for h in rows]}
    return {
        "symbol": sym, "events": evs, "upcoming": upcoming, "position": position,
        "discussions": pulse.listing(db, user, symbol=sym, sort="top", limit=4)["items"],
        "status": status_for(db, user, sym),
    }  # fmt: skip


BRIEF = """You prepare a short, neutral intelligence note about {symbol} for someone who tracks it.
Use ONLY the FACTS and DISCUSSIONS below. Do not add prices, figures, dates or events that are not there.
Never recommend buying, selling or holding, and don't address the reader's decision. Discussions are opinions
(from members, Nexis Research, or Nexis-generated perspectives as labelled) — present them as views, not facts.

FACTS (verified developments with sources)
{facts}

DISCUSSIONS ON NEXIS PULSE
{discussions}

Return JSON: {{"what_happened": "2-3 sentences", "why_it_may_matter": "2-3 sentences, conditional language",
"perspectives": [{{"view": "one sentence", "from": "who holds it, as labelled"}}],
"uncertainty": "1-2 sentences on what is unknown or could change the picture",
"cited_events": [event numbers used]}}"""


def brief(db: Session, user: User | None, symbol: str) -> dict[str, Any]:
    data = asset_intelligence(db, user, symbol)
    evs = data["events"][:8]
    if not evs:
        return {"available": False, "reason": "No recent developments with a source to brief on."}
    facts = "\n".join(f"[{i}] {e['published_at'][:10]} · {e['publisher'] or e['provider_label']}: {e['title']}"
                      + (f" — {e['summary'][:400]}" if e.get("summary") else "") for i, e in enumerate(evs, 1))  # fmt: skip
    disc = (
        "\n".join(
            f"- ({d['source']['label']}) {d['title']}" + (f" · {d['sentiment']}" if d.get("sentiment") else "")
            for d in data["discussions"]
        )
        or "None yet."
    )
    key = (
        "intel-brief:"
        + hashlib.sha1(json.dumps([symbol, [e["id"] for e in evs], [d["id"] for d in data["discussions"]]]).encode()).hexdigest()[
            :20
        ]
    )

    def fetch() -> dict[str, Any]:
        try:
            msg = llm.chat([{"role": "system", "content": "You write careful, neutral financial context. Output valid JSON only."},
                            {"role": "user", "content": BRIEF.format(symbol=data["symbol"], facts=facts, discussions=disc)}],
                           max_tokens=900, temperature=0.2)  # fmt: skip
        except llm.LLMUnavailable as exc:
            raise ProviderError(f"the AI model is unavailable ({exc.reason})") from exc
        m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            out = None
        if not isinstance(out, dict):
            raise ProviderError("the AI model returned an unreadable brief")
        cited = [evs[int(i) - 1] for i in out.get("cited_events") or [] if isinstance(i, int | float) and 1 <= int(i) <= len(evs)]
        return {
            "what_happened": str(out.get("what_happened", ""))[:900], "why_it_may_matter": str(out.get("why_it_may_matter", ""))[:900],
            "perspectives": [{"view": str(p.get("view", ""))[:300], "from": str(p.get("from", ""))[:80]} for p in (out.get("perspectives") or [])[:4] if isinstance(p, dict)],
            "uncertainty": str(out.get("uncertainty", ""))[:600],
            "sources": [{"title": e["title"], "url": e["url"], "publisher": e["publisher"], "published_at": e["published_at"]} for e in (cited or evs[:3])],
            "model": msg.get("_model"),
        }  # fmt: skip

    try:
        value, meta = markets.cached(db, key, timedelta(hours=6), fetch)
    except ProviderError as exc:
        return {"available": False, "reason": exc.message}
    return {"available": True, **value, "generated_at": meta["fetched_at"],
            "disclaimer": "AI-generated context from the sources listed. Not financial advice and not a recommendation."}  # fmt: skip
