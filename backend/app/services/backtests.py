"""Backtest and walk-forward orchestration + persistence."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.backtesting.engine import CostModel, run_backtest
from app.backtesting.evaluation import (
    MarketData,
    chart_series,
    grid_search,
    make_segments,
    segment_metrics,
    summarize,
    walk_forward,
)
from app.core.config import get_settings
from app.core.errors import ConfigurationError, InsufficientDataError, NexisError, NotFoundError
from app.models import Backtest, BacktestTrade, Experiment
from app.services import experiments as exp_svc
from app.services.market_data import Panel, get_dataset, load_panel
from app.services.notifications import notify
from app.strategies.mean_reversion import zscore_series
from app.strategies.registry import build_strategy

Progress = Callable[[float, str | None], None] | None


def _market(panel: Panel, universe: list[str], benchmark: str | None) -> MarketData:
    """Adjusted OHLC so splits/dividends do not create artificial returns: open is scaled by adj/close."""
    factor = (panel.adj_close[universe] / panel.close[universe]).where(panel.close[universe] > 0)
    close = panel.adj_close[universe]
    open_ = panel.open[universe] * factor
    open_ = open_.where(open_.notna(), np.nan)
    bench = panel.adj_close[benchmark] if benchmark else None
    return MarketData(close=close, open=open_, volume=panel.volume[universe], benchmark=bench)


def validate_config(db: Session, cfg: dict[str, Any]) -> tuple[Panel, list[str]]:
    panel = load_panel(db, cfg["dataset_id"])
    universe = cfg.get("universe") or panel.tradable
    panel.require(universe)
    if len(universe) < 1:
        raise ConfigurationError("universe is empty")
    if cfg.get("benchmark_symbol"):
        panel.require([cfg["benchmark_symbol"]])
    build_strategy(cfg["strategy"], cfg.get("params") or {})
    for combo_key in cfg.get("param_grid") or {}:
        build_strategy(cfg["strategy"], {**(cfg.get("params") or {}), combo_key: (cfg["param_grid"][combo_key] or [None])[0]})
    CostModel(cfg.get("commission_bps", 5.0), cfg.get("slippage_bps", 5.0))
    s, e = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    if s >= e:
        raise ConfigurationError("start_date must be before end_date")
    if s < panel.close.index[0] or e > panel.close.index[-1] + pd.Timedelta(days=7):
        raise ConfigurationError(
            f"dates must lie within the dataset ({panel.close.index[0].date()} → {panel.close.index[-1].date()})"
        )
    if cfg.get("param_grid") and not cfg.get("train_end") and cfg.get("mode", "backtest") == "backtest":
        raise ConfigurationError("a parameter grid requires train_end so parameters are selected in-sample only")
    make_segments(s, e, _s(cfg.get("train_end")), _s(cfg.get("validation_end")))
    return panel, universe


def execute_backtest(db: Session, cfg: dict[str, Any], progress: Progress = None, parent_id: int | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    panel, universe = validate_config(db, cfg)
    ds = get_dataset(db, cfg["dataset_id"])
    rf = cfg.get("risk_free_rate", get_settings().risk_free_rate)
    start, end = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    costs = CostModel(cfg.get("commission_bps", 5.0), cfg.get("slippage_bps", 5.0))
    execution = cfg.get("execution", "next_open")
    capital = float(cfg.get("initial_capital", 1_000_000))
    bench = cfg.get("benchmark_symbol")
    data = _market(panel, universe, bench)
    params = dict(cfg.get("params") or {})
    exp = exp_svc.start_experiment(
        db,
        "backtest",
        cfg.get("name") or f"{cfg['strategy']} backtest",
        ds,
        cfg,
        seed=cfg.get("seed"),
        model=cfg["strategy"],
        features=None,
        train=(start, cfg.get("train_end")) if cfg.get("train_end") else None,
        test=(
            pd.Timestamp(cfg["validation_end"]) + pd.Timedelta(days=1)
            if cfg.get("validation_end")
            else pd.Timestamp(cfg["train_end"]) + pd.Timedelta(days=1),
            end,
        )
        if cfg.get("train_end")
        else None,
        notes=cfg.get("notes"),
        parent_id=parent_id,
    )
    try:
        selection = None
        if cfg.get("param_grid"):
            te = pd.Timestamp(cfg["train_end"])
            selection = grid_search(
                cfg["strategy"],
                params,
                cfg["param_grid"],
                data,
                start,
                te,
                costs,
                execution,
                rf,
                cfg.get("objective", "sharpe_ratio"),
                capital,
                (lambda p: progress(0.6 * p, "in-sample parameter search")) if progress else None,
            )
            params.update(selection["best_params"])
        if progress:
            progress(0.65, "running backtest")
        strategy = build_strategy(cfg["strategy"], params)
        result = run_backtest(strategy, data.close, data.open, data.volume, start, end, capital, costs, execution)  # type: ignore[arg-type]
        if progress:
            progress(0.85, "computing metrics")
        summary = summarize(result, data.benchmark, rf, capital)
        segs = make_segments(result.dates[0], result.dates[-1], _s(cfg.get("train_end")), _s(cfg.get("validation_end")))
        seg_metrics = segment_metrics(result, segs, data.benchmark, rf) if len(segs) > 1 else {}
        series = chart_series(result, data.benchmark, capital, rf)
        series["signal_log"] = result.signal_log[-400:]
        series["weights_history"] = result.weights_history[-400:]
        series["notes"] = result.notes
        series["segments"] = [{"name": n, "start": a.date().isoformat(), "end": b.date().isoformat()} for n, a, b in segs]

        bt = Backtest(
            experiment_id=exp.id,
            name=exp.name,
            strategy_key=cfg["strategy"],
            dataset_id=ds.id,
            benchmark_symbol=bench,
            execution=execution,
            start_date=result.dates[0].date(),
            end_date=result.dates[-1].date(),
            metrics=exp_svc._jsonable(
                {"full": summary, "segments": seg_metrics, "selected_params": params, "parameter_search": selection}
            ),
            series=exp_svc._jsonable(series),
            trade_count=summary["number_of_trades"],
        )
        db.add(bt)
        db.flush()
        rows = [
            {
                **t,
                "backtest_id": bt.id,
                "signal_date": pd.Timestamp(t["signal_date"]).date(),
                "date": pd.Timestamp(t["date"]).date(),
            }
            for t in result.trades
        ]
        for i in range(0, len(rows), 5000):
            db.execute(insert(BacktestTrade), rows[i : i + 5000])

        metric_rows = exp_svc.metric_rows_from("full", summary)
        for name, m in seg_metrics.items():
            metric_rows += exp_svc.metric_rows_from(name, m)
        exp_svc.complete_experiment(
            db,
            exp,
            metric_rows,
            summary={
                "backtest_id": bt.id,
                "strategy": cfg["strategy"],
                "selected_params": params,
                "headline": {
                    k: summary.get(k)
                    for k in (
                        "cumulative_return",
                        "annualized_return",
                        "sharpe_ratio",
                        "max_drawdown",
                        "total_transaction_costs",
                        "number_of_trades",
                    )
                },
            },
            artifacts=None,
            duration=time.perf_counter() - t0,
        )
        notify(
            db,
            "success",
            "backtest",
            f"Backtest {exp.code} completed",
            f"{strategy.name}: net cumulative return {summary['cumulative_return']:+.2%}, "
            f"{summary['number_of_trades']} trades, costs {summary['total_transaction_costs']:,.0f}.",
            link=f"/backtesting?id={bt.id}",
        )
        db.commit()
        return {"experiment_id": exp.id, "experiment_code": exp.code, "backtest_id": bt.id}
    except NexisError as exc:
        db.rollback()
        exp_svc.fail_experiment(db, exp.id, exc.message)
        notify(db, "error", "backtest", f"Backtest {exp.code} failed", exc.message, link="/backtesting")
        db.commit()
        raise


def execute_walk_forward(
    db: Session, cfg: dict[str, Any], progress: Progress = None, parent_id: int | None = None
) -> dict[str, Any]:
    t0 = time.perf_counter()
    cfg = {**cfg, "mode": "walk_forward"}
    if not cfg.get("param_grid"):
        raise ConfigurationError("walk-forward analysis requires a parameter grid to re-select in each fold")
    panel, universe = validate_config(db, {**cfg, "train_end": None, "validation_end": None})
    ds = get_dataset(db, cfg["dataset_id"])
    rf = cfg.get("risk_free_rate", get_settings().risk_free_rate)
    costs = CostModel(cfg.get("commission_bps", 5.0), cfg.get("slippage_bps", 5.0))
    data = _market(panel, universe, cfg.get("benchmark_symbol"))
    start, end = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    exp = exp_svc.start_experiment(
        db,
        "walk_forward",
        cfg.get("name") or f"{cfg['strategy']} walk-forward",
        ds,
        cfg,
        seed=cfg.get("seed"),
        model=cfg["strategy"],
        notes=cfg.get("notes"),
        parent_id=parent_id,
    )
    try:
        wf = walk_forward(
            cfg["strategy"],
            dict(cfg.get("params") or {}),
            cfg["param_grid"],
            data,
            start,
            end,
            int(cfg.get("train_days", 504)),
            int(cfg.get("test_days", 126)),
            costs,
            cfg.get("execution", "next_open"),
            rf,
            cfg.get("objective", "sharpe_ratio"),
            float(cfg.get("initial_capital", 1_000_000)),
            bool(cfg.get("anchored", False)),
            (lambda p: progress(p * 0.95, "walk-forward folds")) if progress else None,
        )
        rows = exp_svc.metric_rows_from("aggregate_oos", wf["aggregate_out_of_sample"])
        for f in wf["folds"]:
            rows += exp_svc.metric_rows_from(
                f"fold_{f['fold']}", {k: v for k, v in f.items() if k.startswith("test_") or k == "in_sample_objective"}
            )
        exp.train_start = pd.Timestamp(wf["folds"][0]["train_start"]).date()
        exp.test_start = pd.Timestamp(wf["folds"][0]["test_start"]).date()
        exp.test_end = pd.Timestamp(wf["folds"][-1]["test_end"]).date()
        exp_svc.complete_experiment(
            db,
            exp,
            rows,
            summary={
                "folds": len(wf["folds"]),
                "aggregate_sharpe": wf["aggregate_out_of_sample"].get("sharpe_ratio"),
                "aggregate_cumulative_return": wf["aggregate_out_of_sample"].get("cumulative_return"),
                "mean_in_sample_objective": wf["mean_in_sample_objective"],
                "mean_out_of_sample_sharpe": wf["mean_out_of_sample_sharpe"],
            },
            artifacts=wf,
            duration=time.perf_counter() - t0,
        )
        notify(
            db,
            "success",
            "backtest",
            f"Walk-forward {exp.code} completed",
            f"{len(wf['folds'])} folds; stitched out-of-sample Sharpe {_fmt(wf['aggregate_out_of_sample'].get('sharpe_ratio'))}.",
            link=f"/experiments?id={exp.id}",
        )
        db.commit()
        return {"experiment_id": exp.id, "experiment_code": exp.code}
    except NexisError as exc:
        db.rollback()
        exp_svc.fail_experiment(db, exp.id, exc.message)
        notify(db, "error", "backtest", f"Walk-forward {exp.code} failed", exc.message, link="/backtesting")
        db.commit()
        raise


def get_backtest(db: Session, bt_id: int) -> Backtest:
    bt = db.get(Backtest, bt_id)
    if bt is None:
        raise NotFoundError(f"backtest {bt_id} not found")
    return bt


def list_backtests(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Backtest, Experiment)
        .join(Experiment, Experiment.id == Backtest.experiment_id)
        .order_by(Backtest.created_at.desc())
        .limit(limit)
    ).all()
    return [serialize(bt, exp, include_series=False) for bt, exp in rows]


def serialize(bt: Backtest, exp: Experiment | None = None, include_series: bool = True) -> dict[str, Any]:
    out = {
        "id": bt.id,
        "experiment_id": bt.experiment_id,
        "experiment_code": exp.code if exp else None,
        "name": bt.name,
        "strategy_key": bt.strategy_key,
        "dataset_id": bt.dataset_id,
        "benchmark_symbol": bt.benchmark_symbol,
        "execution": bt.execution,
        "start_date": bt.start_date.isoformat(),
        "end_date": bt.end_date.isoformat(),
        "trade_count": bt.trade_count,
        "created_at": bt.created_at.isoformat(),
        "headline": {
            k: bt.metrics["full"].get(k)
            for k in (
                "cumulative_return",
                "annualized_return",
                "sharpe_ratio",
                "max_drawdown",
                "annualized_turnover",
                "total_transaction_costs",
            )
        },
    }
    if exp is not None:
        out["config"] = exp.config
        out["dataset_version"] = exp.dataset_version
        out["reproducibility"] = exp.reproducibility
    if include_series:
        out["metrics"] = bt.metrics
        out["series"] = bt.series
    return out


def trades(db: Session, bt_id: int, symbol: str | None = None, limit: int = 5000, offset: int = 0) -> dict[str, Any]:
    get_backtest(db, bt_id)
    q = select(BacktestTrade).where(BacktestTrade.backtest_id == bt_id)
    if symbol:
        q = q.where(BacktestTrade.symbol == symbol)
    rows = db.scalars(q.order_by(BacktestTrade.id).offset(offset).limit(limit)).all()
    return {"trades": [trade_dict(t) for t in rows], "offset": offset, "limit": limit}


def trade_dict(t: BacktestTrade) -> dict[str, Any]:
    return {
        "id": t.id,
        "signal_date": t.signal_date.isoformat(),
        "date": t.date.isoformat(),
        "symbol": t.symbol,
        "side": t.side,
        "shares": t.shares,
        "price": t.price,
        "notional": t.notional,
        "commission": t.commission,
        "slippage": t.slippage,
        "weight_before": t.weight_before,
        "weight_after": t.weight_after,
    }


def symbol_diagnostics(db: Session, bt_id: int, symbol: str) -> dict[str, Any]:
    """Price, indicator and executed trades for one asset — makes the signal logic inspectable."""
    bt = get_backtest(db, bt_id)
    exp = db.get(Experiment, bt.experiment_id)
    panel = load_panel(db, bt.dataset_id)
    panel.require([symbol])
    params = (bt.metrics or {}).get("selected_params") or (exp.config.get("params") if exp else {}) or {}
    close = panel.adj_close[[symbol]].loc[pd.Timestamp(bt.start_date) - pd.Timedelta(days=400) : pd.Timestamp(bt.end_date)]
    out: dict[str, Any] = {"symbol": symbol, "strategy": bt.strategy_key}
    window = close.loc[pd.Timestamp(bt.start_date) :]
    out["dates"] = [d.date().isoformat() for d in window.index]
    out["price"] = [None if pd.isna(v) else float(v) for v in window[symbol]]
    if bt.strategy_key == "mean_reversion":
        w = int(params.get("window", 20))
        z = zscore_series(close, w)[symbol].loc[pd.Timestamp(bt.start_date) :]
        out["indicator"] = {
            "name": f"z-score ({w}-day)",
            "values": [None if pd.isna(v) else float(v) for v in z],
            "thresholds": {"entry": -float(params.get("entry_z", 2.0)), "exit": -float(params.get("exit_z", 0.25))},
        }
    elif bt.strategy_key == "trend_filter":
        w = int(params.get("ma_window", 200))
        ma = close[symbol].rolling(w).mean().loc[pd.Timestamp(bt.start_date) :]
        out["indicator"] = {"name": f"SMA {w}", "values": [None if pd.isna(v) else float(v) for v in ma], "overlay": True}
    elif bt.strategy_key == "momentum":
        lb, sk = int(params.get("lookback", 126)), int(params.get("skip", 21))
        mom = (close[symbol].shift(sk) / close[symbol].shift(sk + lb) - 1).loc[pd.Timestamp(bt.start_date) :]
        out["indicator"] = {"name": f"momentum ({lb}d, skip {sk})", "values": [None if pd.isna(v) else float(v) for v in mom]}
    out["trades"] = trades(db, bt_id, symbol)["trades"]
    wh = (bt.series or {}).get("weights_history", [])
    out["target_weights"] = [{"date": h["date"], "weight": h["weights"].get(symbol, 0.0)} for h in wh]
    return out


def _s(v: Any) -> str | None:
    return None if v in (None, "") else str(v)


def _fmt(v: Any) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def ensure_no_empty_universe(universe: list[str]) -> None:
    if not universe:
        raise InsufficientDataError("universe is empty")


exp_svc.register_runner("backtest", execute_backtest)
exp_svc.register_runner("walk_forward", execute_walk_forward)
