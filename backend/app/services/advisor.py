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
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, NexisError, ProviderError
from app.db.base import utcnow
from app.models import MarketCache
from app.services import advisor_voice as voice
from app.services import compare as cmp
from app.services import llm, markets, valuation

DISCLAIMER = (
    "Educational analysis from public data — not personalised investment advice, and Nexis is not a licensed adviser. "
    "Markets can fall; consider your goals, horizon and risk tolerance, and consult a licensed professional before investing."
)

SYSTEM = """You are Nexis Advisor — the AI financial advisor inside the Nexis Finance app. You talk like a brilliant, warm,
senior wealth adviser and equity analyst sitting across the table from the user: natural conversation, clear opinions,
real numbers, zero fluff. Your advice should be more thorough and more useful than any generic chatbot's, because you
work from live data.

Context: the app is built for investors in the UAE (dirham is pegged to the US dollar at 3.6725). When a UAE share is discussed,
compare it with UAE sector peers and the local index; when an investor asks about bonds or low-risk options, look at UAE
government bonds and sukuk.

How you work
- Always research before you answer. Use the tools generously — typically several per question:
  * resolve names with search_instruments — this is a UAE-first app: Abu Dhabi (ADX) shares end in .AD (FAB.AD, ALDAR.AD, IHC.AD),
    Dubai (DFM) shares in .AE (EMAAR.AE, EMIRATESNBD.AE), UAE government/quasi-government bonds and sukuk in .BOND
    (search "UAE bond" or "sukuk"), and the indices are DFMGI.AE and FADGI.AD; Saudi shares end in .SR;
  * get_instrument for the live quote, valuation, financials, dividends and the analyst consensus;
  * get_news for what is happening right now (read the headlines and explain why they matter);
  * get_price_stats for 1y behaviour, and 5y when the horizon is long;
  * run_valuation when the user asks whether something is cheap/expensive or worth buying;
  * compare_instruments to benchmark against peers or alternatives (e.g. a stock vs its sector peers, vs the local index
    DFMGI.AE or the S&P 500) — suggest a better alternative when there is one;
  * position_calculator whenever shares or a money amount are mentioned.
- Every number and fact must come from tool results in this conversation. Never invent prices, ratios, dates or news. If
  data is missing, say so plainly.

How you answer
- Open with a direct answer to the question in one or two sentences (a clear view — e.g. "I'd buy some, but in stages" /
  "I'd wait" / "I'd trim"), then explain. Write in flowing, conversational paragraphs with a few short headings or bullets
  where they genuinely help; bold the key numbers.
- Cover what matters for the decision: what the business does and how it's doing; what's in the news and why it matters;
  valuation vs history, peers and analyst targets; momentum and risk (drawdowns, volatility); income; the bull case and the
  bear case; and your recommendation with reasoning.
- Make it actionable: for a buy idea, suggest a sensible position size (as a share of the portfolio), an entry approach
  (e.g. split into 2–3 purchases), what would make you change your mind, and what to watch next (earnings dates, catalysts).
  When the user gives a quantity or amount, show the cost, scenarios (52-week low, analyst target) and dividend income.
- Tailor to the person: if you don't know their horizon, risk tolerance or how big this is versus their savings, give your
  best general advice and ask one or two sharp follow-up questions at the end.
- Be honest about uncertainty; never promise returns; never suggest putting everything in one position.
- Reply in the user's language (Arabic if they write in Arabic, etc.).
- Length: as long as the question deserves — usually 300–700 words for an investment question, shorter for simple ones.
- End with one short italic line: *Educational guidance based on public data — not personalised financial advice.*"""


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


# ---------------------------------------------------------------------- entry points

TOOL_STATUS = {
    "search_instruments": "Looking up {query}",
    "get_instrument": "Checking {symbol}'s price, valuation and analyst ratings",
    "get_news": "Reading the latest news on {symbol}",
    "get_price_stats": "Analysing {symbol}'s price history",
    "compare_instruments": "Comparing {symbols}",
    "run_valuation": "Running a valuation on {symbol}",
    "position_calculator": "Working out your position in {symbol}",
}


def _status_text(name: str, args: dict[str, Any]) -> str:
    tpl = TOOL_STATUS.get(name, name)
    try:
        return tpl.format(
            query=args.get("query", ""), symbol=args.get("symbol", ""), symbols=", ".join(args.get("symbols") or [])
        )
    except (KeyError, IndexError):
        return tpl


def stream(db: Session, messages: list[dict[str, str]], language: str | None = None) -> Iterator[dict[str, Any]]:
    """Answer as a stream of events: ``status`` (what is being looked up), ``delta`` (answer text) and ``done``."""
    if not messages or messages[-1]["role"] != "user":
        raise ConfigurationError("the last message must be from the user")
    trace: list[dict[str, Any]] = []
    sent_text = False
    try:
        for ev in _llm_stream(db, messages, trace, language):
            if ev["type"] == "delta":
                sent_text = True
            yield ev
        _record_llm_state(db, None)
        return
    except llm.LLMUnavailable as exc:
        _record_llm_state(db, exc.reason)
        if sent_text:
            yield {"type": "delta", "text": "\n\n*(The AI model stopped responding — please ask again.)*"}
            yield {"type": "done", "meta": {"mode": "ai", "tools": trace, "symbols": [], "news": [], "disclaimer": DISCLAIMER}}
            return
        reason = exc.reason
    yield {"type": "status", "text": "Pulling live prices, fundamentals and news"}
    out = briefing(db, messages[-1]["content"], messages)
    out["ai"] = {"used": False, "reason": reason}
    yield {"type": "delta", "text": out.pop("answer")}
    yield {"type": "done", "meta": out}


def ask(db: Session, messages: list[dict[str, str]], language: str | None = None) -> dict[str, Any]:
    """Non-streaming variant: collects the stream into one answer."""
    text, meta = [], {}
    for ev in stream(db, messages, language):
        if ev["type"] == "delta":
            text.append(ev["text"])
        elif ev["type"] == "done":
            meta = ev["meta"]
    return {"answer": "".join(text).strip(), **meta}


def _llm_stream(
    db: Session, messages: list[dict[str, str]], trace: list[dict[str, Any]], language: str | None = None
) -> Iterator[dict[str, Any]]:
    today = datetime.now(UTC).strftime("%A %d %B %Y, %H:%M UTC")
    lang = (
        "\n\nThe user has chosen Arabic: always answer in clear Modern Standard Arabic (keep tickers and numbers as they are)."
        if language == "ar"
        else ""
    )
    convo: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM + lang + f"\n\nCurrent date and time: {today}."}]
    convo += [{"role": m["role"], "content": m["content"][:6000]} for m in messages[-16:]]
    news_used: list[dict[str, Any]] = []
    symbols: list[str] = []
    for _ in range(8):
        msg: dict[str, Any] = {}
        for kind, payload in llm.chat_stream(convo, TOOLS, max_tokens=3500):
            if kind == "text":
                yield {"type": "delta", "text": payload}
            else:
                msg = payload
        calls = msg.get("tool_calls") or []
        if not calls:
            if not (msg.get("content") or "").strip():
                raise llm.LLMUnavailable("The language model returned an empty answer.")
            meta = {"mode": "ai", "ai": {"used": True, "provider": msg.get("_provider"), "model": msg.get("_model")}, "tools": trace, "symbols": list(dict.fromkeys(symbols)),
                    "news": news_used[:8], "disclaimer": DISCLAIMER}  # fmt: skip
            yield {"type": "done", "meta": meta}
            return
        if msg.get("content"):
            yield {"type": "delta", "text": "\n\n"}
        convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name, args = c["function"]["name"], {}
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
                yield {"type": "status", "text": _status_text(name, args)}
                result = _run_tool(db, name, args)
                ok = True
            except (NexisError, KeyError, TypeError, ValueError) as exc:
                result, ok = {"error": getattr(exc, "message", str(exc))}, False
            trace.append({"tool": name, "args": args, "ok": ok})
            if ok and name == "get_news":
                news_used += result["items"][:4]
            if ok and isinstance(result, dict) and result.get("symbol"):
                symbols.append(result["symbol"])
            convo.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=str)[:14000]})
    raise llm.LLMUnavailable("The language model did not finish within the tool-call budget.")


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
            llm.chat([{"role": "user", "content": "Reply with: ok"}], max_tokens=20)
            _record_llm_state(db, None)
        except llm.LLMUnavailable as exc:
            _record_llm_state(db, exc.reason)
        row = db.get(MarketCache, _LLM_STATE)
    state = row.payload["v"]
    return {**st, "available": bool(state["ok"]), "last_error": state["reason"]}


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
        "dividend",
        "dividends",
        "target",
        "targets",
        "analyst",
        "analysts",
        "risk",
        "risky",
        "valuation",
        "expensive",
        "cheap",
        "fair",
        "value",
        "hold",
        "long",
        "term",
        "short",
        "week",
        "month",
        "year",
        "years",
        "earnings",
        "better",
        "which",
        "best",
        "right",
        "should",
        "could",
        "will",
        "think",
        "about",
        "buying",
        "selling",
        "sell",
        "own",
        "owning",
        "holding",
        "money",
        "portfolio",
        "after",
        "before",
        "still",
        "really",
        "more",
        "less",
        "than",
        "know",
        "explain",
        "tell",
        "advice",
        "advise",
        "recommend",
        "opinion",
        "view",
        "market",
        "markets",
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


_FOLLOW_UP = re.compile(
    r"\b(it|its|it's|this|that|they|them|the stock|the company|the share|what about|how about|and the|dividend|target|analysts?|"
    r"valuation|expensive|cheap|risk|risky|sell|hold|news|earnings|should i|worth it|long term|short term)\b",
    re.I,
)
_SPLIT = re.compile(r"\b(?:vs\.?|versus|compared (?:to|with)|against|and|or)\b|,|/", re.I)


def _result(
    answer: str, symbols: list[str] | None = None, news: list[dict[str, Any]] | None = None, pos: Any = None
) -> dict[str, Any]:
    return {
        "answer": answer,
        "mode": "data",
        "tools": [
            {"tool": t, "args": {"symbol": s}, "ok": True}
            for s in (symbols or [])
            for t in ("get_instrument", "get_news", "get_price_stats")
        ],
        "symbols": symbols or [],
        "news": news or [],
        "position": pos,
        "disclaimer": DISCLAIMER,
    }


def _resolve_many(db: Session, text: str) -> list[str]:
    parts = [
        p.strip()
        for p in _SPLIT.split(re.sub(r"(?i)^\s*(compare|which is better|is it better to buy)\s*", "", text))
        if p and p.strip()
    ]
    out: list[str] = []
    for part in parts:
        if len(re.findall(r"[A-Za-z]", part)) < 2:
            continue
        hit = resolve_symbol(db, part)
        if hit and hit["symbol"] not in out:
            out.append(hit["symbol"])
    return out[:5]


def briefing(db: Session, question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Conversational answer assembled from live data (used when no language model is available)."""
    messages = history or [{"role": "user", "content": question}]
    text = question.strip()
    if reply := voice.smalltalk(text):
        return _result(reply)
    gloss = voice.glossary_answer(text)
    explicit = re.search(r"\$?\b[A-Z]{2,6}(\.[A-Z]{1,3})?\b|\.ae\b", text)
    if gloss and not explicit:
        return _result(gloss)
    if voice.COMPARE.search(text):
        syms = _resolve_many(db, text)
        if len(syms) >= 2:
            try:
                return _result(voice.comparison_answer(db, syms, text), syms)
            except NexisError as exc:
                return _result(
                    f"I tried to compare {', '.join(syms)} but couldn't get the data right now ({exc.message}). Try again in a minute?"
                )
    prior = voice.symbol_from_history(messages)
    sym = None
    if prior and _FOLLOW_UP.search(text) and not explicit and len(text.split()) <= 14:
        sym = prior
    if sym is None:
        inst = resolve_symbol(db, text)
        sym = inst["symbol"] if inst else prior
    if sym is None:
        return _result(
            "Happy to help — which company, fund or market are you asking about? You can type a name (*Emaar*, *Emirates NBD*, "
            "*Apple*), a ticker (*EMAAR.AE*, *NVDA*) or something like *gold*, *S&P 500* or *Bitcoin*. If you're planning a purchase, "
            "tell me the number of shares or the amount and I'll work out the numbers."
        )
    req = parse_request(text)
    pos = None
    if req["shares"] or req["amount"]:
        try:
            pos = position(db, sym, req["shares"], req["amount"], req["amount_currency"])
        except NexisError:
            pos = None
    try:
        answer, news_items = voice.instrument_answer(db, sym, text, pos, price_stats, lambda d, s: markets.news(d, s)["items"])
    except NexisError as exc:
        return _result(f"I couldn't load live data for {sym} right now ({exc.message}). Mind trying again in a moment?")
    return _result(answer, [sym], news_items[:6], pos)
