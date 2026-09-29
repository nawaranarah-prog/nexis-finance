"""Asset research, correlation and factor analytics endpoints' business logic."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.correlation import correlation_matrix, highly_correlated_pairs, rolling_average_correlation
from app.analytics.factors import FACTOR_DEFINITIONS, factor_exposure, factor_returns, rolling_factor_betas
from app.analytics.rolling import (
    histogram,
    moving_average,
    return_distribution,
    rolling_beta,
    rolling_correlation,
    rolling_volatility,
)
from app.core.config import get_settings
from app.core.errors import ConfigurationError, InsufficientDataError
from app.services.market_data import load_panel
from app.services.portfolios import get_portfolio, simulate

ROLLING_WINDOWS = (20, 60, 120, 252)
_factor_cache: OrderedDict[tuple[int, int], tuple[pd.DataFrame, dict[str, Any]]] = OrderedDict()
_factor_lock = Lock()


def _slice(df: pd.DataFrame | pd.Series, start: date | None, end: date | None) -> Any:
    return df.loc[pd.Timestamp(start) if start else None : pd.Timestamp(end) if end else None]


def _l(s: pd.Series) -> list[float | None]:
    return [None if not np.isfinite(v) else float(v) for v in s.to_numpy(dtype=float)]


def asset_research(
    db: Session,
    dataset_id: int,
    symbol: str,
    benchmark: str | None,
    start: date | None,
    end: date | None,
    window: int = 60,
    frequency: str = "daily",
) -> dict[str, Any]:
    if window not in ROLLING_WINDOWS:
        raise ConfigurationError(f"window must be one of {ROLLING_WINDOWS}")
    if frequency not in ("daily", "weekly", "monthly"):
        raise ConfigurationError("frequency must be daily, weekly or monthly")
    panel = load_panel(db, dataset_id)
    panel.require([symbol])
    bench = benchmark or (panel.benchmarks[0] if panel.benchmarks else None)
    if bench:
        panel.require([bench])
    rf = get_settings().risk_free_rate
    close = _slice(panel.adj_close[symbol], start, end).dropna()
    if len(close) < 3:
        raise InsufficientDataError(f"{symbol} has fewer than 3 observations in the selected window")
    idx = close.index
    r = close.pct_change(fill_method=None).dropna()
    b_close = panel.adj_close[bench].reindex(idx) if bench else None
    b_r = b_close.pct_change(fill_method=None) if b_close is not None else None
    summary = M.performance_summary(r, b_r, rf) if len(r) >= 2 else {}
    dist = return_distribution(r, frequency)
    info = panel.assets[symbol]
    missing = int(panel.close[symbol].loc[idx[0] : idx[-1]].isna().sum())
    return {
        "symbol": symbol,
        "name": info["name"],
        "asset_type": info["asset_type"],
        "sector": info["sector"],
        "attributes": info["attributes"],
        "benchmark": bench,
        "window": window,
        "frequency": frequency,
        "summary": summary,
        "max_drawdown_details": M.max_drawdown_details(r) if len(r) else None,
        "missing_bars_in_window": missing,
        "series": {
            "dates": [d.date().isoformat() for d in idx],
            "open": _l(panel.open[symbol].reindex(idx)),
            "high": _l(panel.high[symbol].reindex(idx)),
            "low": _l(panel.low[symbol].reindex(idx)),
            "close": _l(panel.close[symbol].reindex(idx)),
            "adj_close": _l(close),
            "volume": _l(panel.volume[symbol].reindex(idx)),
            "ma_50": _l(moving_average(close, 50)),
            "ma_200": _l(moving_average(close, 200)),
            "returns": _l(close.pct_change(fill_method=None)),
            "cumulative_return": _l(close / close.iloc[0] - 1),
            "benchmark_cumulative_return": _l(b_close / b_close.dropna().iloc[0] - 1)
            if b_close is not None and b_close.notna().any()
            else None,
            "drawdown": _l(M.drawdown_series(close.pct_change(fill_method=None).fillna(0))),
            "rolling_volatility": _l(rolling_volatility(close.pct_change(fill_method=None), window)),
            "rolling_beta": _l(rolling_beta(close.pct_change(fill_method=None), b_r, window).reindex(idx))
            if b_r is not None
            else None,
            "rolling_correlation": _l(rolling_correlation(close.pct_change(fill_method=None), b_r, window).reindex(idx))
            if b_r is not None
            else None,
        },
        "distribution": {
            "frequency": frequency,
            "histogram": histogram(dist, 50 if frequency == "daily" else 30),
            "mean": float(dist.mean()) if len(dist) else None,
            "std": float(dist.std()) if len(dist) > 1 else None,
            "count": len(dist),
        },
    }


def asset_comparison(db: Session, dataset_id: int, symbols: list[str], start: date | None, end: date | None) -> dict[str, Any]:
    panel = load_panel(db, dataset_id)
    panel.require(symbols)
    rf = get_settings().risk_free_rate
    px = _slice(panel.adj_close[symbols], start, end)
    out = []
    for s in symbols:
        r = px[s].pct_change(fill_method=None).dropna()
        if len(r) < 2:
            continue
        m = M.performance_summary(r, None, rf)
        out.append(
            {
                "symbol": s,
                "name": panel.assets[s]["name"],
                "sector": panel.assets[s]["sector"],
                **{
                    k: m[k]
                    for k in ("cumulative_return", "annualized_return", "annualized_volatility", "sharpe_ratio", "max_drawdown")
                },
            }
        )
    norm = px.apply(lambda c: c / c.dropna().iloc[0] if c.notna().any() else c)
    return {"assets": out, "dates": [d.date().isoformat() for d in norm.index], "normalized": {s: _l(norm[s]) for s in symbols}}


def correlation(
    db: Session,
    dataset_id: int,
    symbols: list[str] | None,
    start: date | None,
    end: date | None,
    method: str = "pearson",
    threshold: float = 0.8,
    rolling_window: int = 60,
    pair: tuple[str, str] | None = None,
) -> dict[str, Any]:
    panel = load_panel(db, dataset_id)
    symbols = symbols or panel.tradable
    panel.require(symbols)
    if len(symbols) > 80:
        raise ConfigurationError("select at most 80 assets for a correlation matrix")
    if not 20 <= rolling_window <= 504:
        raise ConfigurationError("rolling_window must be between 20 and 504")
    r = panel.returns(symbols, start, end)
    mat = correlation_matrix(r, method=method)
    pairs = highly_correlated_pairs(r, threshold)
    avg = rolling_average_correlation(r, rolling_window) if len(r) > rolling_window else pd.Series(dtype=float)
    out = {
        "matrix": mat,
        "high_pairs": pairs,
        "threshold": threshold,
        "rolling_window": rolling_window,
        "rolling_average": {"dates": [d.date().isoformat() for d in avg.index], "values": _l(avg)},
        "missing_observations": {s: int(r[s].isna().sum()) for s in symbols},
    }
    if pair:
        a, b = pair
        panel.require([a, b])
        rr = panel.returns([a, b], start, end)
        rc = rolling_correlation(rr[a], rr[b], rolling_window)
        out["pair"] = {"a": a, "b": b, "dates": [d.date().isoformat() for d in rc.index], "values": _l(rc)}
    return out


def _factors(db: Session, dataset_id: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    panel = load_panel(db, dataset_id)
    key = (dataset_id, panel.version)
    with _factor_lock:
        if key in _factor_cache:
            return _factor_cache[key]
    uni = panel.tradable
    f, meta = factor_returns(panel.adj_close[uni], panel.volume[uni])
    with _factor_lock:
        _factor_cache[key] = (f, meta)
        while len(_factor_cache) > 8:
            _factor_cache.popitem(last=False)
    return f, meta


def factor_analytics(
    db: Session, dataset_id: int, portfolio_id: int | None, start: date | None, end: date | None, rolling_window: int = 126
) -> dict[str, Any]:
    f, meta = _factors(db, dataset_id)
    f = _slice(f, start, end).dropna()
    if len(f) < 60:
        raise InsufficientDataError("fewer than 60 days of factor returns in the selected window")
    rf = get_settings().risk_free_rate
    perf = []
    for c in f.columns:
        s = M.performance_summary(f[c], None, rf)
        perf.append(
            {
                "factor": c,
                "definition": FACTOR_DEFINITIONS[c],
                "formation_months": meta["formation_months"][c],
                **{
                    k: s[k]
                    for k in ("cumulative_return", "annualized_return", "annualized_volatility", "sharpe_ratio", "max_drawdown")
                },
            }
        )
    cum = (1 + f).cumprod() - 1
    out: dict[str, Any] = {
        "note": (
            "Factors are market-derived proxies built from prices and volumes only (no accounting data). "
            "Long-short quintile portfolios, formed at month-end with data available at that close."
        ),
        "definitions": FACTOR_DEFINITIONS,
        "performance": perf,
        "correlation": {"factors": list(f.columns), "matrix": f.corr().round(4).to_numpy().tolist()},
        "cumulative": {"dates": [d.date().isoformat() for d in cum.index], **{c: _l(cum[c]) for c in f.columns}},
        "period": [f.index[0].date().isoformat(), f.index[-1].date().isoformat()],
    }
    if portfolio_id:
        p = get_portfolio(db, portfolio_id)
        if p.dataset_id != dataset_id:
            raise ConfigurationError("portfolio belongs to a different dataset")
        _, sim, bench_r, _ = simulate(db, p, start, end)
        mkt = bench_r
        exp = factor_exposure(sim.returns, f, mkt)
        rb = rolling_factor_betas(sim.returns, pd.concat([mkt.rename("market"), f], axis=1).dropna(), rolling_window)
        out["exposure"] = {"portfolio": p.name, "benchmark_as_market": p.benchmark_symbol, **exp}
        out["rolling_betas"] = {
            "window": rolling_window,
            "dates": [d.date().isoformat() for d in rb.index],
            **{c: _l(rb[c]) for c in rb.columns},
        }
    return out
