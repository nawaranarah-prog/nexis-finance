"""Investment-banking style valuation: discounted cash flow, trading comparables and a football field.

Defaults are derived from reported data (annual statements, key statistics, the US 10-year yield)
and every assumption is returned so it can be inspected and overridden. The output is a model
estimate under stated assumptions — not a price target.
"""

from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, InsufficientDataError, ProviderError
from app.services import markets

# Statutory corporate tax rates used as defaults (editable).
TAX = {"AE": 0.09, "US": 0.21, "SA": 0.20, "GB": 0.25, "DE": 0.30, "FR": 0.25, "IN": 0.25, "JP": 0.30}
# Country risk premium over a mature market (Damodaran-style order of magnitude; editable).
CRP = {"AE": 0.007, "SA": 0.009, "US": 0.0}
SUFFIX_COUNTRY = {".AE": "AE", ".SR": "SA", ".L": "GB", ".DE": "DE", ".PA": "FR", ".NS": "IN", ".BO": "IN", ".T": "JP"}
COUNTRY_CODE = {"United Arab Emirates": "AE", "United States": "US", "Saudi Arabia": "SA", "United Kingdom": "GB"}
EQUITY_RISK_PREMIUM = 0.05
# Curated sector peer groups where the provider's "similar" suggestions (co-viewing data) are weakest.
PEER_GROUPS: dict[str, list[str]] = {
    "GCC banks": ["EMIRATESNBD.AE", "DIB.AE", "CBD.AE", "MASQ.AE", "1120.SR", "1180.SR", "1010.SR", "1150.SR", "QNBK.QA"],
    "GCC real estate": ["EMAAR.AE", "EMAARDEV.AE", "UPP.AE", "4300.SR", "4250.SR", "4020.SR", "4322.SR", "4090.SR"],
    "GCC utilities & infrastructure": ["DEWA.AE", "EMPOWER.AE", "SALIK.AE", "PARKIN.AE", "2082.SR", "5110.SR"],
    "GCC telecoms": ["DU.AE", "7010.SR", "7020.SR"],
    "US mega-cap technology": ["AAPL", "MSFT", "GOOGL", "META", "NVDA", "AMZN", "ORCL", "AVGO"],
}


def _country(symbol: str, profile_country: str | None) -> str:
    for suf, c in SUFFIX_COUNTRY.items():
        if symbol.endswith(suf):
            return c
    return COUNTRY_CODE.get(profile_country or "", "US")


def _risk_free(db: Session) -> tuple[float, str]:
    try:
        q = markets.quotes(db, ["^TNX"])[0]
        if q.get("price"):
            return q["price"] / 100, "US 10-year Treasury yield (^TNX)"
    except (ProviderError, IndexError):
        pass
    return 0.04, "fallback 4.0% (10-year yield unavailable)"


def _fx(db: Session, frm: str | None, to: str | None) -> float:
    if not frm or not to or frm == to:
        return 1.0
    if {frm, to} == {"AED", "USD"}:
        return 3.6725 if frm == "USD" else 1 / 3.6725  # the dirham peg
    q = markets.quotes(db, [f"{frm}{to}=X"])
    if not q or not q[0].get("price"):
        raise ProviderError(f"no FX rate {frm}/{to} to align reporting and trading currencies")
    return float(q[0]["price"])


def defaults(db: Session, symbol: str) -> dict[str, Any]:
    d = markets.details(db, symbol)
    if d["type"] not in ("equity", ""):
        raise ConfigurationError(f"{d['symbol']} is a {d['type']} — DCF and comparables apply to operating companies (equities)")
    st = markets.statements(db, symbol)
    rows = {r["key"]: r["values"] for r in st["rows"]}
    years = st["years"]

    def series(k: str) -> list[float]:
        return [v for y in years if (v := rows.get(k, {}).get(y)) is not None]

    fcf_hist, rev = series("annualFreeCashFlow"), series("annualTotalRevenue")
    # Normalise cyclical cash flows: average of up to the last three fiscal years.
    recent = fcf_hist[-3:]
    base_fcf = sum(recent) / len(recent) if recent else d["financials"].get("free_cash_flow")
    if base_fcf is None:
        raise InsufficientDataError("no free-cash-flow data reported for this company")
    if len(rev) >= 2 and rev[0] > 0 and rev[-1] > 0:
        growth = (rev[-1] / rev[0]) ** (1 / (len(rev) - 1)) - 1
        growth_src = f"revenue CAGR {years[0]}–{years[-1]}"
    else:
        growth = d["financials"].get("revenue_growth") or 0.03
        growth_src = "latest reported revenue growth"
    country = _country(d["symbol"], d["profile"].get("country"))
    rf, rf_src = _risk_free(db)
    raw_beta = d["valuation"].get("beta")
    beta = 0.67 * raw_beta + 0.33 if raw_beta is not None else 1.0
    fin_ccy, px_ccy = d.get("financial_currency") or d["currency"], d["currency"]
    return {
        "symbol": d["symbol"],
        "name": d["name"],
        "currency": px_ccy,
        "financial_currency": fin_ccy,
        "price": d["quote"]["price"],
        "country": country,
        "assumptions": {
            "base_fcf": base_fcf,
            "years": 5,
            "growth_start": round(max(-0.05, min(0.12, growth)), 4),
            "terminal_growth": 0.025,
            "risk_free_rate": round(rf, 4),
            "beta": round(beta, 3),
            "equity_risk_premium": EQUITY_RISK_PREMIUM,
            "country_risk_premium": CRP.get(country, 0.01),
            "pre_tax_cost_of_debt": round(rf + 0.015, 4),
            "tax_rate": TAX.get(country, 0.25),
            "cash": d["financials"].get("cash") or 0.0,
            "debt": d["financials"].get("debt") or 0.0,
            "shares_outstanding": d["shares"].get("outstanding"),
            "market_cap": d["valuation"].get("market_cap"),
        },
        "sources": {
            "base_fcf": f"average free cash flow FY{years[-len(recent)]}–FY{years[-1]}" if recent else "trailing twelve months free cash flow",
            "growth_start": growth_src + " (clipped to −5%…12%), fading linearly to terminal growth",
            "risk_free_rate": rf_src,
            "beta": f"Blume-adjusted (0.67 × raw {raw_beta:.2f} + 0.33)" if raw_beta is not None else "no reported beta — 1.0 assumed",
            "country_risk_premium": f"country {country}",
            "tax_rate": f"statutory rate, {country}",
        },
        "history": {"years": years, "free_cash_flow": {y: rows.get("annualFreeCashFlow", {}).get(y) for y in years},
                    "revenue": {y: rows.get("annualTotalRevenue", {}).get(y) for y in years},
                    "ebitda": {y: rows.get("annualEBITDA", {}).get(y) for y in years}},
        "details": d,
    }  # fmt: skip


def wacc(a: dict[str, Any]) -> dict[str, float]:
    ke = a["risk_free_rate"] + a["beta"] * a["equity_risk_premium"] + a["country_risk_premium"]
    kd = a["pre_tax_cost_of_debt"] * (1 - a["tax_rate"])
    e, dbt = max(a.get("market_cap") or 0.0, 0.0), max(a.get("debt") or 0.0, 0.0)
    we = e / (e + dbt) if e + dbt > 0 else 1.0
    return {
        "cost_of_equity": ke,
        "after_tax_cost_of_debt": kd,
        "equity_weight": we,
        "debt_weight": 1 - we,
        "wacc": we * ke + (1 - we) * kd,
    }


def dcf(a: dict[str, Any], w: float, g_term: float, fx: float = 1.0) -> dict[str, Any]:
    """Two-stage FCFF model. ``fx`` converts reporting-currency values into the trading currency."""
    n = int(a["years"])
    if not 3 <= n <= 10:
        raise ConfigurationError("projection years must be between 3 and 10")
    if w <= g_term:
        raise ConfigurationError("WACC must exceed terminal growth")
    shares = a.get("shares_outstanding")
    if not shares:
        raise InsufficientDataError("shares outstanding not reported")
    g0 = a["growth_start"]
    fcf, rows, pv_sum = a["base_fcf"], [], 0.0
    for t in range(1, n + 1):
        g = g0 + (g_term - g0) * (t - 1) / max(n - 1, 1)
        fcf *= 1 + g
        df = 1 / (1 + w) ** t
        pv_sum += fcf * df
        rows.append({"year": t, "growth": g, "fcf": fcf, "discount_factor": df, "pv": fcf * df})
    tv = fcf * (1 + g_term) / (w - g_term)
    pv_tv = tv / (1 + w) ** n
    ev = pv_sum + pv_tv
    equity = ev - (a.get("debt") or 0.0) + (a.get("cash") or 0.0)
    return {
        "projection": rows,
        "terminal_value": tv,
        "pv_terminal_value": pv_tv,
        "pv_forecast": pv_sum,
        "enterprise_value": ev,
        "equity_value": equity,
        "terminal_share_of_ev": pv_tv / ev if ev else None,
        "value_per_share": equity / shares * fx,
    }


def _implied_growth(a: dict[str, Any], w: float, fx: float, price: float | None) -> dict[str, Any]:
    """Reverse DCF: the starting growth rate at which the model value equals the market price."""
    if not price or w <= a["terminal_growth"]:
        return {"growth": None, "note": "not computable"}

    def value(g: float) -> float:
        return dcf({**a, "growth_start": g}, w, a["terminal_growth"], fx)["value_per_share"]

    lo, hi = -0.5, 1.0
    try:
        if price < value(lo):
            return {"growth": None, "note": "the price implies cash flows shrinking faster than 50% a year at this WACC"}
        if price > value(hi):
            return {"growth": None, "note": "the price implies starting growth above 100% a year at this WACC"}
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if value(mid) < price else (lo, mid)
    except (ConfigurationError, InsufficientDataError, ZeroDivisionError):
        return {"growth": None, "note": "not computable"}
    g = (lo + hi) / 2
    return {"growth": g, "note": f"the price implies starting free-cash-flow growth of {g:.1%} a year, fading to terminal growth"}


def _is_financial(d: dict[str, Any]) -> bool:
    p = d.get("profile") or {}
    return p.get("sector") == "Financial Services" or "bank" in (p.get("industry") or "").lower()


def _median(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None and x > 0]
    return statistics.median(xs) if xs else None


def _quartiles(xs: list[float]) -> tuple[float, float] | None:
    xs = sorted(x for x in xs if x is not None and x > 0)
    if len(xs) < 2:
        return None
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], q[2]


def comparables(db: Session, symbol: str, peers: list[str] | None, target: dict[str, Any]) -> dict[str, Any]:
    group = next((name for name, members in PEER_GROUPS.items() if target["symbol"] in members), None)
    if peers:
        source = "your peers"
    elif group:
        peers_auto, source = [m for m in PEER_GROUPS[group] if m != target["symbol"]], f"curated peer group: {group}"
    else:
        peers_auto, source = markets.peers(db, symbol), "provider suggestions, filtered to the same sector"
    peer_syms = [markets.clean_symbol(p) for p in (peers or peers_auto)][:8]
    peer_syms = [p for p in peer_syms if p != target["symbol"]]
    sector = target["details"]["profile"].get("sector")
    rows, skipped, other_sector = [], [], []
    for p in peer_syms:
        try:
            d = markets.details(db, p)
        except ProviderError as exc:
            skipped.append({"symbol": p, "reason": exc.message})
            continue
        if d["type"] != "equity":
            skipped.append({"symbol": p, "reason": f"{d['type']} — not an operating company"})
            continue
        v, f = d["valuation"], d["financials"]
        if not peers and sector and d["profile"].get("sector") and d["profile"]["sector"] != sector:
            other_sector.append(p)
        rows.append({
            "symbol": p, "name": d["name"], "currency": d["currency"], "market_cap": v.get("market_cap"),
            "pe": v.get("pe_trailing"), "pe_forward": v.get("pe_forward"), "ev_ebitda": v.get("ev_to_ebitda"),
            "ev_revenue": v.get("ev_to_revenue"), "pb": v.get("price_to_book"), "ebitda_margin": f.get("ebitda_margin"),
            "revenue_growth": f.get("revenue_growth"), "roe": f.get("return_on_equity"),
        })  # fmt: skip
    if not peers and not group and other_sector and len(other_sector) < len(rows):
        # Suggested peers come from co-viewing data, not sector screens: keep same-sector companies when any exist.
        rows = [r for r in rows if r["symbol"] not in other_sector]
    t = target["details"]
    tv, tf = t["valuation"], t["financials"]
    shares = target["assumptions"].get("shares_outstanding")
    net_debt = (target["assumptions"].get("debt") or 0.0) - (target["assumptions"].get("cash") or 0.0)
    fx = target.get("fx", 1.0)
    implied: dict[str, Any] = {}
    specs = [
        ("pe", "P/E (trailing)", lambda m: m * tv["eps_trailing"] if tv.get("eps_trailing") and tv["eps_trailing"] > 0 else None),
        (
            "ev_ebitda",
            "EV/EBITDA",
            lambda m: (m * tf["ebitda"] - net_debt) / shares * fx if tf.get("ebitda") and shares else None,
        ),
        (
            "ev_revenue",
            "EV/Revenue",
            lambda m: (m * tf["revenue"] - net_debt) / shares * fx if tf.get("revenue") and shares else None,
        ),
        ("pb", "P/B", lambda m: m * tv["book_value_per_share"] if tv.get("book_value_per_share") else None),
    ]
    if _is_financial(t):
        specs = [x for x in specs if x[0] in ("pe", "pb")]  # EV is not meaningful for banks and insurers
    for key, label, fn in specs:
        vals = [r[key] for r in rows]
        med, q = _median(vals), _quartiles(vals)
        if med is None:
            continue
        mid = fn(med)
        if mid is None or mid <= 0:
            continue
        lo, hi = (fn(q[0]), fn(q[1])) if q else (mid, mid)
        implied[key] = {"label": label, "peer_median": med, "target_multiple": tv.get({"pe": "pe_trailing", "ev_ebitda": "ev_to_ebitda", "ev_revenue": "ev_to_revenue", "pb": "price_to_book"}[key]),
                        "implied_price": mid, "range": [lo, hi], "peers_used": sum(1 for v in vals if v and v > 0)}  # fmt: skip
    return {
        "peers": rows,
        "skipped": skipped,
        "implied": implied,
        "other_sector": other_sector if not group else [],
        "auto_selected": not peers,
        "source": source,
    }


def run(db: Session, symbol: str, overrides: dict[str, Any] | None = None, peers: list[str] | None = None) -> dict[str, Any]:
    base = defaults(db, symbol)
    a = {**base["assumptions"], **{k: v for k, v in (overrides or {}).items() if k in base["assumptions"] and v is not None}}
    fx = _fx(db, base["financial_currency"], base["currency"])
    base["fx"] = fx
    wc = wacc(a)
    w = float((overrides or {}).get("wacc") or wc["wacc"])
    main = dcf(a, w, a["terminal_growth"], fx)
    grid_w = [round(w + dw, 4) for dw in (-0.01, -0.005, 0, 0.005, 0.01)]
    grid_g = [round(a["terminal_growth"] + dg, 4) for dg in (-0.01, -0.005, 0, 0.005, 0.01)]
    sens = [[dcf(a, ww, gg, fx)["value_per_share"] if ww > gg else None for gg in grid_g] for ww in grid_w]
    comps = comparables(db, symbol, peers, base)
    implied_growth = _implied_growth(a, w, fx, base["price"])
    d, px = base["details"], base["price"]
    ranges = []
    flat = [v for row in sens for v in row if v is not None]
    financial = _is_financial(base["details"])
    ranges.append(
        {
            "method": "DCF (WACC ±1%, g ±1%)" + (" — not meaningful for banks" if financial else ""),
            "low": min(flat),
            "high": max(flat),
            "mid": None if financial else main["value_per_share"],
        }
    )
    for c in comps["implied"].values():
        ranges.append(
            {"method": f"Comps · {c['label']}", "low": min(c["range"]), "high": max(c["range"]), "mid": c["implied_price"]}
        )
    q = d["quote"]
    if q.get("week52_low") and q.get("week52_high"):
        ranges.append({"method": "52-week trading range", "low": q["week52_low"], "high": q["week52_high"], "mid": None})
    an = d["analysts"]
    if an.get("target_low") and an.get("target_high"):
        ranges.append(
            {
                "method": f"Analyst targets ({int(an.get('count') or 0)} analysts)",
                "low": an["target_low"],
                "high": an["target_high"],
                "mid": an.get("target_mean"),
            }
        )
    fair = [r["mid"] for r in ranges if r["mid"] and not r["method"].startswith("Analyst")]
    blended = statistics.median(fair) if fair else None
    upside = blended / px - 1 if blended and px else None
    view = (
        None if upside is None
        else "Undervalued on these assumptions" if upside > 0.15
        else "Overvalued on these assumptions" if upside < -0.10
        else "Fairly valued on these assumptions"
    )  # fmt: skip
    warnings = []
    if financial:
        warnings.append(
            "For banks and other financials, free-cash-flow DCFs and EV multiples are not meaningful; the fair value uses P/E and P/B only."
        )
    if main["terminal_share_of_ev"] and main["terminal_share_of_ev"] > 0.75:
        warnings.append(
            f"The terminal value is {main['terminal_share_of_ev']:.0%} of enterprise value, so the DCF is dominated by long-run assumptions."
        )
    if px and not 1 / 3 <= main["value_per_share"] / px <= 3:
        warnings.append(
            f"The DCF value ({main['value_per_share']:.2f}) is far from the market price ({px:.2f}): the market is pricing different cash-flow, "
            "growth or discount-rate assumptions than these defaults (reported free cash flow can also include non-recurring or "
            "non-distributable items). Compare with the market-implied growth below and adjust the inputs."
        )
    used_peers = [r["symbol"] for r in comps["peers"]]
    if comps["auto_selected"] and comps["other_sector"]:
        if set(comps["other_sector"]) & set(used_peers):
            warnings.append(
                f"No same-sector peers were suggested, so the comparables use companies from other sectors ({', '.join(comps['other_sector'])}); "
                "enter sector peers for a meaningful comparison."
            )
        else:
            warnings.append(
                f"Suggested peers from other sectors were left out ({', '.join(comps['other_sector'])}); the comparables use "
                f"{', '.join(used_peers)} only — add more sector peers for a sturdier median."
            )
    elif len(used_peers) < 3:
        warnings.append(f"Only {len(used_peers)} peer(s) in the comparables — the medians are fragile.")
    if fx != 1.0:
        warnings.append(
            f"Financials are reported in {base['financial_currency']} and converted to {base['currency']} at {fx:.4f}."
        )
    confidence = (
        "low"
        if len([w for w in warnings if not w.startswith("Financials are reported")]) >= 2
        else "medium"
        if warnings
        else "normal"
    )
    mids = [r["mid"] for r in ranges if r["mid"] and not r["method"].startswith(("Analyst", "52-week"))]
    if len(mids) >= 2 and max(mids) / min(mids) > 2.5:
        view, confidence = "Inconclusive — the valuation methods disagree by more than 2.5×", "low"
    elif view and confidence == "low":
        view += " (low confidence — see warnings)"
    return {
        "symbol": base["symbol"],
        "name": base["name"],
        "currency": base["currency"],
        "financial_currency": base["financial_currency"],
        "fx_to_trading_currency": fx,
        "confidence": confidence,
        "warnings": warnings,
        "price": px,
        "assumptions": a,
        "sources": base["sources"],
        "history": base["history"],
        "wacc": {**wc, "used": w, "overridden": bool((overrides or {}).get("wacc"))},
        "dcf": main,
        "market_implied_growth": implied_growth,
        "sensitivity": {"wacc": grid_w, "terminal_growth": grid_g, "value_per_share": sens},
        "comparables": comps,
        "football_field": ranges,
        "blended_fair_value": blended,
        "upside": upside,
        "view": view,
        "analysts": an,
        "method": "Blended value = median of the DCF value and each comparable-multiple midpoint (analyst targets are shown for reference, not blended).",
        "source": markets.SOURCE,
    }
