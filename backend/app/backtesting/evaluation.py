"""Backtest evaluation: metrics, chronological segments, parameter search and walk-forward.

Out-of-sample discipline
------------------------
* Segments are strictly chronological: in-sample (research) → validation → out-of-sample test.
* When a parameter grid is supplied, parameters are chosen using **in-sample data only**
  (the backtest is re-run over the in-sample window alone), then frozen and evaluated on the
  later segments. Validation/test performance never influences the choice.
* Walk-forward: each fold selects parameters on its training window, then trades its test
  window; aggregate statistics are computed from the stitched fold *test* returns only.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.metrics import TRADING_DAYS, drawdown_series, performance_summary
from app.analytics.rolling import monthly_return_table, rolling_sharpe, rolling_volatility
from app.backtesting.engine import BacktestResult, CostModel, run_backtest
from app.core.errors import ConfigurationError, InsufficientDataError
from app.strategies.registry import build_strategy

MAX_GRID_COMBINATIONS = 36
OBJECTIVES = ("sharpe_ratio", "annualized_return", "calmar_ratio", "sortino_ratio")


@dataclass
class MarketData:
    close: pd.DataFrame
    open: pd.DataFrame
    volume: pd.DataFrame
    benchmark: pd.Series | None  # benchmark close prices


def summarize(
    result: BacktestResult, benchmark_close: pd.Series | None, risk_free_rate: float, initial_capital: float
) -> dict[str, Any]:
    net = result.net_returns
    bench_r = None
    if benchmark_close is not None:
        bench_r = benchmark_close.reindex(net.index).pct_change(fill_method=None)
    perf = performance_summary(net, bench_r, risk_free_rate)
    gross = performance_summary(result.gross_returns, None, risk_free_rate)
    years = len(net) / TRADING_DAYS
    real_trades = [t for t in result.trades if t["side"] != "delist"]
    perf.update(
        {
            "gross_cumulative_return": gross["cumulative_return"],
            "gross_annualized_return": gross["annualized_return"],
            "gross_sharpe_ratio": gross["sharpe_ratio"],
            "total_transaction_costs": result.total_costs,
            "transaction_costs_pct_initial": result.total_costs / initial_capital,
            "annual_cost_drag": gross["annualized_return"] - perf["annualized_return"],
            "annualized_turnover": float(result.turnover.sum() / years) if years > 0 else None,
            "number_of_trades": len(real_trades),
            "rebalance_count": len(result.weights_history),
            "average_gross_exposure": float(result.exposure["gross"].mean()),
            "final_equity": float(result.equity.iloc[-1]),
            "missing_price_events": result.missing_price_events,
        }
    )
    if bench_r is not None and bench_r.notna().sum() > 2:
        bp = performance_summary(bench_r.dropna(), None, risk_free_rate)
        perf["benchmark_cumulative_return"] = bp["cumulative_return"]
        perf["benchmark_annualized_return"] = bp["annualized_return"]
        perf["benchmark_sharpe_ratio"] = bp["sharpe_ratio"]
        perf["benchmark_max_drawdown"] = bp["max_drawdown"]
    return _clean(perf)


def chart_series(
    result: BacktestResult, benchmark_close: pd.Series | None, initial_capital: float, risk_free_rate: float
) -> dict[str, Any]:
    idx = result.dates
    out: dict[str, Any] = {
        "dates": [d.date().isoformat() for d in idx],
        "equity": _l(result.equity),
        "gross_equity": _l(initial_capital * (1 + result.gross_returns).cumprod()),
        "net_returns": _l(result.net_returns),
        "drawdown": _l(drawdown_series(result.net_returns)),
        "rolling_sharpe_126": _l(rolling_sharpe(result.net_returns, 126, risk_free_rate)),
        "rolling_vol_63": _l(rolling_volatility(result.net_returns, 63)),
        "costs": _l(result.costs),
        "turnover": _l(result.turnover),
        "gross_exposure": _l(result.exposure["gross"]),
        "net_exposure": _l(result.exposure["net"]),
        "monthly_returns": monthly_return_table(result.net_returns),
    }
    if benchmark_close is not None:
        b = benchmark_close.reindex(idx).ffill()
        first = b.dropna()
        if not first.empty:
            out["benchmark_equity"] = _l(initial_capital * b / first.iloc[0])
    return out


def make_segments(
    start: pd.Timestamp, end: pd.Timestamp, train_end: str | None, validation_end: str | None
) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    if train_end is None:
        return [("full", start, end)]
    te = pd.Timestamp(train_end)
    if not start < te < end:
        raise ConfigurationError("train_end must fall strictly inside the backtest window")
    segs = [("in_sample", start, te)]
    if validation_end is not None:
        ve = pd.Timestamp(validation_end)
        if not te < ve < end:
            raise ConfigurationError("validation_end must fall strictly between train_end and the end date")
        segs.append(("validation", te + pd.Timedelta(days=1), ve))
        segs.append(("out_of_sample", ve + pd.Timedelta(days=1), end))
    else:
        segs.append(("out_of_sample", te + pd.Timedelta(days=1), end))
    return segs


def segment_metrics(
    result: BacktestResult,
    segments: list[tuple[str, pd.Timestamp, pd.Timestamp]],
    benchmark_close: pd.Series | None,
    risk_free_rate: float,
) -> dict[str, Any]:
    out = {}
    bench_r = benchmark_close.reindex(result.dates).pct_change(fill_method=None) if benchmark_close is not None else None
    for name, a, b in segments:
        r = result.net_returns.loc[a:b]
        if len(r) < 5:
            out[name] = {"error": "segment too short"}
            continue
        m = performance_summary(r, bench_r.loc[a:b] if bench_r is not None else None, risk_free_rate)
        seg_costs = float(result.costs.loc[a:b].sum())
        m["transaction_costs"] = seg_costs
        m["turnover"] = float(result.turnover.loc[a:b].sum())
        if bench_r is not None:
            br = bench_r.loc[a:b].dropna()
            if len(br) > 2:
                m["benchmark_cumulative_return"] = float((1 + br).prod() - 1)
        out[name] = _clean(m)
    return out


def _expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid)
    for k in keys:
        if not isinstance(grid[k], list) or not grid[k]:
            raise ConfigurationError(f"grid values for '{k}' must be a non-empty list")
    combos = [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*(grid[k] for k in keys))]
    if len(combos) > MAX_GRID_COMBINATIONS:
        raise ConfigurationError(f"parameter grid has {len(combos)} combinations; maximum is {MAX_GRID_COMBINATIONS}")
    return combos


def grid_search(
    strategy_key: str,
    base_params: dict[str, Any],
    grid: dict[str, list[Any]],
    data: MarketData,
    start: pd.Timestamp,
    end: pd.Timestamp,
    costs: CostModel,
    execution: str,
    risk_free_rate: float,
    objective: str = "sharpe_ratio",
    initial_capital: float = 1_000_000.0,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Evaluate each combination on [start, end] only and select the best by ``objective``."""
    if objective not in OBJECTIVES:
        raise ConfigurationError(f"objective must be one of {OBJECTIVES}")
    combos = _expand_grid(grid)
    rows = []
    for n_done, combo in enumerate(combos):
        params = {**base_params, **combo}
        row: dict[str, Any] = {"params": combo}
        try:
            strat = build_strategy(strategy_key, params)
            res = run_backtest(
                strat, data.close, data.open, data.volume, start, end, initial_capital, costs, execution, record_trades=False
            )  # type: ignore[arg-type]
            perf = performance_summary(res.net_returns, None, risk_free_rate)
            row.update(
                {
                    k: perf.get(k)
                    for k in (
                        "sharpe_ratio",
                        "annualized_return",
                        "calmar_ratio",
                        "sortino_ratio",
                        "max_drawdown",
                        "annualized_volatility",
                    )
                }
            )
        except (InsufficientDataError, ConfigurationError) as exc:
            row["error"] = exc.message
        rows.append(_clean(row))
        if progress:
            progress((n_done + 1) / len(combos))
    scored = [r for r in rows if r.get(objective) is not None]
    if not scored:
        raise InsufficientDataError("no parameter combination produced a valid objective value in-sample")
    best = max(scored, key=lambda r: r[objective])
    return {
        "objective": objective,
        "results": rows,
        "best_params": best["params"],
        "best_value": best[objective],
        "selection_window": [start.date().isoformat(), end.date().isoformat()],
    }


def walk_forward(
    strategy_key: str,
    base_params: dict[str, Any],
    grid: dict[str, list[Any]],
    data: MarketData,
    start: pd.Timestamp,
    end: pd.Timestamp,
    train_days: int,
    test_days: int,
    costs: CostModel,
    execution: str,
    risk_free_rate: float,
    objective: str = "sharpe_ratio",
    initial_capital: float = 1_000_000.0,
    anchored: bool = False,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    if train_days < 126 or test_days < 21:
        raise ConfigurationError("train_days must be >= 126 and test_days >= 21")
    idx = data.close.loc[start:end].index
    folds_spec = []
    a = 0
    while a + train_days + test_days <= len(idx):
        tr0 = 0 if anchored else a
        folds_spec.append(
            (idx[tr0], idx[a + train_days - 1], idx[a + train_days], idx[min(a + train_days + test_days, len(idx)) - 1])
        )
        a += test_days
    if len(folds_spec) < 2:
        raise InsufficientDataError("window too short for at least two walk-forward folds", details={"days": len(idx)})
    if len(folds_spec) > 20:
        raise ConfigurationError(f"walk-forward would create {len(folds_spec)} folds; maximum is 20 — increase test_days")

    folds = []
    stitched: list[pd.Series] = []
    capital = initial_capital
    for fi, (tr_s, tr_e, te_s, te_e) in enumerate(folds_spec):

        def sub_progress(p: float, fi: int = fi) -> None:
            if progress:
                progress((fi + p * 0.9) / len(folds_spec))

        gs = grid_search(
            strategy_key,
            base_params,
            grid,
            data,
            tr_s,
            tr_e,
            costs,
            execution,
            risk_free_rate,
            objective,
            initial_capital,
            sub_progress,
        )
        params = {**base_params, **gs["best_params"]}
        strat = build_strategy(strategy_key, params)
        res = run_backtest(strat, data.close, data.open, data.volume, te_s, te_e, capital, costs, execution, record_trades=False)  # type: ignore[arg-type]
        capital = float(res.equity.iloc[-1])
        bench = data.benchmark.reindex(res.dates).pct_change(fill_method=None) if data.benchmark is not None else None
        perf = performance_summary(res.net_returns, bench, risk_free_rate)
        folds.append(
            _clean(
                {
                    "fold": fi + 1,
                    "train_start": tr_s.date().isoformat(),
                    "train_end": tr_e.date().isoformat(),
                    "test_start": te_s.date().isoformat(),
                    "test_end": te_e.date().isoformat(),
                    "selected_params": gs["best_params"],
                    "in_sample_objective": gs["best_value"],
                    "test_cumulative_return": perf["cumulative_return"],
                    "test_sharpe_ratio": perf["sharpe_ratio"],
                    "test_max_drawdown": perf["max_drawdown"],
                    "test_annualized_volatility": perf["annualized_volatility"],
                    "test_costs": res.total_costs,
                    "grid": gs["results"],
                }
            )
        )
        stitched.append(res.net_returns)
        if progress:
            progress((fi + 1) / len(folds_spec))

    oos = pd.concat(stitched)
    bench_all = data.benchmark.reindex(oos.index).pct_change(fill_method=None) if data.benchmark is not None else None
    agg = performance_summary(oos, bench_all, risk_free_rate)
    sharpe_is = [f["in_sample_objective"] for f in folds if f.get("in_sample_objective") is not None]
    sharpe_oos = [f["test_sharpe_ratio"] for f in folds if f.get("test_sharpe_ratio") is not None]
    return {
        "folds": folds,
        "aggregate_out_of_sample": _clean(agg),
        "mean_in_sample_objective": float(np.mean(sharpe_is)) if sharpe_is else None,
        "mean_out_of_sample_sharpe": float(np.mean(sharpe_oos)) if sharpe_oos else None,
        "parameter_stability": _param_stability([f["selected_params"] for f in folds]),
        "series": {
            "dates": [d.date().isoformat() for d in oos.index],
            "equity": _l(initial_capital * (1 + oos).cumprod()),
            "drawdown": _l(drawdown_series(oos)),
        },
        "anchored": anchored,
        "train_days": train_days,
        "test_days": test_days,
    }


def _param_stability(selected: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in {k for s in selected for k in s}:
        vals = [s.get(k) for s in selected]
        counts: dict[str, int] = {}
        for v in vals:
            counts[str(v)] = counts.get(str(v), 0) + 1
        out[k] = counts
    return out


def _l(s: pd.Series) -> list[float | None]:
    return [None if (v is None or not math.isfinite(v)) else float(v) for v in s.to_numpy(dtype=float)]


def _clean(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_clean(v) for v in d]
    if isinstance(d, (float, np.floating)):
        return float(d) if math.isfinite(d) else None
    if isinstance(d, np.integer):
        return int(d)
    return d
