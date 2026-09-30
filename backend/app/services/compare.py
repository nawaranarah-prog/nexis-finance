"""Compare any instruments over any window: performance, risk, co-movement, periodic returns,
fundamentals and a transparent rule-based scorecard.

Returns are computed per instrument in its own currency from adjusted closes. Annualisation uses the
observed calendar span (so it is valid for hourly, daily, weekly and monthly bars alike).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConfigurationError, InsufficientDataError, ProviderError
from app.services import markets

BUCKETS = {"hour": "h", "day": "D", "week": "W-FRI", "month": "ME", "quarter": "QE", "year": "YE"}
BUCKET_LABEL = {
    "hour": "%Y-%m-%d %H:00",
    "day": "%Y-%m-%d",
    "week": "wk %Y-%m-%d",
    "month": "%b %Y",
    "year": "%Y",
}


def _bucket_label(t: pd.Timestamp, bucket: str) -> str:
    # strftime has no quarter directive.
    return f"{t.year} Q{(t.month - 1) // 3 + 1}" if bucket == "quarter" else t.strftime(BUCKET_LABEL[bucket])


# Scorecard weights (sum to 1). Categories without data for every instrument are dropped and the rest re-weighted.
WEIGHTS = {"momentum": 0.2, "risk_adjusted": 0.25, "resilience": 0.15, "valuation": 0.15, "analysts": 0.15, "income": 0.1}


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _metrics(p: pd.Series, rf: float) -> dict[str, Any]:
    p = p.dropna()
    if len(p) < 3:
        raise InsufficientDataError(f"{p.name}: fewer than 3 prices in the window")
    r = p.pct_change().dropna()
    days = max((p.index[-1] - p.index[0]).total_seconds() / 86400, 1e-9)
    years = days / 365.25
    total = p.iloc[-1] / p.iloc[0] - 1
    per_year = len(r) / years if years > 0 else np.nan
    vol = r.std(ddof=1) * math.sqrt(per_year) if len(r) > 1 else np.nan
    ann = (1 + total) ** (1 / years) - 1 if years >= 0.25 else np.nan  # annualising < 3 months is misleading
    # Risk-adjusted ratios over less than ~3 months are dominated by noise, so they are not reported.
    ratios_ok = years >= 0.25 and vol and not np.isnan(vol) and vol > 0
    wealth = p / p.iloc[0]
    dd = wealth / wealth.cummax() - 1
    trough = dd.idxmin()
    peak = wealth.loc[:trough].idxmax()
    downside = r[r < 0].std(ddof=1) * math.sqrt(per_year) if (r < 0).sum() > 1 else np.nan
    return {
        "start": p.index[0].isoformat(),
        "end": p.index[-1].isoformat(),
        "start_price": _f(p.iloc[0]),
        "end_price": _f(p.iloc[-1]),
        "observations": len(p),
        "total_return": _f(total),
        "annualized_return": _f(ann),
        "annualized_volatility": _f(vol),
        "sharpe_ratio": _f((ann - rf) / vol) if ratios_ok else None,
        "sortino_ratio": _f((ann - rf) / downside)
        if ratios_ok and downside and not np.isnan(downside) and downside > 0
        else None,
        "max_drawdown": _f(dd.min()),
        "max_drawdown_peak": peak.isoformat(),
        "max_drawdown_trough": trough.isoformat(),
        "best_period": _f(r.max()),
        "worst_period": _f(r.min()),
        "positive_periods": _f((r > 0).mean()),
        "high": _f(p.max()),
        "low": _f(p.min()),
    }


def _snapshot(d: dict[str, Any]) -> dict[str, Any]:
    q, v, a, dv = d["quote"], d["valuation"], d["analysts"], d["dividends"]
    lo, hi, px = q.get("week52_low"), q.get("week52_high"), q.get("price")
    return {
        "name": d["name"],
        "type": d["type"],
        "exchange": d["exchange"],
        "currency": d["currency"],
        "price": px,
        "market_cap": v.get("market_cap"),
        "pe_trailing": v.get("pe_trailing"),
        "pe_forward": v.get("pe_forward"),
        "price_to_book": v.get("price_to_book"),
        "ev_to_ebitda": v.get("ev_to_ebitda"),
        "dividend_yield": dv.get("yield"),
        "beta": v.get("beta"),
        "week52_position": (px - lo) / (hi - lo) if px is not None and lo is not None and hi and hi > lo else None,
        "analyst_recommendation": a.get("recommendation"),
        "analyst_mean": a.get("recommendation_mean"),
        "analyst_count": a.get("count"),
        "analyst_target": a.get("target_mean"),
        "analyst_upside": a.get("upside_to_mean_target"),
        "sector": d["profile"].get("sector"),
        "industry": d["profile"].get("industry"),
    }


def _rank_score(values: dict[str, float | None], higher_better: bool = True) -> dict[str, float] | None:
    vals = {k: v for k, v in values.items() if v is not None}
    if len(vals) < len(values) or len(vals) < 2:
        return None
    n = len(vals)
    sign = 1 if higher_better else -1
    out = {}
    for k, v in vals.items():
        # Ties share the average rank, so equal values always get equal scores.
        worse = sum(1 for o in vals.values() if sign * o < sign * v)
        equal = sum(1 for o in vals.values() if o == v) - 1
        out[k] = 100.0 * (worse + equal / 2) / (n - 1)
    return out


def scorecard(metrics: dict[str, dict[str, Any]], snaps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    syms = list(metrics)
    pe = {s: (snaps[s].get("pe_forward") or snaps[s].get("pe_trailing")) for s in syms}
    pe = {s: (v if v is not None and v > 0 else None) for s, v in pe.items()}
    raw = {
        "momentum": _rank_score({s: metrics[s]["total_return"] for s in syms}),
        "risk_adjusted": _rank_score({s: metrics[s]["sharpe_ratio"] for s in syms}),
        "resilience": _rank_score({s: metrics[s]["max_drawdown"] for s in syms}),
        "valuation": _rank_score(pe, higher_better=False),
        "analysts": _rank_score({s: snaps[s].get("analyst_mean") for s in syms}, higher_better=False),
        # Equities without a dividend genuinely yield 0; for other instruments a missing yield is unknown.
        "income": _rank_score(
            {
                s: (snaps[s].get("dividend_yield") or 0.0) if snaps[s].get("type") == "equity" else snaps[s].get("dividend_yield")
                for s in syms
            }
        ),
    }
    used = {k: v for k, v in raw.items() if v is not None}
    if not used:
        return {"available": False, "reason": "not enough comparable data"}
    wsum = sum(WEIGHTS[k] for k in used)
    comp = {s: sum(WEIGHTS[k] * used[k][s] for k in used) / wsum for s in syms}
    ranking = sorted(syms, key=lambda s: -comp[s])
    strengths: dict[str, list[str]] = {s: [] for s in syms}
    weaknesses: dict[str, list[str]] = {s: [] for s in syms}
    for s in syms:
        m, sn = metrics[s], snaps[s]
        best = lambda k, s=s: k in used and used[k][s] == 100  # noqa: E731
        worst = lambda k, s=s: k in used and used[k][s] == 0  # noqa: E731
        if best("momentum"):
            strengths[s].append(f"strongest return in the window ({m['total_return']:+.1%})")
        if worst("momentum"):
            weaknesses[s].append(f"weakest return in the window ({m['total_return']:+.1%})")
        if best("risk_adjusted") and m["sharpe_ratio"] is not None:
            strengths[s].append(f"best risk-adjusted return (Sharpe {m['sharpe_ratio']:.2f})")
        if worst("risk_adjusted") and m["sharpe_ratio"] is not None:
            weaknesses[s].append(f"worst risk-adjusted return (Sharpe {m['sharpe_ratio']:.2f})")
        if best("resilience"):
            strengths[s].append(f"shallowest drawdown ({m['max_drawdown']:.1%})")
        if worst("resilience"):
            weaknesses[s].append(f"deepest drawdown ({m['max_drawdown']:.1%})")
        if best("valuation"):
            strengths[s].append(f"lowest P/E in the group ({pe[s]:.1f}x)")
        if worst("valuation"):
            weaknesses[s].append(f"highest P/E in the group ({pe[s]:.1f}x)")
        if best("analysts") and sn.get("analyst_recommendation"):
            strengths[s].append(
                f"most favourable analyst consensus ({sn['analyst_recommendation'].replace('_', ' ')}, {int(sn['analyst_count'] or 0)} analysts)"
            )
        if worst("analysts") and sn.get("analyst_recommendation"):
            weaknesses[s].append(f"least favourable analyst consensus ({sn['analyst_recommendation'].replace('_', ' ')})")
        if best("income") and sn.get("dividend_yield"):
            strengths[s].append(f"highest dividend yield ({sn['dividend_yield']:.1%})")
        if sn.get("analyst_upside") is not None:
            (strengths if sn["analyst_upside"] > 0 else weaknesses)[s].append(
                f"{sn['analyst_upside']:+.1%} to the mean analyst target"
            )
    return {
        "available": True,
        "weights": {k: WEIGHTS[k] / wsum for k in used},
        "dropped": [k for k in raw if k not in used],
        "category_scores": used,
        "composite": comp,
        "ranking": ranking,
        "reasons": strengths,
        "weaknesses": weaknesses,
        "method": "Each category ranks the instruments from 0 (worst in group) to 100 (best); the composite is the weighted mean. "
        "Categories are dropped when any instrument lacks the data. Relative to this group and window only.",
    }


def compare(
    db: Session,
    symbols: list[str],
    period: str | None = "1y",
    start: date | None = None,
    end: date | None = None,
    interval: str | None = None,
    bucket: str | None = None,
    risk_free_rate: float | None = None,
) -> dict[str, Any]:
    syms = list(dict.fromkeys(markets.clean_symbol(s) for s in symbols))
    if not 1 <= len(syms) <= 8:
        raise ConfigurationError("choose between 1 and 8 instruments")
    s0, e0, iv = markets.resolve_window(period, start, end, interval)
    rf = get_settings().risk_free_rate if risk_free_rate is None else risk_free_rate
    frame, metas = markets.close_frame(db, syms, iv, s0, e0)
    if s0 is not None:
        frame = frame[frame.index >= pd.Timestamp(s0, tz=frame.index.tz)]
    metrics = {s: _metrics(frame[s].rename(s), rf) for s in syms}

    rebased = frame.apply(lambda c: c / c.dropna().iloc[0] * 100 if c.notna().any() else c)
    # Different exchanges have different holidays/weekends: carry the last price across short gaps for display only.
    rebased = rebased.ffill(limit=5)
    step = max(1, len(rebased) // 600)
    chart = rebased.iloc[::step]
    if chart.index[-1] != rebased.index[-1]:
        chart = pd.concat([chart, rebased.iloc[[-1]]])

    rets = frame.pct_change(fill_method=None)
    corr = rets.corr(min_periods=10)
    bench = syms[0]
    for s in syms[1:]:
        pair = rets[[s, bench]].dropna()
        metrics[s]["correlation_to_first"] = _f(pair[s].corr(pair[bench])) if len(pair) > 10 else None
        metrics[s]["beta_to_first"] = (
            _f(pair[s].cov(pair[bench]) / pair[bench].var()) if len(pair) > 10 and pair[bench].var() > 0 else None
        )

    bucket = bucket or {"1h": "day", "1d": "month", "1wk": "month", "1mo": "year"}[iv]
    if bucket not in BUCKETS:
        raise ConfigurationError(f"unknown bucket '{bucket}' (use one of {', '.join(BUCKETS)})")
    if bucket == "hour" and iv != "1h":
        raise ConfigurationError("hourly buckets need hourly data — choose the 1h interval")
    idx = frame.index.tz_convert(None) if frame.index.tz is not None else frame.index
    last = frame.set_axis(idx).resample(BUCKETS[bucket]).last()
    first_price = frame.set_axis(idx).apply(lambda c: c.dropna().iloc[0])
    per = last.pct_change(fill_method=None)
    per.iloc[0] = last.iloc[0] / first_price - 1  # first bucket measured from the window's first price
    per = per.dropna(how="all").tail(60)
    periodic = [{"period": _bucket_label(t, bucket), **{s: _f(per.at[t, s]) for s in syms}} for t in per.index]

    snaps: dict[str, dict[str, Any]] = {}
    for s in syms:
        try:
            snaps[s] = _snapshot(markets.details(db, s))
        except ProviderError as exc:
            m = metas[s]
            snaps[s] = {
                "name": m.get("longName") or s,
                "type": (m.get("instrumentType") or "").lower(),
                "currency": m.get("currency"),
                "error": exc.message,
            }
    currencies = sorted({(snaps[s].get("currency") or "?") for s in syms})
    return {
        "symbols": syms,
        "interval": iv,
        "bucket": bucket,
        "window": {
            "period": period if not (start or end) else None,
            "start": metrics[bench]["start"],
            "end": metrics[bench]["end"],
        },
        "risk_free_rate": rf,
        "currencies": currencies,
        "currency_note": None
        if len(currencies) == 1
        else "Instruments trade in different currencies; returns are in each instrument's own currency (AED is pegged to USD, so AED vs USD returns are directly comparable).",
        "chart": {"t": [t.isoformat() for t in chart.index], "series": {s: [_f(v) for v in chart[s]] for s in syms}},
        "metrics": metrics,
        "correlation": {s: {t: _f(corr.at[s, t]) for t in syms} for s in syms},
        "periodic_returns": periodic,
        "snapshots": snaps,
        "scorecard": scorecard(metrics, snaps)
        if len(syms) > 1
        else {"available": False, "reason": "add a second instrument to rank"},
        "source": markets.SOURCE,
    }
