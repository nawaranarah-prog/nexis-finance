"""Conversational answers for the advisor when no language model is available.

Everything is computed from live data (quote, fundamentals, analyst consensus, price history, news);
the wording is assembled so it reads like a conversation — a direct answer, the reasoning on both
sides, a clear "my take", the maths for the user's position and a follow-up question. It keeps the
thread: follow-ups such as "what about the dividend?" refer to the last instrument discussed.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NexisError
from app.services import compare as cmp
from app.services import markets

GLOSSARY: dict[str, tuple[str, str]] = {
    r"p\s*/?\s*e|price[- ]to[- ]earnings|pe ratio": (
        "P/E ratio",
        "The P/E ratio is the share price divided by the company's earnings per share — roughly how many years of today's "
        "profits you're paying for. A P/E of 10 means you pay 10 AED for every 1 AED of annual profit. Low P/Es can mean a "
        "bargain *or* a business the market expects to shrink; high P/Es usually mean investors expect fast growth. It's most "
        "useful when you compare companies in the same industry.",
    ),
    r"dividend yield|dividends?": (
        "Dividends",
        "A dividend is cash a company pays its shareholders, usually once or twice a year in the UAE. The dividend yield is the "
        "annual dividend divided by the share price — a 5% yield means about 5 AED a year for every 100 AED invested. Yields "
        "aren't guaranteed: companies can cut them when profits fall, and a very high yield is sometimes a warning sign.",
    ),
    r"\betfs?\b|exchange[- ]traded fund|index fund": (
        "ETFs and index funds",
        "An ETF is a basket of investments you can buy like a single share — for example one ETF can hold all 500 companies in "
        "the S&P 500. You get instant diversification at a low cost, which is why many long-term investors build their core "
        "portfolio from a few broad index ETFs and add individual stocks around it.",
    ),
    r"\bbonds?\b|sukuk|fixed income": (
        "Bonds and sukuk",
        "A bond is a loan to a government or company that pays you interest and returns your money at maturity; sukuk are the "
        "Sharia-compliant equivalent. They're usually steadier than stocks, which is why portfolios mix both. When interest "
        "rates rise, existing bond prices fall (and vice-versa) — long-dated bonds feel that most.",
    ),
    r"diversif": (
        "Diversification",
        "Diversification means spreading your money so one bad outcome can't sink you — different companies, sectors, "
        "countries and asset types (stocks, bonds, gold, cash). A common rule of thumb is to keep any single stock to "
        "5–10% of your portfolio.",
    ),
    r"dollar[- ]cost|\bdca\b|invest(ing)? monthly|regular investing": (
        "Dollar-cost averaging",
        "Dollar-cost averaging means investing a fixed amount on a schedule (say every month) instead of all at once. You buy "
        "more shares when prices are low and fewer when they're high, and you avoid the stress of timing the market.",
    ),
    r"market cap": (
        "Market capitalisation",
        "Market cap is the total value of a company's shares: share price × number of shares. Large caps (tens of billions) "
        "tend to be steadier; small caps can grow faster but swing more.",
    ),
    r"\bbeta\b|volatil": (
        "Volatility and beta",
        "Volatility measures how much a price swings — 40% annual volatility means big moves are normal. Beta compares a "
        "stock's moves with the market: a beta of 1.5 tends to move 50% more than the market in both directions.",
    ),
    r"stop[- ]loss": (
        "Stop-loss orders",
        "A stop-loss is an order to sell automatically if the price falls to a level you choose, capping your loss. It adds "
        "discipline, but in fast markets the sale can happen below your level, and short dips can shake you out.",
    ),
}

GREETING = re.compile(r"^\s*(hi|hello|hey|yo|salam|assalam|marhaba|good (morning|evening|afternoon)|مرحبا|السلام)\b", re.I)
THANKS = re.compile(r"^\s*(thanks|thank you|thx|shukran|شكرا)\b", re.I)
COMPARE = re.compile(r"\b(vs\.?|versus|compared? (to|with)|or|and|better)\b", re.I)
SYMBOL_IN_TEXT = re.compile(r"\(([A-Z0-9^][A-Z0-9^=.\-]{0,14})\)")


def _pct(v: float | None, signed: bool = True, d: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:+.{d}f}%" if signed else f"{v * 100:.{d}f}%"


def _money(v: float | None, ccy: str | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:,.2f} {ccy}" if abs(v) < 1e5 else f"{v:,.0f} {ccy}"


def _big(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v / 1e9:.1f}bn" if abs(v) >= 1e9 else f"{v / 1e6:.0f}m"


def _intent(text: str) -> set[str]:
    t = text.lower()
    out = set()
    if re.search(r"\b(buy|invest|put .* in|should i|worth|good (buy|investment|idea)|enter|get in)\b", t):
        out.add("buy")
    if re.search(r"\b(sell|exit|get out|take profit|cut)\b", t):
        out.add("sell")
    if re.search(r"(what'?s up|news|happening|going on|latest|why is it (up|down)|why did)", t):
        out.add("news")
    if re.search(r"dividend|yield|income|payout", t):
        out.add("dividend")
    if re.search(r"expensive|cheap|overvalued|undervalued|valuation|fair value|p/?e", t):
        out.add("valuation")
    if re.search(r"analyst|target|rating|consensus", t):
        out.add("analysts")
    if re.search(r"risk|safe|lose|downside|crash", t):
        out.add("risk")
    return out


def _signals(d: dict[str, Any], st: dict[str, Any] | None) -> tuple[float, list[str], list[str]]:
    q, v, an, dv = d["quote"], d["valuation"], d["analysts"], d["dividends"]
    score, pros, cons = 0.0, [], []
    if an.get("recommendation_mean") and an.get("count"):
        trend = an.get("trend") or {}
        buys = (trend.get("strongBuy") or 0) + (trend.get("buy") or 0)
        total = sum(int(x or 0) for x in trend.values()) or int(an["count"])
        if an["recommendation_mean"] <= 2.0:
            score += 1
            pros.append(
                f"analysts are positive — {buys} of {total} rate it a buy" if buys else "analysts rate it a buy on average"
            )
        elif an["recommendation_mean"] >= 3.0:
            score -= 1
            cons.append("analysts are lukewarm on it")
    up = an.get("upside_to_mean_target")
    if up is not None:
        if up > 0.15:
            score += 1
            pros.append(f"the average analyst target is {_pct(up)} above today's price")
        elif up < 0:
            score -= 1
            cons.append(f"it already trades above the average analyst target ({_pct(up)})")
    pe = v.get("pe_forward") or v.get("pe_trailing")
    if pe and pe > 0:
        if pe <= 10:
            score += 1
            pros.append(f"it looks cheap on earnings (P/E around {pe:.1f})")
        elif pe >= 35:
            score -= 1
            cons.append(f"it's priced for a lot of growth (P/E around {pe:.0f})")
    if q.get("price") and q.get("ma200"):
        if q["price"] >= q["ma200"]:
            score += 0.5
            pros.append("the price is above its 200-day average, so the longer-term trend is up")
        else:
            score -= 0.5
            cons.append("it's trading below its 200-day average — the trend is still weak")
    if dv.get("yield") and dv["yield"] >= 0.04:
        score += 0.5
        pros.append(f"it pays a solid dividend (about {_pct(dv['yield'], False)} a year)")
    if st:
        if st.get("max_drawdown") is not None and st["max_drawdown"] < -0.3:
            score -= 0.5
            cons.append(f"it's been a bumpy ride — down {abs(st['max_drawdown']):.0%} from peak to trough in the past year")
        if st.get("annualized_volatility") and st["annualized_volatility"] > 0.45:
            score -= 0.5
            cons.append("it swings a lot (very high volatility)")
    return score, pros, cons


def _stance(score: float, intents: set[str]) -> str:
    if "sell" in intents:
        if score >= 1.5:
            return ("On balance I wouldn't rush to sell — the fundamentals and analyst support still look decent. If you need the "
                    "money or it has grown into too big a slice of your portfolio, trimming part of the position is a sensible middle ground.")  # fmt: skip
        if score <= -0.5:
            return ("I can see the case for reducing it — the signals are more negative than positive right now. You don't have to "
                    "sell everything; trimming and re-checking after the next results is a reasonable approach.")  # fmt: skip
        return "It's a genuine toss-up. If you still believe in the long-term story, holding is fine; if it keeps you up at night, trimming is fine too."
    if score >= 2:
        return ("**My take: leaning positive.** For a long-term investor this looks like a reasonable buy. I'd still build the "
                "position gradually (for example in two or three steps) rather than all at once, and keep it to a sensible slice of your portfolio.")  # fmt: skip
    if score >= 0.5:
        return ("**My take: cautiously positive.** There's more to like than to worry about, but it isn't a slam dunk. If you want "
                "exposure, start small and add on weakness rather than going all-in.")  # fmt: skip
    if score > -0.5:
        return ("**My take: neutral — a hold-and-watch.** The good and the bad roughly cancel out. I'd wait for a clearer signal "
                "(better results, a pullback in price, or the trend turning up) before committing a lot.")  # fmt: skip
    return ("**My take: cautious.** Right now the negatives outweigh the positives. If you still like the company, there's no harm in "
            "waiting for the price or the trend to improve — you can always buy later.")  # fmt: skip


def _position_text(pos: dict[str, Any]) -> str:
    ccy = pos["currency"]
    usd = f" (about {pos['cost_usd']:,.0f} USD)" if ccy == "AED" and pos.get("cost_usd") else ""
    parts = [
        f"**If you buy {pos['shares']:,.0f} shares**, that's roughly **{_money(pos['cost'], ccy)}**{usd} before broker fees."
    ]
    lines = []
    if pos.get("at_52w_low"):
        lines.append(
            f"- if it revisits its 52-week low, the position would be worth {_money(pos['at_52w_low']['value'], ccy)} ({_pct(pos['at_52w_low']['change_pct'])})"
        )
    if pos.get("at_analyst_mean_target"):
        t = pos["at_analyst_mean_target"]
        lines.append(f"- if it reaches the average analyst target, about {_money(t['value'], ccy)} ({_pct(t['change_pct'])})")
    if pos.get("annual_dividends"):
        lines.append(f"- at the current dividend you'd collect roughly {_money(pos['annual_dividends'], ccy)} a year")
    if pos.get("share_of_avg_daily_volume") is not None and pos["share_of_avg_daily_volume"] < 0.001:
        lines.append("- the order is tiny compared with daily trading volume, so you should be able to buy at the quoted price")
    return "\n".join([*parts, *lines])


def _news_text(items: list[dict[str, Any]], n: int = 3) -> str:
    if not items:
        return "I didn't find fresh headlines about it in the last few weeks — often a sign of a quiet period."
    lines = ["Here's what's been in the news lately:"]
    for it in items[:n]:
        when = (it.get("published_at") or "")[:10]
        lines.append(f"- [{it['title']}]({it['url']}) — *{it.get('publisher') or it.get('source')}*, {when}")
    return "\n".join(lines)


def _question(intents: set[str], has_position: bool) -> str:
    if "buy" in intents or has_position:
        return "How long are you planning to hold it — months or several years? And roughly what share of your savings would this be? That changes my answer quite a bit."
    if "sell" in intents:
        return "Do you need the money soon, or is this more about how the investment has been performing?"
    return "Are you thinking about buying it, or already holding it? I can tailor the answer either way."


def instrument_answer(
    db: Session, sym: str, text: str, position: dict[str, Any] | None, price_stats, news_fn
) -> tuple[str, list[dict[str, Any]]]:  # type: ignore[no-untyped-def]
    d = markets.details(db, sym)
    q, v, an, dv, f = d["quote"], d["valuation"], d["analysts"], d["dividends"], d["financials"]
    ccy, name = d["currency"], d["name"]
    try:
        st = price_stats(db, sym, "1y")
    except NexisError:
        st = None
    try:
        news_items = news_fn(db, sym)
    except NexisError:
        news_items = []
    intents = _intent(text)
    score, pros, cons = _signals(d, st)
    paras: list[str] = []

    move = f"{_pct(q.get('change_pct'))} today" if q.get("change_pct") is not None else "flat today"
    hi, lo = q.get("week52_high"), q.get("week52_low")
    where = ""
    if hi and lo and q.get("price"):
        off_high = q["price"] / hi - 1
        where = (
            f", about {abs(off_high):.0%} below its 52-week high of {hi:,.2f}"
            if off_high < -0.05
            else ", close to its 52-week high"
        )
    year = (
        f" Over the past year it's {'up' if (st or {}).get('total_return', 0) >= 0 else 'down'} {abs((st or {}).get('total_return') or 0):.0%}."
        if st
        else ""
    )
    paras.append(
        f"Here's the picture on **{name} ({sym})**: it's trading at **{_money(q.get('price'), ccy)}**, {move}{where}.{year}"
    )

    if "news" in intents or not intents or "buy" in intents:
        paras.append(_news_text(news_items))
    if "valuation" in intents or "buy" in intents or not intents:
        bits = []
        if v.get("pe_trailing"):
            bits.append(f"a P/E of {v['pe_trailing']:.1f}")
        if v.get("price_to_book"):
            bits.append(f"{v['price_to_book']:.2f}× book value")
        if f.get("revenue_growth") is not None:
            bits.append(f"revenue growth of {_pct(f['revenue_growth'])}")
        if f.get("profit_margin") is not None:
            bits.append(f"a {_pct(f['profit_margin'], False)} profit margin")
        if bits:
            paras.append(
                "On the numbers, it has " + ", ".join(bits[:-1]) + (f" and {bits[-1]}" if len(bits) > 1 else bits[0]) + "."
            )
    if "dividend" in intents:
        if dv.get("yield"):
            paras.append(
                f"On income: the dividend yield is about **{_pct(dv['yield'], False)}**"
                + (f" ({dv['rate']:.2f} {ccy} per share a year)" if dv.get("rate") else "")
                + (f", paying out {_pct(dv['payout_ratio'], False, 0)} of profits" if dv.get("payout_ratio") else "")
                + ". That's attractive, but dividends can be cut if profits fall."
            )
        else:
            paras.append("It doesn't currently pay a meaningful dividend, so the return has to come from the share price.")
    if ("analysts" in intents or "buy" in intents or not intents) and an.get("count"):
        paras.append(
            f"The {int(an['count'])} professional analysts who cover it rate it **{(an.get('recommendation') or 'n/a').replace('_', ' ')}** on average, "
            f"with a mean price target of {_money(an.get('target_mean'), ccy)}"
            + (f" ({_pct(an['upside_to_mean_target'])} from here)" if an.get("upside_to_mean_target") is not None else "")
            + ". Targets are opinions, not promises — treat them as one input."
        )
    if pros or cons:
        why = []
        if pros:
            why.append("What I like: " + "; ".join(pros[:3]) + ".")
        if cons:
            why.append("What gives me pause: " + "; ".join(cons[:3]) + ".")
        paras.append(" ".join(why))
    paras.append(_stance(score, intents))
    if position:
        paras.append(_position_text(position))
    if "risk" in intents or "buy" in intents:
        paras.append(
            "Whatever you decide, keep single stocks to a slice of your portfolio (many advisers suggest no more than 5–10% in one name) and only invest money you won't need for a few years."
        )
    paras.append(_question(intents, position is not None))
    return "\n\n".join(paras), news_items


def comparison_answer(db: Session, syms: list[str], text: str) -> str:
    r = cmp.compare(db, syms, period="1y")
    m, sn = r["metrics"], r["snapshots"]
    names = {s: sn[s].get("name") or s for s in r["symbols"]}
    best = max(r["symbols"], key=lambda s: m[s]["total_return"] or -9)
    calm = min(r["symbols"], key=lambda s: m[s]["annualized_volatility"] or 9)
    lines = [
        f"Good question — here's how {' and '.join(f'**{names[s]}** ({s})' for s in r['symbols'])} stack up over the past year:"
    ]
    for s in r["symbols"]:
        x = m[s]
        extra = []
        if sn[s].get("pe_trailing"):
            extra.append(f"P/E {sn[s]['pe_trailing']:.1f}")
        if sn[s].get("dividend_yield"):
            extra.append(f"yield {_pct(sn[s]['dividend_yield'], False)}")
        if sn[s].get("analyst_recommendation"):
            extra.append(f"analysts: {sn[s]['analyst_recommendation'].replace('_', ' ')}")
        lines.append(
            f"- **{s}**: {_pct(x['total_return'])} return, {_pct(x['annualized_volatility'], False, 0)} volatility, worst drop {_pct(x['max_drawdown'], d=0)}"
            + (f" · {', '.join(extra)}" if extra else "")
        )
    sc = r["scorecard"]
    take = [f"**{names[best]}** has been the stronger performer"]
    if calm != best:
        take.append(f"while **{names[calm]}** gave the smoother ride")
    lines.append(" ".join(take) + ".")
    if sc.get("available"):
        top = sc["ranking"][0]
        reasons = "; ".join(sc["reasons"].get(top, [])[:2])
        lines.append(f"**My take:** on balance I'd lean towards **{names[top]}**" + (f" — {reasons}" if reasons else "") + ". "
                     "If you care most about steady income, weigh the dividend yields more heavily; if you want growth and can live with swings, lean towards the stronger performer.")  # fmt: skip
    if r.get("currency_note"):
        lines.append(r["currency_note"])
    lines.append("Want me to build a full PDF comparison report, or look at a different time frame (say 3 or 5 years)?")
    return "\n\n".join(lines)


def glossary_answer(text: str) -> str | None:
    t = text.lower()
    if not re.search(r"\b(what('?s| is| are)|explain|meaning of|how does|define)\b", t):
        return None
    for pat, (title, body) in GLOSSARY.items():
        if re.search(pat, t):
            return f"**{title}** — {body}\n\nWant me to show you how this looks for a specific stock? Just name one, e.g. *Emaar* or *Apple*."
    return None


def smalltalk(text: str) -> str | None:
    if THANKS.search(text):
        return "Anytime! If you want, name another stock or fund and I'll give you my read on it."
    if GREETING.search(text) and len(text.split()) <= 6:
        return ("Hi! I'm your Nexis advisor. Ask me about any stock, fund, bond ETF, currency or crypto — including Dubai-listed "
                "shares. For example:\n- *What's happening with Emaar? I'm thinking of buying 500 shares.*\n- *Compare Emirates NBD and "
                "Dubai Islamic Bank*\n- *Is Nvidia expensive right now?*\n- *What is a P/E ratio?*")  # fmt: skip
    return None


def symbol_from_history(messages: list[dict[str, str]]) -> str | None:
    for m in reversed(messages[:-1]):
        found = SYMBOL_IN_TEXT.findall(m["content"])
        if found:
            return found[0]
    return None
