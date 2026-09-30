"""PDF reports for the markets features: instrument comparisons and valuations.

Each report contains the data tables and charts, the latest headlines, and a **Recommendation**
section. The recommendation is written by the language model when one is configured (given only
the computed figures, and told not to add numbers of its own); otherwise it is the transparent
rule-based view derived from the scorecard / valuation, and the report says which one it is.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError
from app.models import Report
from app.reporting.pdf import PageBreak, Spacer, bar_chart, build_pdf, line_chart, mm, para, table
from app.services import compare as cmp
from app.services import llm, markets, valuation
from app.services.notifications import notify

DISCLAIMER = (
    "This report is generated automatically from public market data (Yahoo Finance and TradingView public endpoints, which are unofficial and "
    "carry no warranty) and news headlines (Google News, Yahoo Finance). It is educational research, not personalised investment "
    "advice or an offer to buy or sell any security; Nexis Finance is not a licensed investment adviser. Past performance does not "
    "predict future returns. Model valuations depend entirely on their assumptions. Verify figures independently and consult a "
    "licensed professional before investing."
)


def _p(v: Any, d: int = 1, signed: bool = True) -> str:
    if v is None:
        return "–"
    return f"{v * 100:+.{d}f}%" if signed else f"{v * 100:.{d}f}%"


def _n(v: Any, d: int = 2) -> str:
    return "–" if v is None else f"{v:,.{d}f}"


def _big(v: Any) -> str:
    if v is None:
        return "–"
    a = abs(v)
    return f"{v / 1e12:.2f}T" if a >= 1e12 else f"{v / 1e9:.2f}B" if a >= 1e9 else f"{v / 1e6:.1f}M" if a >= 1e6 else f"{v:,.0f}"


def _latin(s: str) -> bool:
    try:
        s.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _narrative(kind: str, facts: dict[str, Any]) -> tuple[dict[str, str] | None, str | None]:
    """Ask the model for report prose. Returns (sections, reason-if-unavailable)."""
    prompt = (
        f"Write the narrative sections of a professional {kind} research report from the JSON facts below. Use only these facts — "
        "do not introduce any other numbers, dates or events. Respond with a JSON object with keys: "
        '"executive_summary" (80-120 words), "recommendation" (120-200 words: a clear, balanced view — which instrument(s) look '
        "most attractive in this analysis and for what kind of investor/horizon, and what would change the view), "
        '"key_risks" (3-5 short sentences separated by newlines). Plain text, no markdown.\n\nFACTS:\n'
        + json.dumps(facts, default=str)[:14000]
    )
    try:
        msg = llm.chat(
            [
                {"role": "system", "content": "You are a sell-side equity research editor. Output valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
            temperature=0.2,
        )
    except llm.LLMUnavailable as exc:
        return None, exc.reason
    text = msg.get("content") or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not data.get("recommendation"):
        return None, "the language model did not return a usable narrative"
    return {k: str(data.get(k) or "") for k in ("executive_summary", "recommendation", "key_risks")}, None


def _store(
    db: Session, title: str, kind: str, story: list[Any], footer: str, config: dict[str, Any], sections: list[str]
) -> Report:
    now = datetime.now(UTC)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:60]
    fname = f"{now:%Y%m%d-%H%M%S}-{slug}.pdf"
    path = Path(get_settings().reports_dir) / fname
    build_pdf(str(path), story, title, footer)
    content = path.read_bytes()
    rep = Report(
        title=title[:200],
        report_type=kind,
        file_name=fname,
        file_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        config=config,
        sections=sections,
        content=content,
    )
    db.add(rep)
    notify(
        db, "success", "report", f"{kind.title()} report generated", f"“{title}” ({len(content) / 1024:.0f} KB).", link="/reports"
    )
    db.commit()
    return rep


def _news_block(db: Session, symbols: list[str], per: int = 3) -> list[Any]:
    rows = [["Instrument", "Headline", "Source", "Date"]]
    for s in symbols:
        try:
            items = markets.news(db, s)["items"][:per]
        except NexisError:
            items = []
        for n in items:
            if not _latin(n["title"]):
                continue  # the PDF fonts have no Arabic/CJK glyphs; those headlines stay in the web view
            rows.append(
                [
                    s,
                    para(f'<link href="{_esc(n["url"])}" color="#2563eb">{_esc(n["title"][:140])}</link>', "small"),
                    para(
                        _esc((n.get("publisher") or n["source"])[:40]) if _latin(n.get("publisher") or "") else n["source"],
                        "small",
                    ),
                    (n.get("published_at") or "")[:10],
                ]
            )
    if len(rows) == 1:
        return [para("No recent headlines were returned by the news feeds.", "small")]
    return [table(rows, [32 * mm, 90 * mm, 30 * mm, 20 * mm], align_right_from=9)]


# ---------------------------------------------------------------------- comparison


def rule_based_comparison_view(r: dict[str, Any]) -> dict[str, str]:
    syms, m, sn, sc = r["symbols"], r["metrics"], r["snapshots"], r["scorecard"]
    if not sc.get("available"):
        s = syms[0]
        return {
            "executive_summary": f"{sn[s].get('name', s)} returned {_p(m[s]['total_return'])} over the window with a maximum drawdown of {_p(m[s]['max_drawdown'])}.",
            "recommendation": "Add at least one more instrument to produce a relative ranking.",
            "key_risks": "",
        }
    rank, comp = sc["ranking"], sc["composite"]
    top, last = rank[0], rank[-1]
    low_vol = min(syms, key=lambda s: m[s]["annualized_volatility"] if m[s]["annualized_volatility"] is not None else 9e9)
    income = max(syms, key=lambda s: sn[s].get("dividend_yield") or 0)
    pairs = [
        (a, b, r["correlation"][a][b]) for i, a in enumerate(syms) for b in syms[i + 1 :] if r["correlation"][a][b] is not None
    ]
    diversifier = min(pairs, key=lambda x: x[2]) if pairs else None
    summary = (
        f"Over {r['window']['start'][:10]} to {r['window']['end'][:10]} ({r['interval']} bars), {sn[top].get('name', top)} ranks first on the "
        f"composite scorecard ({comp[top]:.0f}/100) and {sn[last].get('name', last)} last ({comp[last]:.0f}/100). "
        f"Returns ranged from {_p(min(m[s]['total_return'] for s in syms))} to {_p(max(m[s]['total_return'] for s in syms))}."
    )
    rec = [
        f"Most attractive in this comparison: {sn[top].get('name', top)} ({top}) — "
        + ("; ".join(sc["reasons"][top][:3]) or "highest composite score")
        + "."
    ]
    if low_vol != top:
        rec.append(
            f"For capital preservation, {low_vol} had the lowest volatility ({_p(m[low_vol]['annualized_volatility'], signed=False)})."
        )
    if (sn[income].get("dividend_yield") or 0) > 0 and income != top:
        rec.append(f"For income, {income} offers the highest dividend yield ({_p(sn[income]['dividend_yield'], signed=False)}).")
    if diversifier and diversifier[2] < 0.5:
        rec.append(
            f"{diversifier[0]} and {diversifier[1]} moved least together (correlation {diversifier[2]:.2f}), so combining them diversifies."
        )
    rec.append(
        f"Least favoured: {last} — " + ("; ".join(sc.get("weaknesses", {}).get(last, [])[:2]) or "lowest composite score") + "."
    )
    rec.append("This ranking is relative to this group and window; a different horizon can change it.")
    risks = [
        f"{s}: maximum drawdown {_p(m[s]['max_drawdown'])}"
        for s in syms
        if m[s]["max_drawdown"] is not None and m[s]["max_drawdown"] < -0.2
    ]
    if r.get("currency_note"):
        risks.append("Currency: " + r["currency_note"])
    risks.append("Scores use past prices and current fundamentals; neither guarantees future results.")
    return {"executive_summary": summary, "recommendation": " ".join(rec), "key_risks": "\n".join(risks)}


def _uae_context(db: Session, r: dict[str, Any]) -> list[Any]:
    """Benchmarks every UAE investor looks at: both indices over the same window, rates and the dirham peg."""
    from app.services import uae

    out: list[Any] = [para("UAE market context", "h2")]
    try:
        idx = cmp.compare(
            db, ["DFMGI.AE", "FADGI.AD"], r["window"]["period"], None, None, r["interval"] if r["interval"] != "1h" else "1d"
        )
        rows = [["Benchmark", "Return", "Volatility", "Max drawdown"]]
        rows += [[idx["snapshots"][s].get("name") or s, _p(m["total_return"]), _p(m["annualized_volatility"], signed=False), _p(m["max_drawdown"])]
                 for s, m in idx["metrics"].items()]  # fmt: skip
        out.append(table(rows, [70 * mm, 30 * mm, 30 * mm, 30 * mm]))
    except NexisError:
        out.append(para("The UAE index series were unavailable when this report was generated.", "small"))
    lines = ["The UAE dirham is pegged to the US dollar at 3.6725, so UAE interest rates move with US policy rates and AED and USD returns "
             "are directly comparable."]  # fmt: skip
    try:
        tnx = markets.quotes(db, ["^TNX"])[0]["price"]
        gov = [
            b
            for b in uae.bonds(db)
            if b["issuer"].startswith("Government of United Arab Emirates") and b.get("yield_to_maturity")
        ]
        if gov:
            g = min(gov, key=lambda b: abs(b["years_to_maturity"] - 10))
            lines.append(f"The US 10-year Treasury yields {tnx:.2f}%; the UAE government bond maturing {g['maturity'][:4]} yields "
                         f"{g['yield_to_maturity'] * 100:.2f}% — the benchmark a dividend yield or expected return should beat.")  # fmt: skip
    except (NexisError, IndexError, KeyError, TypeError):
        pass
    out.append(para(" ".join(lines), "small"))
    return out


def generate_comparison(
    db: Session,
    symbols: list[str],
    period: str | None,
    start: date | None,
    end: date | None,
    interval: str | None,
    bucket: str | None,
    title: str | None,
) -> dict[str, Any]:
    r = cmp.compare(db, symbols, period, start, end, interval, bucket)
    syms, m, sn = r["symbols"], r["metrics"], r["snapshots"]
    title = title or f"Comparison: {' vs '.join(syms)}"
    facts = {
        k: r[k] for k in ("symbols", "interval", "window", "currencies", "currency_note", "metrics", "correlation", "snapshots")
    }
    facts["scorecard"] = {k: r["scorecard"].get(k) for k in ("ranking", "composite", "reasons", "weaknesses")}
    ai, reason = _narrative("instrument comparison", facts)
    text = ai or rule_based_comparison_view(r)

    story: list[Any] = [
        para(_esc(title), "title"),
        para(
            f"{r['window']['start'][:16].replace('T', ' ')} → {r['window']['end'][:16].replace('T', ' ')} · {r['interval']} bars · "
            f"periodic returns by {r['bucket']} · generated {datetime.now(UTC):%d %b %Y %H:%M} UTC",
            "subtitle",
        ),
        para("Executive summary", "h1"),
        para(_esc(text["executive_summary"])),
        Spacer(1, 3 * mm),
        para("Instruments", "h2"),
        table(
            [["Symbol", "Name", "Type", "Exchange", "Ccy", "Price"]]
            + [
                [
                    s,
                    para(_esc((sn[s].get("name") or s)[:70]), "small"),
                    sn[s].get("type") or "",
                    (sn[s].get("exchange") or "")[:16],
                    sn[s].get("currency") or "",
                    _n(sn[s].get("price")),
                ]
                for s in syms
            ],
            [32 * mm, 62 * mm, 14 * mm, 28 * mm, 12 * mm, 22 * mm],
            align_right_from=5,
        ),
        Spacer(1, 3 * mm),
        line_chart(r["chart"]["t"], r["chart"]["series"], "Growth of 100 (adjusted close, own currency)"),
        para("Performance and risk", "h2"),
        table(
            [["", *syms]]
            + [
                [label, *[fmt(m[s].get(k)) for s in syms]]
                for label, k, fmt in [
                    ("Total return", "total_return", _p),
                    ("Annualised return", "annualized_return", _p),
                    ("Annualised volatility", "annualized_volatility", lambda v: _p(v, signed=False)),
                    ("Sharpe ratio", "sharpe_ratio", lambda v: _n(v)),
                    ("Sortino ratio", "sortino_ratio", lambda v: _n(v)),
                    ("Maximum drawdown", "max_drawdown", _p),
                    ("Best period", "best_period", _p),
                    ("Worst period", "worst_period", _p),
                    ("% positive periods", "positive_periods", lambda v: _p(v, 0, False)),
                    (f"Correlation to {syms[0]}", "correlation_to_first", lambda v: _n(v)),
                    (f"Beta to {syms[0]}", "beta_to_first", lambda v: _n(v)),
                ]
            ]
        ),
        Spacer(1, 3 * mm),
        bar_chart(syms, [m[s]["total_return"] or 0 for s in syms], "Total return over the window", percent=True),
    ]
    per = r["periodic_returns"][-24:]
    if per:
        story += [
            para(f"Returns by {r['bucket']}", "h2"),
            table([["Period", *syms]] + [[row["period"], *[_p(row.get(s)) for s in syms]] for row in per]),
        ]
    if len(syms) > 1:
        story += [
            para("Correlation of returns", "h2"),
            table([["", *syms]] + [[a, *[_n(r["correlation"][a][b]) for b in syms]] for a in syms]),
        ]
    story += [
        PageBreak(),
        para("Fundamentals and analyst consensus", "h2"),
        table(
            [["", *syms]]
            + [
                [label, *[fmt(sn[s].get(k)) for s in syms]]
                for label, k, fmt in [
                    ("Market cap", "market_cap", _big),
                    ("P/E (trailing)", "pe_trailing", _n),
                    ("P/E (forward)", "pe_forward", _n),
                    ("Price / book", "price_to_book", _n),
                    ("EV / EBITDA", "ev_to_ebitda", _n),
                    ("Dividend yield", "dividend_yield", lambda v: _p(v, 2, False)),
                    ("Beta", "beta", _n),
                    ("Position in 52-week range", "week52_position", lambda v: _p(v, 0, False)),
                    ("Analyst consensus", "analyst_recommendation", lambda v: (v or "–").replace("_", " ")),
                    ("Analysts", "analyst_count", lambda v: _n(v, 0)),
                    ("Mean target", "analyst_target", _n),
                    ("Upside to target", "analyst_upside", _p),
                ]
            ]
        ),
    ]
    sc = r["scorecard"]
    if sc.get("available"):
        cats = list(sc["category_scores"])
        story += [
            para("Scorecard (0 = worst in group, 100 = best)", "h2"),
            table(
                [["", *[c.replace("_", " ") for c in cats], "Composite"]]
                + [[s, *[f"{sc['category_scores'][c][s]:.0f}" for c in cats], f"{sc['composite'][s]:.0f}"] for s in sc["ranking"]]
            ),
            para(
                _esc(
                    sc["method"]
                    + " Weights: "
                    + ", ".join(f"{k.replace('_', ' ')} {w:.0%}" for k, w in sc["weights"].items())
                    + "."
                ),
                "small",
            ),
        ]
    if any(x.endswith((".AE", ".AD", ".BOND")) for x in syms):
        story += _uae_context(db, r)
    story += [para("Recent headlines", "h2"), *_news_block(db, syms)]
    story += [
        para("Recommendation", "h1"),
        para(
            "Written by the AI research model from the computed figures above."
            if ai
            else f"Rule-based view derived from the scorecard (AI narrative unavailable: {_esc(reason or '')}).",
            "small",
        ),
        para(_esc(text["recommendation"])),
        para("Key risks", "h2"),
        *[para("• " + _esc(line)) for line in text["key_risks"].split("\n") if line.strip()],
        para("Methodology", "h2"),
        para(
            "Adjusted closes from the provider; returns in each instrument's own currency; annualisation uses the calendar span; "
            f"Sharpe uses a {r['risk_free_rate']:.1%} risk-free rate and is omitted for windows under three months; drawdowns are "
            "peak-to-trough on the window's prices; periodic returns use the last price in each bucket.",
            "small",
        ),
        para("Disclaimer", "h2"),
        para(DISCLAIMER, "small"),
    ]
    rep = _store(
        db,
        title,
        "comparison",
        story,
        "Educational research — not investment advice.",
        {
            "symbols": syms,
            "period": period,
            "start": str(start) if start else None,
            "end": str(end) if end else None,
            "interval": r["interval"],
            "bucket": r["bucket"],
            "ai_narrative": bool(ai),
        },
        ["summary", "performance", "periodic", "correlation", "fundamentals", "scorecard", "news", "recommendation"],
    )
    return {"report_id": rep.id, "file_name": rep.file_name, "ai_narrative": bool(ai), "ai_reason": reason}


# ---------------------------------------------------------------------- valuation


def rule_based_valuation_view(v: dict[str, Any]) -> dict[str, str]:
    px, fair, up, an = v["price"], v["blended_fair_value"], v["upside"], v["analysts"]
    ccy = v["currency"]
    summary = (
        f"{v['name']} trades at {_n(px)} {ccy}. The DCF gives {_n(v['dcf']['value_per_share'])} {ccy} per share at a "
        f"{_p(v['wacc']['used'], 2, False)} WACC and {_p(v['assumptions']['terminal_growth'], 1, False)} terminal growth; the blended "
        f"fair value across methods is {_n(fair)} {ccy} ({_p(up)})."
    )
    rec = [f"{v['view'] or 'No view'}: the blended model value is {_p(up)} versus the market price."]
    if an.get("recommendation"):
        rec.append(
            f"For reference, {int(an.get('count') or 0)} analysts rate it {an['recommendation'].replace('_', ' ')} with a mean target of {_n(an.get('target_mean'))} {ccy}."
        )
    mi = v.get("market_implied_growth") or {}
    if mi.get("note"):
        rec.append(f"Reverse DCF: {mi['note']}.")
    rec.append(
        "Treat the output as a range, not a point: see the sensitivity table and football field, and revisit the assumptions that drive it."
    )
    risks = list(v.get("warnings") or [])
    risks.append("Valuation depends on free-cash-flow normalisation, growth, discount rate and terminal assumptions.")
    return {"executive_summary": summary, "recommendation": " ".join(rec), "key_risks": "\n".join(risks)}


def generate_valuation(
    db: Session, symbol: str, overrides: dict[str, Any] | None, peers: list[str] | None, title: str | None
) -> dict[str, Any]:
    v = valuation.run(db, symbol, overrides, peers)
    ccy, a = v["currency"], v["assumptions"]
    title = title or f"Valuation: {v['name']} ({v['symbol']})"
    facts = {
        k: v[k]
        for k in (
            "symbol",
            "name",
            "currency",
            "price",
            "blended_fair_value",
            "upside",
            "view",
            "warnings",
            "market_implied_growth",
            "analysts",
            "football_field",
        )
    }
    facts |= {
        "dcf_value_per_share": v["dcf"]["value_per_share"],
        "wacc": v["wacc"],
        "assumptions": a,
        "comparables": v["comparables"]["implied"],
    }
    ai, reason = _narrative("company valuation", facts)
    text = ai or rule_based_valuation_view(v)
    ff = v["football_field"]
    story: list[Any] = [
        para(_esc(title), "title"),
        para(
            f"Price {_n(v['price'])} {ccy} · generated {datetime.now(UTC):%d %b %Y %H:%M} UTC · financials in {v['financial_currency']}",
            "subtitle",
        ),
        para("Executive summary", "h1"),
        para(_esc(text["executive_summary"])),
        table(
            [
                ["Measure", f"Value ({ccy})"],
                ["Market price", _n(v["price"])],
                ["DCF value per share", _n(v["dcf"]["value_per_share"])],
                ["Blended fair value", _n(v["blended_fair_value"])],
                ["Upside / downside", _p(v["upside"])],
                ["Analyst mean target", _n(v["analysts"].get("target_mean"))],
                ["Model view", v["view"] or "–"],
            ],
            [70 * mm, 50 * mm],
        ),
        para("Football field (value per share)", "h2"),
        table(
            [["Method", "Low", "Mid", "High"]] + [[x["method"], _n(x["low"]), _n(x["mid"]), _n(x["high"])] for x in ff],
            [80 * mm, 28 * mm, 28 * mm, 28 * mm],
        ),
        para("Discounted cash flow", "h2"),
        table(
            [
                ["Assumption", "Value", "Source"],
                ["Base free cash flow", _big(a["base_fcf"]), v["sources"].get("base_fcf", "")],
                ["Starting growth", _p(a["growth_start"]), v["sources"].get("growth_start", "")],
                ["Terminal growth", _p(a["terminal_growth"]), "assumption"],
                ["Risk-free rate", _p(a["risk_free_rate"], 2, False), v["sources"].get("risk_free_rate", "")],
                ["Beta", _n(a["beta"]), v["sources"].get("beta", "")],
                ["Equity risk premium", _p(a["equity_risk_premium"], 1, False), "assumption"],
                ["Country risk premium", _p(a["country_risk_premium"], 1, False), v["sources"].get("country_risk_premium", "")],
                ["Cost of equity", _p(v["wacc"]["cost_of_equity"], 2, False), "CAPM"],
                ["After-tax cost of debt", _p(v["wacc"]["after_tax_cost_of_debt"], 2, False), "rf + 1.5% × (1 − tax)"],
                ["WACC", _p(v["wacc"]["used"], 2, False), "overridden" if v["wacc"]["overridden"] else "market-value weights"],
            ],
            [45 * mm, 28 * mm, 100 * mm],
            align_right_from=9,
        ),
        Spacer(1, 2 * mm),
        table(
            [["Year", "Growth", "FCF", "Discount factor", "Present value"]]
            + [
                [str(p["year"]), _p(p["growth"]), _big(p["fcf"]), _n(p["discount_factor"], 3), _big(p["pv"])]
                for p in v["dcf"]["projection"]
            ]
            + [
                ["Terminal", "", _big(v["dcf"]["terminal_value"]), "", _big(v["dcf"]["pv_terminal_value"])],
                ["Enterprise value", "", "", "", _big(v["dcf"]["enterprise_value"])],
                ["Equity value", "", "", "", _big(v["dcf"]["equity_value"])],
            ]
        ),
        para("Sensitivity: value per share (rows WACC, columns terminal growth)", "h2"),
        table(
            [["WACC \\ g", *[_p(g, 1, False) for g in v["sensitivity"]["terminal_growth"]]]]
            + [
                [_p(w, 1, False), *[_n(x) for x in row]]
                for w, row in zip(v["sensitivity"]["wacc"], v["sensitivity"]["value_per_share"], strict=True)
            ]
        ),
    ]
    comps = v["comparables"]
    if comps["peers"]:
        story += [
            PageBreak(),
            para("Trading comparables", "h2"),
            table(
                [["Peer", "Mkt cap", "P/E", "EV/EBITDA", "EV/Rev", "P/B", "EBITDA mgn", "Rev growth"]]
                + [
                    [
                        p["symbol"],
                        _big(p["market_cap"]),
                        _n(p["pe"]),
                        _n(p["ev_ebitda"]),
                        _n(p["ev_revenue"]),
                        _n(p["pb"]),
                        _p(p["ebitda_margin"], 1, False),
                        _p(p["revenue_growth"]),
                    ]
                    for p in comps["peers"]
                ]
            ),
            Spacer(1, 2 * mm),
            table(
                [["Multiple", "Peer median", f"{v['symbol']} multiple", f"Implied price ({ccy})", "Peers used"]]
                + [
                    [c["label"], _n(c["peer_median"]), _n(c["target_multiple"]), _n(c["implied_price"]), str(c["peers_used"])]
                    for c in comps["implied"].values()
                ]
            ),
        ]
    hist = v["history"]
    if hist["years"]:
        story += [
            para("Reported history", "h2"),
            table(
                [["", *hist["years"]]]
                + [
                    [label, *[_big(hist[k].get(y)) for y in hist["years"]]]
                    for label, k in (("Revenue", "revenue"), ("EBITDA", "ebitda"), ("Free cash flow", "free_cash_flow"))
                ]
            ),
        ]
    story += [para("Recent headlines", "h2"), *_news_block(db, [v["symbol"]], per=6)]
    story += [
        para("Recommendation", "h1"),
        para(
            "Written by the AI research model from the computed figures above."
            if ai
            else f"Rule-based view derived from the valuation (AI narrative unavailable: {_esc(reason or '')}).",
            "small",
        ),
        para(_esc(text["recommendation"])),
        para("Key risks and caveats", "h2"),
        *[para("• " + _esc(line)) for line in text["key_risks"].split("\n") if line.strip()],
        para("Disclaimer", "h2"),
        para(DISCLAIMER, "small"),
    ]
    rep = _store(
        db,
        title,
        "valuation",
        story,
        "Model valuation — not investment advice.",
        {"symbol": v["symbol"], "overrides": overrides or {}, "peers": peers, "ai_narrative": bool(ai)},
        ["summary", "football_field", "dcf", "sensitivity", "comparables", "history", "news", "recommendation"],
    )
    return {"report_id": rep.id, "file_name": rep.file_name, "ai_narrative": bool(ai), "ai_reason": reason}
