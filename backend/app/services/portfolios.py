"""Portfolio construction, persistence and analytics."""

from __future__ import annotations

import math
from collections import OrderedDict
from datetime import date
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.correlation import correlation_matrix
from app.analytics.optimization import allocate, validate_weights
from app.analytics.portfolio import REBALANCE_FREQUENCIES, prepare_asset_returns, simulate_portfolio
from app.analytics.rolling import histogram, rolling_beta, rolling_correlation, rolling_volatility
from app.core.config import get_settings
from app.core.errors import ConfigurationError, ConflictError, InsufficientDataError, NotFoundError
from app.models import Portfolio, PortfolioPosition, PortfolioReturn, RiskMetric
from app.risk.contribution import concentration, correlation_exposure, risk_contributions
from app.risk.var import ALLOWED_CONFIDENCE, horizon_returns, rolling_var_backtest, var_summary
from app.services.market_data import Panel, load_panel
from app.services.notifications import notify

_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_cache_lock = Lock()
_CACHE_MAX = 64


def _cache_get(key: tuple[Any, ...]) -> dict[str, Any] | None:
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    return None


def _cache_put(key: tuple[Any, ...], value: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


# --- CRUD ---------------------------------------------------------------------------------


def get_portfolio(db: Session, pid: int) -> Portfolio:
    p = db.get(Portfolio, pid)
    if p is None:
        raise NotFoundError(f"portfolio {pid} not found")
    return p


def list_portfolios(db: Session, dataset_id: int | None = None) -> list[Portfolio]:
    q = select(Portfolio).order_by(Portfolio.created_at.desc())
    if dataset_id is not None:
        q = q.where(Portfolio.dataset_id == dataset_id)
    return list(db.scalars(q))


def build_weights(panel: Panel, payload: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any]]:
    symbols: list[str] = payload["symbols"]
    if len(set(symbols)) != len(symbols):
        raise ConfigurationError("duplicate symbols in portfolio")
    if not 1 <= len(symbols) <= 60:
        raise ConfigurationError("a portfolio must hold between 1 and 60 assets")
    panel.require(symbols)
    method = payload.get("allocation_method", "custom")
    lo, hi = float(payload.get("min_weight", 0.0)), float(payload.get("max_weight", 1.0))
    est_start, est_end = payload.get("estimation_start"), payload.get("estimation_end")
    rets = panel.returns(symbols, est_start, est_end).dropna()
    if method != "custom" and method != "equal_weight" and len(rets) < 60:
        raise InsufficientDataError(
            f"estimation window has {len(rets)} complete observations for these assets; at least 60 required",
            details={"hint": "widen the estimation window or remove recently listed assets"},
        )
    custom = payload.get("weights")
    if method == "custom":
        if not custom:
            raise ConfigurationError("custom allocation requires a weight for every symbol")
        missing = [s for s in symbols if s not in custom]
        if missing:
            raise ConfigurationError("missing weights", details={"symbols": missing})
        validate_weights({s: custom[s] for s in symbols}, lo, hi)
    res = allocate(
        method,
        rets if len(rets) else pd.DataFrame(columns=symbols),
        custom_weights=custom,
        min_weight=lo,
        max_weight=hi,
        risk_free_rate=payload.get("risk_free_rate", get_settings().risk_free_rate),
    )
    w = {s: float(v) for s, v in res.weights.items()}
    validate_weights(w, 0.0, 1.0, enforce_bounds=False)
    details = {
        "method": method,
        "min_weight": lo,
        "max_weight": hi,
        **res.diagnostics,
        "disclaimer": "Research optimisation on historical data (in-sample). Not investment advice.",
    }
    return w, details


def create_portfolio(db: Session, payload: dict[str, Any]) -> Portfolio:
    if db.scalars(select(Portfolio).where(Portfolio.name == payload["name"])).first():
        raise ConflictError(f"a portfolio named '{payload['name']}' already exists")
    p = Portfolio()
    _apply(db, p, payload)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def update_portfolio(db: Session, pid: int, payload: dict[str, Any]) -> Portfolio:
    p = get_portfolio(db, pid)
    other = db.scalars(select(Portfolio).where(Portfolio.name == payload["name"], Portfolio.id != pid)).first()
    if other:
        raise ConflictError(f"a portfolio named '{payload['name']}' already exists")
    _apply(db, p, payload)
    db.commit()
    db.refresh(p)
    return p


def _apply(db: Session, p: Portfolio, payload: dict[str, Any]) -> None:
    panel = load_panel(db, payload["dataset_id"])
    bench = payload["benchmark_symbol"]
    panel.require([bench])
    if payload.get("rebalance_frequency", "monthly") not in REBALANCE_FREQUENCIES:
        raise ConfigurationError("invalid rebalance frequency", details={"allowed": REBALANCE_FREQUENCIES})
    cap = float(payload.get("initial_capital", 1_000_000))
    if not (1_000 <= cap <= 1e12) or not math.isfinite(cap):
        raise ConfigurationError("initial capital must be between 1,000 and 1e12")
    try:
        weights, details = build_weights(panel, payload)
    except ConfigurationError as exc:
        notify(db, "warning", "portfolio", "Invalid portfolio configuration", exc.message, link="/portfolio-lab")
        db.commit()
        raise
    p.name = payload["name"].strip()
    p.dataset_id = payload["dataset_id"]
    p.benchmark_symbol = bench
    p.initial_capital = cap
    p.rebalance_frequency = payload.get("rebalance_frequency", "monthly")
    p.allocation_method = payload.get("allocation_method", "custom")
    p.constraints = {
        "min_weight": payload.get("min_weight", 0.0),
        "max_weight": payload.get("max_weight", 1.0),
        "estimation_start": _s(payload.get("estimation_start")),
        "estimation_end": _s(payload.get("estimation_end")),
    }
    p.allocation_details = details
    p.notes = payload.get("notes")
    p.positions.clear()
    db.flush()
    for s, w in weights.items():
        p.positions.append(PortfolioPosition(symbol=s, weight=w))


def delete_portfolio(db: Session, pid: int) -> None:
    db.delete(get_portfolio(db, pid))
    db.commit()


def serialize(p: Portfolio) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "dataset_id": p.dataset_id,
        "benchmark_symbol": p.benchmark_symbol,
        "initial_capital": p.initial_capital,
        "rebalance_frequency": p.rebalance_frequency,
        "allocation_method": p.allocation_method,
        "constraints": p.constraints,
        "allocation_details": p.allocation_details,
        "notes": p.notes,
        "positions": [{"symbol": x.symbol, "weight": x.weight} for x in p.positions],
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


# --- Analytics --------------------------------------------------------------------------


def _weights(p: Portfolio) -> pd.Series:
    return pd.Series({x.symbol: x.weight for x in p.positions}, dtype=float)


def simulate(db: Session, p: Portfolio, start: date | None, end: date | None) -> tuple[Panel, Any, pd.Series, pd.DataFrame]:
    panel = load_panel(db, p.dataset_id)
    w = _weights(p)
    if w.empty:
        raise ConfigurationError("portfolio has no positions")
    px = panel.adj_close[list(w.index)].loc[pd.Timestamp(start) if start else None : pd.Timestamp(end) if end else None]
    asset_r, filled = prepare_asset_returns(px)
    sim = simulate_portfolio(asset_r, w, p.rebalance_frequency, p.initial_capital, filled)
    bench_r = panel.adj_close[p.benchmark_symbol].pct_change(fill_method=None).reindex(sim.returns.index)
    return panel, sim, bench_r, asset_r


def analytics(
    db: Session,
    pid: int,
    start: date | None = None,
    end: date | None = None,
    risk_free_rate: float | None = None,
    rolling_window: int = 63,
) -> dict[str, Any]:
    p = get_portfolio(db, pid)
    rf = get_settings().risk_free_rate if risk_free_rate is None else risk_free_rate
    if not 5 <= rolling_window <= 504:
        raise ConfigurationError("rolling_window must be between 5 and 504")
    panel = load_panel(db, p.dataset_id)
    key = ("analytics", pid, p.updated_at, panel.version, start, end, rf, rolling_window)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    panel, sim, bench_r, asset_r = simulate(db, p, start, end)
    r = sim.returns
    summary = M.performance_summary(r, bench_r, rf)
    bench_summary = M.performance_summary(bench_r.dropna(), None, rf) if bench_r.notna().sum() > 2 else None
    w = _weights(p)
    value = float(sim.values.iloc[-1])

    risk: dict[str, Any] = {}
    for c in (0.95, 0.99):
        try:
            risk[f"{int(c * 100)}"] = var_summary(r, c, 1, value)
        except InsufficientDataError as exc:
            risk[f"{int(c * 100)}"] = {"error": exc.message}
    try:
        decomposition = risk_contributions(w, asset_r)
    except InsufficientDataError as exc:
        decomposition = {"error": exc.message}
    dd = M.drawdown_series(r)
    bench_wealth = (1 + bench_r.fillna(0)).cumprod()
    port_wealth = (1 + r).cumprod()
    asset_stats = []
    for s in w.index:
        ar = asset_r[s]
        asset_stats.append(
            {
                "symbol": s,
                "name": panel.assets[s]["name"],
                "sector": panel.assets[s]["sector"],
                "target_weight": float(w[s]),
                "current_weight": float(sim.weights[s].iloc[-1]),
                "cumulative_return": M.cumulative_return(ar),
                "annualized_volatility": M.annualized_volatility(ar),
                "beta": M._safe(lambda ar=ar: M.beta(ar, bench_r)),
            }
        )
    corr = correlation_matrix(asset_r, cluster=False) if len(w) >= 2 else None
    sector_w: dict[str, float] = {}
    for s, v in w.items():
        sec = panel.assets[s]["sector"] or "Other"
        sector_w[sec] = sector_w.get(sec, 0.0) + float(v)

    idx = [d.date().isoformat() for d in r.index]
    result = {
        "portfolio": serialize(p),
        "period": {"start": idx[0], "end": idx[-1], "observations": len(idx)},
        "risk_free_rate": rf,
        "summary": summary,
        "benchmark": {"symbol": p.benchmark_symbol, "name": panel.assets[p.benchmark_symbol]["name"], "summary": bench_summary},
        "max_drawdown_details": M.max_drawdown_details(r),
        "var": risk,
        "risk_decomposition": decomposition,
        "concentration": concentration(w),
        "correlation_exposure": correlation_exposure(w, asset_r),
        "sector_weights": sector_w,
        "assets": asset_stats,
        "correlation": corr,
        "turnover": {
            "total_one_way": float(sim.turnover.sum()),
            "rebalances": len(sim.rebalance_dates),
            "annualized": float(sim.turnover.sum() / (len(r) / 252)),
        },
        "notes": sim.notes,
        "series": {
            "dates": idx,
            "value": _l(sim.values),
            "benchmark_value": _l(p.initial_capital * bench_wealth),
            "returns": _l(r),
            "drawdown": _l(dd),
            "benchmark_drawdown": _l(M.drawdown_series(bench_r.fillna(0))),
            "relative_performance": _l(port_wealth / bench_wealth - 1),
            "rolling_volatility": _l(rolling_volatility(r, rolling_window)),
            "benchmark_rolling_volatility": _l(rolling_volatility(bench_r, rolling_window)),
            "rolling_beta": _l(rolling_beta(r, bench_r, rolling_window).reindex(r.index)),
            "rolling_correlation": _l(rolling_correlation(r, bench_r, rolling_window).reindex(r.index)),
            "rolling_window": rolling_window,
        },
        "return_histogram": histogram(r, 60),
    }
    _persist_returns(db, p.id, sim)
    _cache_put(key, result)
    return result


def _persist_returns(db: Session, pid: int, sim: Any) -> None:
    db.execute(delete(PortfolioReturn).where(PortfolioReturn.portfolio_id == pid))
    rows = [
        {"portfolio_id": pid, "date": d.date(), "daily_return": float(rv), "value": float(v)}
        for d, rv, v in zip(sim.returns.index, sim.returns.to_numpy(), sim.values.to_numpy(), strict=True)
    ]
    for i in range(0, len(rows), 5000):
        db.execute(insert(PortfolioReturn), rows[i : i + 5000])
    db.commit()


def risk_analysis(
    db: Session, pid: int, confidences: list[float], lookback_days: int, horizon_days: int, backtest_window: int = 250
) -> dict[str, Any]:
    for c in confidences:
        if c not in ALLOWED_CONFIDENCE:
            raise ConfigurationError(f"confidence must be one of {ALLOWED_CONFIDENCE}")
    if not 60 <= lookback_days <= 5000:
        raise ConfigurationError("lookback_days must be between 60 and 5000")
    if not 1 <= horizon_days <= 20:
        raise ConfigurationError("horizon_days must be between 1 and 20")
    p = get_portfolio(db, pid)
    _, sim, _, _ = simulate(db, p, None, None)
    r = sim.returns.iloc[-lookback_days:]
    value = float(sim.values.iloc[-1])
    as_of = r.index[-1].date()
    out = []
    for c in confidences:
        s = var_summary(r, c, horizon_days, value)
        out.append(s)
        for method in ("historical", "parametric_normal", "cornish_fisher"):
            est = s[method]
            db.add(
                RiskMetric(
                    portfolio_id=p.id,
                    method=method,
                    confidence=c,
                    horizon_days=horizon_days,
                    lookback_days=len(r),
                    as_of=as_of,
                    var=est["var"],
                    cvar=est["cvar"],
                    var_amount=est["var_amount"],
                    cvar_amount=est["cvar_amount"],
                )
            )
    db.commit()
    try:
        bt = rolling_var_backtest(sim.returns, max(confidences), backtest_window)
    except InsufficientDataError as exc:
        bt = {"error": exc.message}
    hr = horizon_returns(r, horizon_days)
    return {
        "portfolio_id": p.id,
        "portfolio_name": p.name,
        "as_of": as_of.isoformat(),
        "portfolio_value": value,
        "lookback_days": len(r),
        "horizon_days": horizon_days,
        "estimates": out,
        "distribution": histogram(hr, 60),
        "var_backtest": bt,
        "methodology": {
            "historical": "Empirical quantile of the lookback returns (overlapping h-day returns when horizon > 1).",
            "parametric_normal": "Assumes i.i.d. normal returns: VaR = -(mu*h + z*sigma*sqrt(h)).",
            "cornish_fisher": "Normal quantile adjusted for sample skewness and excess kurtosis; sqrt-time scaled.",
            "caveat": "VaR is a quantile of historical/modelled losses, not a maximum possible loss.",
        },
    }


def risk_history(db: Session, pid: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(RiskMetric).where(RiskMetric.portfolio_id == pid).order_by(RiskMetric.created_at.desc()).limit(limit)
    )
    return [
        {
            "id": r.id,
            "method": r.method,
            "confidence": r.confidence,
            "horizon_days": r.horizon_days,
            "lookback_days": r.lookback_days,
            "as_of": r.as_of.isoformat(),
            "var": r.var,
            "cvar": r.cvar,
            "var_amount": r.var_amount,
            "cvar_amount": r.cvar_amount,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def compare(db: Session, a: int, b: int, start: date | None, end: date | None) -> dict[str, Any]:
    ra = analytics(db, a, start, end)
    rb = analytics(db, b, start, end)
    keys = [
        "cumulative_return",
        "annualized_return",
        "annualized_volatility",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "calmar_ratio",
        "beta",
        "tracking_error",
        "information_ratio",
    ]
    rows = [{"metric": k, "a": ra["summary"].get(k), "b": rb["summary"].get(k)} for k in keys]
    for label, c in (("VaR 95% (hist.)", "95"), ("CVaR 95% (hist.)", "95")):
        field = "var" if "CVaR" not in label else "cvar"
        rows.append(
            {
                "metric": label,
                "a": ra["var"].get(c, {}).get("historical", {}).get(field),
                "b": rb["var"].get(c, {}).get("historical", {}).get(field),
            }
        )
    rows.append({"metric": "annualized_turnover", "a": ra["turnover"]["annualized"], "b": rb["turnover"]["annualized"]})
    common = sorted(set(ra["series"]["dates"]) & set(rb["series"]["dates"]))
    return {
        "a": {"id": a, "name": ra["portfolio"]["name"], "period": ra["period"]},
        "b": {"id": b, "name": rb["portfolio"]["name"], "period": rb["period"]},
        "metrics": rows,
        "note": "Historical simulation of both configurations over their own available periods; not a ranking or recommendation.",
        "series": {
            "a_dates": ra["series"]["dates"],
            "a_value": ra["series"]["value"],
            "b_dates": rb["series"]["dates"],
            "b_value": rb["series"]["value"],
            "common_start": common[0] if common else None,
        },
    }


def _l(s: pd.Series) -> list[float | None]:
    arr = s.to_numpy(dtype=float)
    return [None if not np.isfinite(v) else float(v) for v in arr]


def _s(v: Any) -> str | None:
    return None if v is None else str(v)
