"""Time Machine: "what happens to my holdings if…" — deterministic, explainable scenario arithmetic. Not a forecast.

Four methods, each named on every result line:

* ``explicit``  — the member states a move for a holding ("Emaar −15%"); applied as given.
* ``factor``    — measured sensitivity. Weekly returns of the holding are regressed (OLS) on the weekly moves of the
  factors the member shocks (oil, S&P 500, DFM index, gold, EUR/USD, US 10-year yield) over a stated window. The
  impact is ``Σ beta × shock`` with a 95% range from the betas' covariance. Shocks bigger than any weekly move in the
  window are flagged as extrapolation; low R² is flagged as a weak relationship. Weekly (Friday) closes are used
  because UAE and US markets trade on different days.
* ``duration``  — bonds with known coupon, maturity and yield: ``ΔP/P ≈ −D·Δy + ½·C·Δy²``.
* ``dcf``       — valuation sensitivity: the existing DCF re-run with the member's growth / discount-rate changes.

Nothing is random (no Monte Carlo): the same inputs and the same data give the same answer, and every run stores the
data it used. ``MODEL_VERSION`` changes whenever the arithmetic changes, so old runs stay interpretable.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import NexisError, ValidationFailed
from app.db.base import utcnow
from app.models import User
from app.services import markets, portfolio_analytics, uae, valuation

MODEL_VERSION = "tm-1.0"
# key -> (market symbol, label, unit). Rates are shocked in basis points of yield; everything else in percent.
FACTORS: dict[str, tuple[str, str, str]] = {
    "oil": ("BZ=F", "Brent crude oil", "pct"),
    "market_us": ("^GSPC", "S&P 500", "pct"),
    "market_uae": ("DFMGI.AE", "DFM General Index", "pct"),
    "gold": ("GC=F", "Gold", "pct"),
    "eurusd": ("EURUSD=X", "Euro vs US dollar", "pct"),
    "rates_us10y": ("^TNX", "US 10-year Treasury yield", "bp"),
}
WINDOWS = (52, 104, 156)  # weeks of history used for the regressions
MIN_WEEKS = 26
MAX_POSITIONS = 30
Closes = Callable[[str, int], "pd.Series | None"]


# ---------------------------------------------------------------------------------------------- data


def market_closes(db: Session) -> Closes:
    """Daily adjusted closes for ``symbol`` covering at least ``weeks`` weeks (None when unavailable)."""

    def get(symbol: str, weeks: int) -> pd.Series | None:
        try:
            h = markets.history(db, symbol, "1d", (utcnow() - timedelta(weeks=weeks + 3)).date(), None)
        except NexisError:
            return None
        bars = [b for b in h["bars"] if b.get("adj_close") is not None]
        if not bars:
            return None
        idx = pd.to_datetime([b["t"] for b in bars], utc=True).tz_convert(None).normalize()
        s = pd.Series([b["adj_close"] for b in bars], index=idx, dtype=float)
        return s[~s.index.duplicated(keep="last")].sort_index()

    return get


def weekly_moves(closes: pd.Series, unit: str) -> pd.Series:
    """Friday-to-Friday moves: percent for prices, basis points of yield for rates (^TNX quotes the yield in %)."""
    w = closes.resample("W-FRI").last().dropna()
    return (w.diff() * 100).dropna() if unit == "bp" else w.pct_change().dropna()


# ---------------------------------------------------------------------------------------------- inputs


def validate(inputs: dict[str, Any]) -> dict[str, Any]:
    """Normalise and bound a scenario. Shocks: percent as a number (−30 = −30%), rates in basis points."""
    src = inputs.get("holdings", "custom")
    if src not in ("mine", "custom"):
        raise ValidationFailed("holdings must be 'mine' or 'custom'.")
    positions = []
    if src == "custom":
        for p in (inputs.get("positions") or [])[:MAX_POSITIONS]:
            sym = markets.clean_symbol(str(p.get("symbol", "")))
            amt = float(p.get("amount") or 0)
            if amt <= 0 or amt > 1e12:
                raise ValidationFailed(f"Amount for {sym} must be positive.")
            positions.append({"symbol": sym, "amount": amt})
    factors = {}
    for k, v in (inputs.get("factor_shocks") or {}).items():
        if k not in FACTORS:
            raise ValidationFailed(f"Unknown factor: {k}.")
        if v is None or v == "":
            continue
        v = float(v)
        lim = 500 if FACTORS[k][2] == "bp" else 90
        if not -lim <= v <= lim:
            raise ValidationFailed(f"{FACTORS[k][1]}: shock must be between −{lim} and {lim}.")
        if v != 0:
            factors[k] = v
    explicit = {}
    for sym, v in (inputs.get("asset_shocks") or {}).items():
        v = float(v)
        if not -100 <= v <= 300:
            raise ValidationFailed(f"{sym}: a move must be between −100% and +300%.")
        explicit[markets.clean_symbol(sym)] = v
    weeks = int(inputs.get("window_weeks") or 104)
    if weeks not in WINDOWS:
        raise ValidationFailed(f"window_weeks must be one of {WINDOWS}.")
    val = inputs.get("valuation") or None
    if val:
        val = {
            "symbol": markets.clean_symbol(str(val.get("symbol", ""))),
            "growth_change": _bounded(val.get("growth_change"), -10, 10, "Growth change"),  # percentage points
            "wacc_change": _bounded(val.get("wacc_change"), -5, 5, "Discount-rate change"),  # percentage points
            "terminal_growth_change": _bounded(val.get("terminal_growth_change"), -2, 2, "Terminal growth change"),
        }
    if not factors and not explicit and not val:
        raise ValidationFailed("Set at least one shock or a valuation change.")
    if src == "custom" and not positions and (factors or explicit):
        raise ValidationFailed("Add at least one holding.")  # a valuation check alone needs no holdings
    return {"holdings": src, "positions": positions, "factor_shocks": factors, "asset_shocks": explicit, "window_weeks": weeks,
            "valuation": val}  # fmt: skip


def _bounded(v: Any, lo: float, hi: float, label: str) -> float:
    v = float(v or 0)
    if not lo <= v <= hi:
        raise ValidationFailed(f"{label} must be between {lo} and {hi} percentage points.")
    return v


# ---------------------------------------------------------------------------------------------- the models


def regress(y: pd.Series, xs: pd.DataFrame) -> dict[str, Any] | None:
    """OLS of ``y`` on ``xs`` with an intercept. Returns betas, their covariance, R², n — or None if too little data."""
    df = pd.concat([y.rename("_y"), xs], axis=1, join="inner").dropna()
    n, k = len(df), xs.shape[1]
    if n < max(MIN_WEEKS, k + 5):
        return None
    X = np.column_stack([np.ones(n), df[xs.columns].to_numpy()])
    Y = df["_y"].to_numpy()
    xtx_inv = np.linalg.pinv(X.T @ X)
    b = xtx_inv @ X.T @ Y
    resid = Y - X @ b
    dof = n - k - 1
    s2 = float(resid @ resid) / dof
    tss = float(((Y - Y.mean()) ** 2).sum())
    cov = s2 * xtx_inv
    return {
        "beta": {c: float(b[i + 1]) for i, c in enumerate(xs.columns)},
        "se": {c: float(math.sqrt(max(cov[i + 1, i + 1], 0))) for i, c in enumerate(xs.columns)},
        "cov": cov[1:, 1:],
        "r2": 1 - float(resid @ resid) / tss if tss > 0 else 0.0,
        "n": n,
        "start": df.index[0].date().isoformat(),
        "end": df.index[-1].date().isoformat(),
        "max_abs_move": {c: float(df[c].abs().max()) for c in xs.columns},
    }


def largest_move(closes: pd.Series, unit: str, weeks: int = 13) -> float:
    """The biggest move over any ``weeks``-week span in the data (fraction for prices, basis points for rates)."""
    w = closes.resample("W-FRI").last().dropna()
    span = (w - w.shift(weeks)) * 100 if unit == "bp" else w / w.shift(weeks) - 1
    return float(span.abs().max()) if span.notna().any() else 0.0


def factor_impact(reg: dict[str, Any], shocks: dict[str, float], largest: dict[str, float] | None = None) -> dict[str, Any]:
    """Impact (in %) of factor ``shocks`` given a regression, with a 95% range from the betas' covariance.

    ``largest``: the biggest 3-month move of each factor in the window. A scenario move is compared with that (not
    with single weeks — nobody means "oil −30% in one week"); beyond it the result is flagged as extrapolation."""
    keys = list(reg["beta"])
    x = np.array([shocks[k] / 100 if FACTORS[k][2] == "pct" else shocks[k] for k in keys])  # pct as fraction, bp as bp
    beta = np.array([reg["beta"][k] for k in keys])
    mid = float(x @ beta)
    se = float(math.sqrt(max(x @ reg["cov"] @ x, 0)))
    flags = []
    for k in keys:
        size = abs(shocks[k] / 100 if FACTORS[k][2] == "pct" else shocks[k])
        limit, span = ((largest or {}).get(k), "3-month") if (largest or {}).get(k) else (reg["max_abs_move"][k], "weekly")
        if size > limit * 1.0001:
            flags.append(f"The {FACTORS[k][1]} move is larger than any {span} move in the data, so this extrapolates.")
    low, high = (mid - 1.96 * se) * 100, (mid + 1.96 * se) * 100
    if low < 0 < high:
        flags.append("The 95% range includes zero: the data can't tell whether this move would push it up or down.")
    if reg["r2"] < 0.2:
        flags.append(f"Weak relationship: these factors explain only {reg['r2']:.0%} of its weekly moves (R² {reg['r2']:.2f}).")
    return {"pct": mid * 100, "low": low, "high": high, "flags": flags}


def duration_convexity(coupon: float, ytm: float, years: float, freq: int = 2) -> tuple[float, float]:
    """Modified duration and convexity (annualised) of a plain fixed-coupon bond, per 1 of yield."""
    n = max(1, math.ceil(years * freq - 1e-9))
    c, y = coupon / freq, ytm / freq
    times = [(years - (n - i) / freq) for i in range(1, n + 1)]  # years to each payment, last one at maturity
    flows = [c] * (n - 1) + [c + 1]
    disc = [(1 + y) ** -(t * freq) for t in times]
    price = sum(f * d for f, d in zip(flows, disc, strict=True))
    mac = sum(t * f * d for t, f, d in zip(times, flows, disc, strict=True)) / price
    mod = mac / (1 + y)
    conv = sum(f * d * (t * freq) * (t * freq + 1) for t, f, d in zip(times, flows, disc, strict=True)) / (
        price * (1 + y) ** 2 * freq**2
    )
    return mod, conv


def bond_impact(coupon: float, ytm: float, years: float, bp: float) -> dict[str, Any]:
    mod, conv = duration_convexity(coupon, ytm, years)
    dy = bp / 10_000
    pct = (-mod * dy + 0.5 * conv * dy * dy) * 100
    return {"pct": pct, "low": pct, "high": pct, "duration": mod, "convexity": conv,
            "flags": [] if abs(bp) <= 200 else ["Duration/convexity is an approximation; it loosens for moves beyond ±200bp."]}  # fmt: skip


def valuation_change(db: Session, v: dict[str, Any]) -> dict[str, Any]:
    """The existing DCF with defaults, then with the member's changes (growth, discount rate, terminal growth)."""
    base = valuation.defaults(db, v["symbol"])
    a = dict(base["assumptions"])
    fx = valuation._fx(db, base["financial_currency"], base["currency"])
    w0 = valuation.wacc(a)["wacc"]
    g0 = a["terminal_growth"]
    before = valuation.dcf(a, w0, g0, fx)["value_per_share"]
    a2 = {**a, "growth_start": a["growth_start"] + v["growth_change"] / 100}
    w1, g1 = w0 + v["wacc_change"] / 100, g0 + v["terminal_growth_change"] / 100
    if w1 <= g1:
        raise ValidationFailed("The discount rate must stay above terminal growth.")
    after = valuation.dcf(a2, w1, g1, fx)["value_per_share"]
    flags = []
    if valuation._is_financial(base["details"]):
        flags.append("For banks and other financials a free-cash-flow DCF is not meaningful.")
    return {
        "symbol": base["symbol"], "name": base["name"], "currency": base["currency"], "price": base["price"],
        "before": before, "after": after, "change_pct": (after / before - 1) * 100 if before else None,
        "assumptions": {"growth_start": [a["growth_start"], a2["growth_start"]], "wacc": [w0, w1], "terminal_growth": [g0, g1]},
        "flags": flags,
    }  # fmt: skip


# ---------------------------------------------------------------------------------------------- a run


def _holdings(db: Session, user: User, s: dict[str, Any]) -> tuple[list[dict[str, Any]], str, list[str]]:
    if s["holdings"] == "custom":
        return [{"symbol": p["symbol"], "name": p["symbol"], "value": p["amount"]} for p in s["positions"]], "as entered", []
    pos, _ = portfolio_analytics._positions(db, user)
    priced = [
        {"symbol": p["symbol"], "name": p["name"], "value": p["value_usd"]} for p in pos if p["priced"] and p["value_usd"] > 0
    ]
    unpriced = [p["symbol"] for p in pos if not p["priced"]]
    if not priced:
        raise ValidationFailed("You have no priced investments in My Nexis — add some, or enter holdings for this scenario.")
    return priced[:MAX_POSITIONS], "USD", unpriced


def run(db: Session, user: User, inputs: dict[str, Any], closes: Closes | None = None) -> dict[str, Any]:
    """Compute a scenario. Returns ``{inputs, snapshot, output, model_version}``; nothing is stored here."""
    s = validate(inputs)
    get = closes or market_closes(db)
    moves_set = bool(s["factor_shocks"] or s["asset_shocks"])
    holdings, currency, unpriced = _holdings(db, user, s) if moves_set else ([], "as entered", [])
    weeks = s["window_weeks"]
    snapshot: dict[str, Any] = {"series": {}, "computed_at": utcnow().isoformat() + "Z", "window_weeks": weeks}

    def moves(symbol: str, unit: str) -> pd.Series | None:
        c = get(symbol, weeks)
        if c is None or len(c) < 10:
            snapshot["series"][symbol] = None
            return None
        c = c[c.index >= c.index[-1] - pd.Timedelta(weeks=weeks)]
        snapshot["series"][symbol] = {"first": c.index[0].date().isoformat(), "last": c.index[-1].date().isoformat(),
                                      "last_close": float(c.iloc[-1]), "days": len(c),
                                      "largest_13w_move": largest_move(c, unit)}  # fmt: skip
        return weekly_moves(c, unit)

    fx = {k: moves(FACTORS[k][0], FACTORS[k][2]) for k in s["factor_shocks"]}
    missing_factors = [FACTORS[k][1] for k, v in fx.items() if v is None]
    usable = {k: v for k, v in fx.items() if v is not None}
    xs = pd.DataFrame(usable) if usable else None

    lines, total, low, high = [], 0.0, 0.0, 0.0
    for h in holdings:
        sym, value = h["symbol"], h["value"]
        line: dict[str, Any] = {"symbol": sym, "name": h["name"], "value": value, "flags": []}
        if sym in s["asset_shocks"]:
            pct = s["asset_shocks"][sym]
            line.update(method="explicit", pct=pct, low=pct, high=pct)
        elif sym.endswith(".BOND") and "rates_us10y" in s["factor_shocks"]:
            b = _bond(db, sym)
            if b is None:
                line.update(method="none", pct=None, flags=["No coupon, maturity and yield for this bond, so no rate impact."])
            else:
                imp = bond_impact(b["coupon"], b["yield_to_maturity"], b["years_to_maturity"], s["factor_shocks"]["rates_us10y"])
                line.update(method="duration", pct=imp["pct"], low=imp["low"], high=imp["high"], flags=imp["flags"],
                            stats={"modified_duration": imp["duration"], "convexity": imp["convexity"],
                                   "assumes": "the bond's yield moves by the same basis points as the US 10-year"})  # fmt: skip
        elif xs is not None:
            y = moves(sym, "pct")
            reg = regress(y, xs) if y is not None else None
            if reg is None:
                line.update(
                    method="none",
                    pct=None,
                    flags=[f"Not enough price history (at least {MIN_WEEKS} weeks) to measure sensitivity."],
                )
            else:
                largest = {k: snapshot["series"][FACTORS[k][0]]["largest_13w_move"] for k in usable}
                imp = factor_impact(reg, s["factor_shocks"], largest)
                line.update(method="factor", pct=imp["pct"], low=imp["low"], high=imp["high"], flags=imp["flags"],
                            stats={"beta": reg["beta"], "se": reg["se"], "r2": reg["r2"], "weeks": reg["n"],
                                   "from": reg["start"], "to": reg["end"]})  # fmt: skip
        else:
            line.update(method="none", pct=None, flags=["No shock applies to this holding."])
        if line.get("pct") is not None:
            line["change"] = value * line["pct"] / 100
            total += line["change"]
            low += value * line["low"] / 100
            high += value * line["high"] / 100
        lines.append(line)

    covered = sum(h["value"] for h, ln in zip(holdings, lines, strict=True) if ln.get("pct") is not None)
    value = sum(h["value"] for h in holdings)
    output = {
        "currency": currency,
        "portfolio": {
            "value": value,
            "change": total,
            "change_pct": total / value * 100 if value else None,
            "wide_low": low,
            "wide_high": high,
            "covered_value": covered,
        },
        "lines": lines,
        "unpriced": unpriced,
        "missing_factors": missing_factors,
        "valuation": valuation_change(db, s["valuation"]) if s["valuation"] else None,
        "notes": [
            "Hypothetical scenario, not a forecast: it applies the moves you chose; it doesn't say how likely they are.",
            "Factor results use past weekly co-movement and assume it scales to the size of move you chose; that "
            "relationship can change. The range shows only the statistical uncertainty of the measurement, not every "
            "way things could turn out.",
            "The portfolio range adds each holding's own 95% bounds, so it is deliberately wide.",
        ],
    }
    return {"inputs": s, "snapshot": snapshot, "output": output, "model_version": MODEL_VERSION}


def _bond(db: Session, symbol: str) -> dict[str, Any] | None:
    try:
        b = uae.bond(db, symbol)
    except NexisError:
        return None
    if not all(b.get(k) is not None for k in ("coupon", "yield_to_maturity", "years_to_maturity")) or b["years_to_maturity"] <= 0:
        return None
    return b


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Two stored outputs side by side: the portfolio change and each holding's change, and what differs."""
    la = {ln["symbol"]: ln for ln in a["lines"]}
    lb = {ln["symbol"]: ln for ln in b["lines"]}
    rows = []
    for sym in list(dict.fromkeys([*la, *lb])):
        pa, pb = (la.get(sym) or {}).get("pct"), (lb.get(sym) or {}).get("pct")
        rows.append({"symbol": sym, "a": pa, "b": pb, "diff": (pb - pa) if pa is not None and pb is not None else None})
    pa, pb = a["portfolio"]["change_pct"], b["portfolio"]["change_pct"]
    return {"portfolio": {"a": pa, "b": pb, "diff": (pb - pa) if pa is not None and pb is not None else None}, "lines": rows}
