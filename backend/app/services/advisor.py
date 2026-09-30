"""AI financial advisor grounded in live market data and news.

With a language model configured, the model answers through tools that fetch live quotes,
fundamentals, analyst consensus, news, price statistics, comparisons and valuations; it is told
never to state a number it did not get from a tool. Without a model (or when the model is
unavailable) the same tools produce a structured data briefing, clearly labelled as such.

This is educational analysis, not regulated personal advice.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, NexisError, ProviderError
from app.db.base import utcnow
from app.models import MarketCache
from app.services import compare as cmp
from app.services import llm, markets, valuation

DISCLAIMER = (
    "Educational analysis from public data — not personalised investment advice, and Nexis is not a licensed adviser. "
    "Markets can fall; consider your goals, horizon and risk tolerance, and consult a licensed professional before investing."
)

SYSTEM = """You are Nexis Advisor, the research assistant of the Nexis Finance platform. You explain markets the way a careful,
experienced (CFA-charterholder style) financial adviser would: balanced, specific, plain-spoken, never hype.

Rules:
- Ground every number and every factual claim in tool results from this conversation. Call tools first; never guess prices,
  ratios, dates or news. If a tool fails or data is missing, say so.
- For a named company, resolve the ticker with search_instruments (UAE Dubai listings end in .AE; Abu Dhabi/ADX listings are not
  available in this data source — say so if asked), then call get_instrument and get_news, and get_price_stats for recent behaviour.
- When the user mentions a quantity of shares or an amount of money, call position_calculator and show the cost in the
  instrument's currency (and USD/AED where helpful), what that position is worth at the 52-week low/high and the analyst target,
  and how it compares with typical daily volume.
- Structure answers with short markdown headings: **Snapshot**, **What's happening** (news, cite publisher and date, link the
  headline), **Fundamentals & valuation**, **What analysts say**, **Risks**, **If you buy …** (when relevant), **Bottom line**.
- The bottom line weighs both sides and says under which circumstances the idea is reasonable or not (horizon, diversification,
  concentration, valuation, momentum). Do not promise returns. You may say what the data suggests; never tell the user to put
  all their money in one position. End with one or two questions about their goals/horizon/risk tolerance if unknown.
- Answer in the user's language (e.g. Arabic if they write in Arabic). Be concise: aim for 250–450 words.
- Finish with one italic line: *Educational analysis, not personalised advice.*"""

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "search_instruments", "description": "Find tickers for a company, fund, index, bond ETF, currency or crypto name.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "get_instrument", "description": "Live quote, valuation ratios, financials, dividends, analyst consensus and company profile.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "get_news", "description": "Recent news headlines (publisher, date, link) about an instrument.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "get_price_stats", "description": "Return, volatility, drawdown and range over a period (1mo, 3mo, 6mo, ytd, 1y, 3y, 5y).",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "period": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "compare_instruments", "description": "Compare 2-6 instruments over a period: performance, risk, correlation, valuation and a rule-based scorecard.",
     "parameters": {"type": "object", "properties": {"symbols": {"type": "array", "items": {"type": "string"}}, "period": {"type": "string"}}, "required": ["symbols"]}}},
    {"type": "function", "function": {"name": "run_valuation", "description": "DCF and trading-comparables valuation with market-implied growth for an operating company.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "position_calculator", "description": "Cost and scenario values of buying a number of shares or a money amount of an instrument.",
     "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "shares": {"type": "number"}, "amount": {"type": "number"},
                                                     "amount_currency": {"type": "string"}}, "required": ["symbol"]}}},
]  # fmt: skip


# ---------------------------------------------------------------------- tools


def _compact_details(d: dict[str, Any]) -> dict[str, Any]:
    prof = dict(d["profile"])
    if prof.get("summary"):
        prof["summary"] = prof["summary"][:600]
    return {k: d[k] for k in ("symbol", "name", "type", "exchange", "currency", "quote", "valuation", "dividends", "financials", "analysts", "next_earnings")} | {
        "profile": prof, "as_of": d["cache"]["fetched_at"]}  # fmt: skip


def position(
    db: Session, symbol: str, shares: float | None = None, amount: float | None = None, amount_currency: str | None = None
) -> dict[str, Any]:
    d = markets.details(db, symbol)
    px, ccy = d["quote"]["price"], d["currency"]
    if not px:
        raise ProviderError(f"no current price for {d['symbol']}")
    if shares is None and amount is None:
        raise ConfigurationError("give a number of shares or an amount")
    fx = (
        valuation._fx(db, amount_currency, ccy)
        if amount is not None and amount_currency and amount_currency.upper() != ccy
        else 1.0
    )
    n = float(shares) if shares is not None else int((amount * fx) // px)
    cost = n * px
    q, an = d["quote"], d["analysts"]

    def at(p: float | None) -> dict[str, Any] | None:
        return {"price": p, "value": n * p, "change": n * p - cost, "change_pct": p / px - 1} if p else None

    usd = cost / 3.6725 if ccy == "AED" else cost if ccy == "USD" else None
    return {
        "symbol": d["symbol"], "name": d["name"], "currency": ccy, "price": px, "shares": n, "cost": cost,
        "cost_usd": usd, "cost_aed": cost * 3.6725 if ccy == "USD" else cost if ccy == "AED" else None,
        "leftover_cash": (amount * fx - cost) if amount is not None else None,
        "share_of_avg_daily_volume": n / q["avg_volume"] if q.get("avg_volume") else None,
        "at_52w_low": at(q.get("week52_low")), "at_52w_high": at(q.get("week52_high")),
        "at_analyst_mean_target": at(an.get("target_mean")), "at_analyst_low_target": at(an.get("target_low")),
        "annual_dividends": n * d["dividends"]["rate"] if d["dividends"].get("rate") else None,
        "note": "Excludes broker commission, exchange fees and VAT; check your broker's schedule.",
    }  # fmt: skip


def price_stats(db: Session, symbol: str, period: str = "1y") -> dict[str, Any]:
    r = cmp.compare(db, [symbol], period=period)
    m = r["metrics"][r["symbols"][0]]
    return {"symbol": r["symbols"][0], "period": period, "interval": r["interval"], **{k: m[k] for k in (
        "start", "end", "start_price", "end_price", "total_return", "annualized_return", "annualized_volatility",
        "sharpe_ratio", "max_drawdown", "max_drawdown_peak", "max_drawdown_trough", "high", "low")},
        "recent_periods": r["periodic_returns"][-6:]}  # fmt: skip


def _run_tool(db: Session, name: str, args: dict[str, Any]) -> Any:
    if name == "search_instruments":
        return markets.search(db, str(args.get("query", "")))[:8]
    if name == "get_instrument":
        return _compact_details(markets.details(db, args["symbol"]))
    if name == "get_news":
        n = markets.news(db, args["symbol"])
        return {"items": n["items"][:10], "feeds": n["feeds"]}
    if name == "get_price_stats":
        return price_stats(db, args["symbol"], args.get("period") or "1y")
    if name == "compare_instruments":
        r = cmp.compare(db, args["symbols"][:6], period=args.get("period") or "1y")
        return {k: r[k] for k in ("symbols", "window", "metrics", "correlation", "snapshots", "currency_note")} | {
            "scorecard": {k: r["scorecard"].get(k) for k in ("ranking", "composite", "reasons", "weaknesses", "method")}}  # fmt: skip
    if name == "run_valuation":
        v = valuation.run(db, args["symbol"])
        return {k: v[k] for k in ("symbol", "currency", "price", "blended_fair_value", "upside", "view", "warnings", "market_implied_growth", "method")} | {
            "dcf_value_per_share": v["dcf"]["value_per_share"], "wacc": v["wacc"]["used"], "assumptions": v["assumptions"],
            "comparables": v["comparables"]["implied"], "football_field": v["football_field"]}  # fmt: skip
    if name == "position_calculator":
        return position(db, args["symbol"], args.get("shares"), args.get("amount"), args.get("amount_currency"))
    raise ConfigurationError(f"unknown tool {name}")


# ---------------------------------------------------------------------- entry point


def ask(db: Session, messages: list[dict[str, str]]) -> dict[str, Any]:
    if not messages or messages[-1]["role"] != "user":
        raise ConfigurationError("the last message must be from the user")
    trace: list[dict[str, Any]] = []
    try:
        out = _ask_llm(db, messages, trace)
        _record_llm_state(db, None)
        return out
    except llm.LLMUnavailable as exc:
        _record_llm_state(db, exc.reason)
        out = briefing(db, messages[-1]["content"])
        out["ai"] = {"used": False, "reason": exc.reason}
        return out


_LLM_STATE = "llm:state"


def _record_llm_state(db: Session, reason: str | None) -> None:
    """Remember whether the last model call worked, so the status reflects reality rather than configuration."""
    payload = {"v": {"ok": reason is None, "reason": reason}}
    row = db.get(MarketCache, _LLM_STATE)
    if row is None:
        db.add(MarketCache(key=_LLM_STATE, payload=payload, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = payload, utcnow()
    db.commit()


def status(db: Session) -> dict[str, Any]:
    st = llm.status()
    if not st["configured"]:
        return {**st, "available": False, "last_error": st["reason"]}
    row = db.get(MarketCache, _LLM_STATE)
    if row is None or utcnow() - row.fetched_at > timedelta(minutes=30):
        try:  # one-token probe; free when the provider rejects the request
            llm.chat([{"role": "user", "content": "Reply with: ok"}], max_tokens=1)
            _record_llm_state(db, None)
        except llm.LLMUnavailable as exc:
            _record_llm_state(db, exc.reason)
        row = db.get(MarketCache, _LLM_STATE)
    state = row.payload["v"]
    return {**st, "available": bool(state["ok"]), "last_error": state["reason"]}


def _ask_llm(db: Session, messages: list[dict[str, str]], trace: list[dict[str, Any]]) -> dict[str, Any]:
    today = datetime.now(UTC).strftime("%A %d %B %Y, %H:%M UTC")
    convo: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM + f"\n\nCurrent date and time: {today}."}]
    convo += [{"role": m["role"], "content": m["content"][:4000]} for m in messages[-12:]]
    news_used: list[dict[str, Any]] = []
    symbols: list[str] = []
    for _ in range(7):
        msg = llm.chat(convo, TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            text = (msg.get("content") or "").strip()
            if not text:
                raise llm.LLMUnavailable("The language model returned an empty answer.")
            return {"answer": text, "mode": "ai", "ai": {"used": True, **llm.status()}, "tools": trace,
                    "symbols": list(dict.fromkeys(symbols)), "news": news_used[:8], "disclaimer": DISCLAIMER}  # fmt: skip
        convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name, args = c["function"]["name"], {}
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
                result = _run_tool(db, name, args)
                ok = True
            except (NexisError, KeyError, TypeError, ValueError) as exc:
                result, ok = {"error": getattr(exc, "message", str(exc))}, False
            trace.append({"tool": name, "args": args, "ok": ok})
            if ok and name == "get_news":
                news_used += result["items"][:4]
            if ok and isinstance(result, dict) and result.get("symbol"):
                symbols.append(result["symbol"])
            convo.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=str)[:12000]})
    raise llm.LLMUnavailable("The language model did not finish within the tool-call budget.")


# ---------------------------------------------------------------------- data-only briefing

_STOP = set(
    [
        "what",
        "whats",
        "what's",
        "is",
        "are",
        "the",
        "a",
        "an",
        "of",
        "on",
        "in",
        "for",
        "to",
        "with",
        "about",
        "my",
        "i",
        "me",
        "we",
        "want",
        "wanna",
        "would",
        "like",
        "buy",
        "sell",
        "shares",
        "share",
        "stock",
        "stocks",
        "should",
        "think",
        "do",
        "does",
        "how",
        "much",
        "many",
        "going",
        "up",
        "down",
        "happening",
        "news",
        "price",
        "latest",
        "today",
        "now",
        "tell",
        "give",
        "please",
        "any",
        "some",
        "this",
        "that",
        "hey",
        "hi",
        "hello",
        "up",
        "w",
        "u",
        "ur",
        "you",
        "your",
        "it",
        "its",
        "and",
        "or",
        "vs",
        "versus",
        "compare",
        "invest",
        "investing",
        "investment",
        "into",
        "worth",
        "good",
        "bad",
        "time",
    ]
)
_QTY = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|m|million)?\s*(shares?|stocks?|units?)\b", re.I)
_AMT = re.compile(
    r"(?:(aed|dhs?|usd|\$|eur|€|gbp|£)\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m)?|(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\s*(aed|dirhams?|dhs?|usd|dollars?|eur|euros?|gbp|pounds?))",
    re.I,
)
_CCY = {"aed": "AED", "dh": "AED", "dhs": "AED", "dirham": "AED", "dirhams": "AED", "usd": "USD", "$": "USD", "dollar": "USD", "dollars": "USD",
        "eur": "EUR", "€": "EUR", "euro": "EUR", "euros": "EUR", "gbp": "GBP", "£": "GBP", "pound": "GBP", "pounds": "GBP"}  # fmt: skip


def _mult(x: str | None) -> float:
    return {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((x or "").lower(), 1.0)


def parse_request(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"shares": None, "amount": None, "amount_currency": None}
    if m := _QTY.search(text):
        out["shares"] = float(m.group(1).replace(",", "")) * _mult(m.group(2))
    elif m := _AMT.search(text):
        if m.group(2):
            out["amount"], out["amount_currency"] = (
                float(m.group(2).replace(",", "")) * _mult(m.group(3)),
                _CCY[m.group(1).lower()],
            )
        else:
            out["amount"], out["amount_currency"] = (
                float(m.group(4).replace(",", "")) * _mult(m.group(5)),
                _CCY[m.group(6).lower()],
            )
    return out


def resolve_symbol(db: Session, text: str) -> dict[str, Any] | None:
    for tok in re.findall(r"\$?\b[A-Z][A-Z0-9]{0,9}(?:[.\-][A-Z]{1,3})?\b|\b[A-Za-z0-9]+\.(?:AE|ae)\b", text):
        t = tok.lstrip("$").upper()
        if len(t) < 2 or t.lower() in _STOP:
            continue
        hits = [h for h in markets.search(db, t) if h["symbol"].upper() == t]
        if hits:
            return hits[0]
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z.&'-]+", text) if w.lower() not in _STOP]
    for n in range(min(3, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            q = " ".join(words[i : i + n])
            hits = [
                h
                for h in markets.search(db, q)
                if h["type"] in ("equity", "etf", "fund", "index", "cryptocurrency", "currency", "futures")
            ]
            if hits:
                return hits[0]
    return None


def _fmt(v: float | None, ccy: str | None = None, pct: bool = False, digits: int = 2) -> str:
    if v is None:
        return "n/a"
    if pct:
        return f"{v * 100:+.{digits - 1}f}%"
    s = f"{v:,.{digits}f}"
    return f"{s} {ccy}" if ccy else s


def briefing(db: Session, question: str) -> dict[str, Any]:
    inst = resolve_symbol(db, question)
    if inst is None:
        return {"answer": "I couldn't identify an instrument in your question. Mention a company, fund or ticker — for example "
                "“What's happening with Emaar? I want to buy 500 shares.”", "mode": "data", "tools": [], "symbols": [], "news": [],
                "disclaimer": DISCLAIMER}  # fmt: skip
    sym = inst["symbol"]
    d = markets.details(db, sym)
    ccy, q, v, an, dv, f = d["currency"], d["quote"], d["valuation"], d["analysts"], d["dividends"], d["financials"]
    lines = [f"### {d['name']} ({sym}) · {d['exchange']}"]
    lines.append(
        f"**Snapshot** — {_fmt(q['price'], ccy)} ({_fmt(q.get('change_pct'), pct=True)} today). 52-week range "
        f"{_fmt(q.get('week52_low'))}–{_fmt(q.get('week52_high'))} {ccy}; market cap {_fmt((v.get('market_cap') or 0) / 1e9, digits=1)}bn {ccy}."
    )
    try:
        st = price_stats(db, sym, "1y")
        lines.append(
            f"Over the past year it returned {_fmt(st['total_return'], pct=True)} with {_fmt(st['annualized_volatility'], pct=True).lstrip('+')} "
            f"annualised volatility and a maximum drawdown of {_fmt(st['max_drawdown'], pct=True)}."
        )
    except NexisError:
        st = None
    try:
        news_items = markets.news(db, sym)["items"][:6]
    except NexisError:
        news_items = []
    if news_items:
        lines.append("**What's happening** — latest headlines:")
        for n in news_items:
            when = (n.get("published_at") or "")[:10]
            lines.append(f"- [{n['title']}]({n['url']}) — {n.get('publisher') or n['source']}, {when}")
    val = [
        f"P/E {v['pe_trailing']:.1f}x" if v.get("pe_trailing") else None,
        f"forward P/E {v['pe_forward']:.1f}x" if v.get("pe_forward") else None,
        f"P/B {v['price_to_book']:.2f}x" if v.get("price_to_book") else None,
        f"EV/EBITDA {v['ev_to_ebitda']:.1f}x" if v.get("ev_to_ebitda") else None,
        f"dividend yield {dv['yield'] * 100:.1f}%" if dv.get("yield") else None,
        f"revenue growth {f['revenue_growth'] * 100:+.1f}%" if f.get("revenue_growth") is not None else None,
        f"profit margin {f['profit_margin'] * 100:.1f}%" if f.get("profit_margin") is not None else None,
    ]
    if any(val):
        lines.append("**Fundamentals & valuation** — " + ", ".join(x for x in val if x) + ".")
    if an.get("count"):
        lines.append(
            f"**What analysts say** — {int(an['count'])} analysts, consensus **{(an.get('recommendation') or 'n/a').replace('_', ' ')}** "
            f"(mean {an['recommendation_mean']:.2f} on a 1 = strong buy … 5 = sell scale); mean target {_fmt(an.get('target_mean'), ccy)} "
            f"({_fmt(an.get('upside_to_mean_target'), pct=True)} vs the current price), range {_fmt(an.get('target_low'))}–{_fmt(an.get('target_high'))}."
        )
    req = parse_request(question)
    pos = None
    if req["shares"] or req["amount"]:
        try:
            pos = position(db, sym, req["shares"], req["amount"], req["amount_currency"])
        except NexisError:
            pos = None
    if pos:
        extra = f" (≈ {_fmt(pos['cost_usd'], 'USD')})" if ccy == "AED" and pos.get("cost_usd") else ""
        lines.append(f"**If you buy {pos['shares']:,.0f} shares** — cost {_fmt(pos['cost'], ccy)}{extra} before fees.")
        for label, k in (
            ("at the 52-week low", "at_52w_low"),
            ("at the 52-week high", "at_52w_high"),
            ("at the mean analyst target", "at_analyst_mean_target"),
        ):
            if pos.get(k):
                lines.append(f"- {label}: {_fmt(pos[k]['value'], ccy)} ({_fmt(pos[k]['change_pct'], pct=True)})")
        if pos.get("annual_dividends"):
            lines.append(f"- dividends at the current rate: about {_fmt(pos['annual_dividends'], ccy)} a year")
    risks = []
    if st and st["max_drawdown"] is not None and st["max_drawdown"] < -0.25:
        risks.append(f"it fell {abs(st['max_drawdown']):.0%} peak-to-trough within the last year")
    if v.get("beta") and v["beta"] > 1.2:
        risks.append(f"it moves more than the market (beta {v['beta']:.2f})")
    if q.get("price") and q.get("ma200") and q["price"] < q["ma200"]:
        risks.append("the price is below its 200-day average (weak trend)")
    if f.get("debt_to_equity_pct") and f["debt_to_equity_pct"] > 150:
        risks.append(f"high leverage (debt/equity {f['debt_to_equity_pct']:.0f}%)")
    lines.append("**Risks to weigh** — " + ("; ".join(risks) if risks else "single-stock concentration and market-wide drawdowns") +
                 ". Size the position so a large fall would not derail your plans, and diversify across sectors.")  # fmt: skip
    return {
        "answer": "\n".join(lines),
        "mode": "data",
        "tools": [{"tool": t, "args": {"symbol": sym}, "ok": True} for t in ("get_instrument", "get_news", "get_price_stats")],
        "symbols": [sym],
        "news": news_items,
        "position": pos,
        "disclaimer": DISCLAIMER,
    }
