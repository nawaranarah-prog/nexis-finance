"""Paid portfolio analytics for My Nexis, computed from the member's own active investments and real market data.

* ``insights`` (Nexis Plus and Pro) — concentration, diversification, currency and asset-type exposure, and which
  positions drive the unrealised gain or loss. Uses the same delayed quotes and FX as the portfolio summary.
* ``risk`` (Nexis Pro) — one year of daily closes: volatility, maximum drawdown, beta against the S&P 500 and the
  correlations between holdings. Today's weights are applied to the past year (stated in the response).

Holdings without a price are listed as excluded instead of being guessed. Nothing here is investment advice.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import NexisError
from app.db.base import utcnow
from app.models import User
from app.services import markets, portfolio

BENCHMARK = "^GSPC"
NOTE = "Analytics use delayed public market data and are for information only, not investment advice."


def _positions(db: Session, user: User) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    s = portfolio.summary(db, user)
    by_sym: dict[str, dict[str, Any]] = {}
    for h in s["holdings"]:
        p = by_sym.setdefault(h["symbol"], {"symbol": h["symbol"], "name": h["name"], "asset_type": h["asset_type"], "currency": h["currency"],
                                            "value_usd": 0.0, "gain_usd": 0.0, "cost_known": True, "priced": True})  # fmt: skip
        if h["value_usd"] is None:
            p["priced"] = False
            continue
        p["value_usd"] += h["value_usd"]
        rate = h["value_usd"] / h["value"] if h["value"] else None
        if h["gain"] is not None and rate:
            p["gain_usd"] += h["gain"] * rate
        else:
            p["cost_known"] = False
    return list(by_sym.values()), s


def insights(db: Session, user: User) -> dict[str, Any]:
    pos, _s = _positions(db, user)
    priced = [p for p in pos if p["priced"] and p["value_usd"] > 0]
    total = sum(p["value_usd"] for p in priced)
    out: dict[str, Any] = {"as_of": utcnow().isoformat() + "Z", "positions": len(pos), "excluded": [p["symbol"] for p in pos if not p["priced"]],
                           "note": NOTE}  # fmt: skip
    if not priced or total <= 0:
        return {**out, "available": False, "reason": "Add an investment with a live price to see insights."}
    w = sorted(((p["symbol"], p["value_usd"] / total) for p in priced), key=lambda x: -x[1])
    hhi = sum(x * x for _, x in w)
    by_ccy: dict[str, float] = {}
    by_type: dict[str, float] = {}
    for p in priced:
        by_ccy[p["currency"] or "?"] = by_ccy.get(p["currency"] or "?", 0) + p["value_usd"]
        by_type[p["asset_type"]] = by_type.get(p["asset_type"], 0) + p["value_usd"]
    gains = sorted(([p["symbol"], p["gain_usd"]] for p in priced if p["cost_known"]), key=lambda x: -x[1])
    observations = []
    if w[0][1] >= 0.4 and len(w) > 1:
        observations.append(f"{w[0][0]} is {w[0][1]:.0%} of your portfolio — a large share in one position.")
    top_ccy = max(by_ccy.items(), key=lambda kv: kv[1])
    if len(by_ccy) > 1 and top_ccy[1] / total >= 0.8:
        observations.append(f"{top_ccy[1] / total:.0%} of your portfolio is in {top_ccy[0]}.")
    if len(w) >= 3 and 1 / hhi < len(w) / 2:
        observations.append(
            f"Your {len(w)} positions behave like about {1 / hhi:.1f} equally sized ones, because a few dominate."
        )
    return {
        **out, "available": True, "total_value_usd": total,
        "weights": [{"symbol": sym, "weight": x} for sym, x in w],
        "concentration": {"largest": {"symbol": w[0][0], "weight": w[0][1]}, "top3_weight": sum(x for _, x in w[:3]),
                          "effective_positions": 1 / hhi, "hhi": hhi},
        "currency_exposure": [{"currency": c, "weight": v / total} for c, v in sorted(by_ccy.items(), key=lambda kv: -kv[1])],
        "asset_types": [{"asset_type": t, "weight": v / total} for t, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
        "gain_drivers": {"top": [{"symbol": a, "gain_usd": g} for a, g in gains if g > 0][:3],
                         "bottom": [{"symbol": a, "gain_usd": g} for a, g in reversed(gains) if g < 0][:3],
                         "without_purchase_price": [p["symbol"] for p in priced if not p["cost_known"]]},
        "observations": observations,
    }  # fmt: skip


def _closes(db: Session, symbol: str) -> pd.Series | None:
    try:
        h = markets.history(db, symbol, "1d", (utcnow() - timedelta(days=372)).date(), None)
    except NexisError:
        return None
    bars = [b for b in h["bars"] if b.get("adj_close") is not None]
    if len(bars) < 40:
        return None
    idx = pd.to_datetime([b["t"] for b in bars], utc=True).tz_convert(None).normalize()
    ser = pd.Series([b["adj_close"] for b in bars], index=idx, dtype=float)
    return ser[~ser.index.duplicated(keep="last")]


def _num(x: float) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


def risk(db: Session, user: User) -> dict[str, Any]:
    pos, _ = _positions(db, user)
    priced = [p for p in pos if p["priced"] and p["value_usd"] > 0]
    out: dict[str, Any] = {"as_of": utcnow().isoformat() + "Z", "window": "1 year of daily closes", "benchmark": "S&P 500",
                           "method": "Today's weights applied to the past year's daily returns.", "note": NOTE}  # fmt: skip
    series = {p["symbol"]: _closes(db, p["symbol"]) for p in priced}
    have = {k: v for k, v in series.items() if v is not None}
    excluded = [p["symbol"] for p in pos if p["symbol"] not in have]
    if not have:
        return {**out, "available": False, "excluded": excluded, "reason": "Not enough price history for your investments yet."}
    total = sum(p["value_usd"] for p in priced if p["symbol"] in have)
    weights = {p["symbol"]: p["value_usd"] / total for p in priced if p["symbol"] in have}
    closes = pd.DataFrame(have).sort_index().ffill(limit=3)
    rets = closes.pct_change(fill_method=None).dropna(how="any")
    if len(rets) < 30:
        return {
            **out,
            "available": False,
            "excluded": excluded,
            "reason": "Not enough overlapping trading days across your investments.",
        }
    port = (rets * pd.Series(weights)).sum(axis=1)
    curve = (1 + port).cumprod()
    dd = curve / curve.cummax() - 1
    bench = _closes(db, BENCHMARK)
    beta = None
    if bench is not None:
        b = bench.pct_change(fill_method=None)
        joined = pd.concat([port.rename("p"), b.rename("b")], axis=1, join="inner").dropna()
        if len(joined) >= 30 and joined["b"].var() > 0:
            beta = joined["p"].cov(joined["b"]) / joined["b"].var()
    corr = rets.corr() if rets.shape[1] > 1 else None
    pairs = []
    if corr is not None:
        cols = list(corr.columns)
        pairs = sorted(({"a": cols[i], "b": cols[j], "correlation": _num(corr.iloc[i, j])} for i in range(len(cols)) for j in range(i + 1, len(cols))),
                       key=lambda x: -(x["correlation"] or 0))  # fmt: skip
    return {
        **out, "available": True, "excluded": excluded, "days": len(rets),
        "start": rets.index[0].date().isoformat(), "end": rets.index[-1].date().isoformat(),
        "portfolio": {"return_1y": _num(curve.iloc[-1] - 1), "volatility": _num(port.std() * math.sqrt(252)),
                      "max_drawdown": _num(dd.min()), "max_drawdown_date": dd.idxmin().date().isoformat(), "beta": _num(beta) if beta is not None else None,
                      "best_day": {"date": port.idxmax().date().isoformat(), "return": _num(port.max())},
                      "worst_day": {"date": port.idxmin().date().isoformat(), "return": _num(port.min())}},
        "holdings": [{"symbol": sym, "weight": weights[sym], "volatility": _num(rets[sym].std() * math.sqrt(252)),
                      "return_1y": _num((1 + rets[sym]).prod() - 1)} for sym in sorted(weights, key=lambda k: -weights[k])],
        "correlations": pairs[:10],
    }  # fmt: skip
