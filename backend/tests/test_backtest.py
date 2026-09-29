"""Backtest engine guarantees: no look-ahead, correct costs, execution timing, rebalancing,
valid weights, honest treatment of missing data, and out-of-sample discipline."""

from __future__ import annotations

import itertools
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from app.backtesting.engine import CostModel, run_backtest
from app.backtesting.evaluation import MarketData, grid_search, make_segments, segment_metrics, walk_forward
from app.core.errors import ConfigurationError
from app.strategies.base import MarketHistory, Param, Strategy
from app.strategies.mean_reversion import zscore_last, zscore_series
from app.strategies.registry import build_strategy
from tests.conftest import make_prices

NO_COST = CostModel(0.0, 0.0)


class SpyStrategy(Strategy):
    """Records exactly what history it is shown and holds a fixed allocation."""

    key = "spy"
    name = "spy"
    description = "test"
    PARAMS: ClassVar[tuple[Param, ...]] = (Param("rebalance", str, "daily", "", choices=("daily", "monthly")),)
    weights: ClassVar[dict[str, float]] = {}

    def reset(self) -> None:
        super().reset()
        self.seen: list[pd.Timestamp] = []

    @property
    def warmup(self) -> int:
        return 1

    def target_weights(self, history: MarketHistory) -> pd.Series | None:
        self.seen.append(history.close.index[-1])
        return pd.Series(self.weights or {c: 1 / history.close.shape[1] for c in history.close.columns})


def _universe(panel: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    cols = [c for c in panel["close"].columns if c not in ("NXMKT", "NXBND")]
    return panel["close"][cols], panel["open"][cols]


# --- 1. No future observations influence signals ----------------------------------------------


def test_strategy_never_sees_future_bars(panel):
    close, open_ = _universe(panel)
    s = SpyStrategy()
    res = run_backtest(s, close, open_, None, "2020-01-02", "2020-06-30", costs=NO_COST)
    signal_dates = [pd.Timestamp(h["signal_date"]) for h in res.weights_history]
    # Every call saw history ending exactly on its signal date.
    assert s.seen[: len(signal_dates)] == signal_dates
    for h in res.weights_history:
        assert pd.Timestamp(h["date"]) > pd.Timestamp(h["signal_date"])  # executed strictly later


@pytest.mark.parametrize(
    "key,params",
    [("momentum", {"lookback": 63, "skip": 5, "top_n": 5}), ("mean_reversion", {}), ("trend_filter", {"ma_window": 50})],
)
def test_perturbing_future_prices_does_not_change_past_signals(panel, key, params):
    close, open_ = _universe(panel)
    cutoff = pd.Timestamp("2021-06-30")
    base = run_backtest(build_strategy(key, params), close, open_, None, "2020-01-02", "2022-06-30", costs=NO_COST)
    rng = np.random.default_rng(0)
    noise = pd.DataFrame(rng.uniform(0.5, 1.5, close.shape), index=close.index, columns=close.columns)
    future_mask = close.index > cutoff
    close2 = close.copy()
    open2 = open_.copy()
    close2.loc[future_mask] *= noise.loc[future_mask]
    open2.loc[future_mask] *= noise.loc[future_mask]
    alt = run_backtest(build_strategy(key, params), close2, open2, None, "2020-01-02", "2022-06-30", costs=NO_COST)
    past_a = [h for h in base.weights_history if pd.Timestamp(h["signal_date"]) <= cutoff]
    past_b = [h for h in alt.weights_history if pd.Timestamp(h["signal_date"]) <= cutoff]
    assert past_a == past_b
    assert base.equity.loc[:cutoff].equals(alt.equity.loc[:cutoff])


def test_vectorised_zscore_matches_point_in_time_calculation(panel):
    close, _ = _universe(panel)
    z_all = zscore_series(close, 20)
    for t in (100, 400, 800):
        pit = zscore_last(close.iloc[: t + 1], 20)
        pd.testing.assert_series_equal(pit, z_all.iloc[t][pit.index], check_names=False, atol=1e-10)


# --- 2. Transaction costs applied correctly ------------------------------------------------------


@pytest.mark.parametrize("commission,slippage", [(10.0, 0.0), (0.0, 10.0), (6.0, 4.0)])
def test_costs_on_constant_price_asset(commission, slippage):
    px = make_prices({"A": [100.0] * 6})
    res = run_backtest(
        build_strategy("equal_weight", {"rebalance": "monthly"}), px, px, None, costs=CostModel(commission, slippage)
    )
    rate = (commission + slippage) / 1e4
    # Target is sized on equity net of estimated cost: notional = 1e6 × (1 − rate).
    notional = 1e6 * (1 - rate)
    assert res.total_costs == pytest.approx(notional * rate)
    assert res.equity.iloc[-1] == pytest.approx(1e6 - notional * rate)
    # Gross performance adds back costs → flat asset, flat gross equity.
    assert (1 + res.gross_returns).prod() == pytest.approx(1.0)
    assert len(res.trades) == 1 and res.trades[0]["commission"] == pytest.approx(notional * commission / 1e4)


def test_zero_costs_have_no_drag(panel):
    close, open_ = _universe(panel)
    res = run_backtest(build_strategy("equal_weight"), close, open_, None, "2020-01-02", "2020-12-31", costs=NO_COST)
    assert res.total_costs == 0.0
    assert np.allclose(res.net_returns, res.gross_returns)


# --- 3/5. Rebalancing and execution timing -------------------------------------------------------------


def test_known_result_tiny_dataset():
    px = make_prices({"A": [100.0, 100.0, 110.0, 121.0]})
    res = run_backtest(build_strategy("equal_weight"), px, px, None, costs=NO_COST)
    # Signal at close of day 0, buy at open of day 1 (100): 10,000 shares.
    assert res.trades[0]["date"] == px.index[1].date().isoformat() and res.trades[0]["shares"] == pytest.approx(10_000)
    assert res.equity.tolist() == pytest.approx([1e6, 1e6, 1.1e6, 1.21e6])
    assert (1 + res.net_returns).prod() - 1 == pytest.approx(0.21)


@pytest.mark.parametrize("execution,field", [("next_open", "open"), ("next_close", "close")])
def test_execution_price_follows_documented_rule(execution, field):
    close = make_prices({"A": [100.0, 102.0, 104.0, 106.0]})
    open_ = make_prices({"A": [99.0, 101.0, 103.0, 105.0]})
    res = run_backtest(build_strategy("equal_weight"), close, open_, None, costs=NO_COST, execution=execution)
    t = res.trades[0]
    assert t["signal_date"] == close.index[0].date().isoformat()
    assert t["date"] == close.index[1].date().isoformat()
    assert t["price"] == pytest.approx((open_ if field == "open" else close).iloc[1, 0])


def test_monthly_rebalancing_restores_equal_weights():
    idx = pd.bdate_range("2024-01-01", "2024-04-30")
    n = len(idx)
    close = pd.DataFrame({"A": 100 * 1.002 ** np.arange(n), "B": 100 * 0.999 ** np.arange(n), "C": np.full(n, 100.0)}, index=idx)
    res = run_backtest(build_strategy("equal_weight", {"rebalance": "monthly"}), close, close, None, costs=NO_COST)
    signal_dates = [h["signal_date"] for h in res.weights_history]
    assert signal_dates == ["2024-01-01", "2024-01-31", "2024-02-29", "2024-03-29"]
    # After each rebalance execution, marked weights are back to 1/3.
    for h in res.weights_history:
        exec_date = pd.Timestamp(h["date"])
        pos = close.index.get_loc(exec_date)
        assert res.exposure["gross"].iloc[pos] == pytest.approx(1.0, abs=1e-9)
        assert all(v == pytest.approx(1 / 3) for v in h["weights"].values())


def test_invalid_strategy_weights_rejected():
    px = make_prices({"A": [100.0] * 5, "B": [100.0] * 5})

    class Levered(SpyStrategy):
        weights: ClassVar[dict[str, float]] = {"A": 0.8, "B": 0.8}

    with pytest.raises(ConfigurationError, match="exceeds 100%"):
        run_backtest(Levered(), px, px, None, costs=NO_COST)

    class Short(SpyStrategy):
        weights: ClassVar[dict[str, float]] = {"A": 1.0, "B": -0.2}

    with pytest.raises(ConfigurationError, match="negative"):
        run_backtest(Short(), px, px, None, costs=NO_COST)


def test_long_only_momentum_weights_are_valid(panel):
    close, open_ = _universe(panel)
    res = run_backtest(build_strategy("momentum", {"top_n": 6}), close, open_, None, "2020-01-02", "2022-12-30")
    for h in res.weights_history:
        w = np.array(list(h["weights"].values()))
        assert (w >= 0).all() and w.sum() <= 1 + 1e-5 and len(w) <= 6  # history rounds to 6 dp


def test_long_short_momentum_is_dollar_neutral(panel):
    close, open_ = _universe(panel)
    res = run_backtest(
        build_strategy("momentum", {"top_n": 5, "long_only": False}), close, open_, None, "2020-01-02", "2021-12-31"
    )
    for h in res.weights_history:
        w = np.array(list(h["weights"].values()))
        assert w.sum() == pytest.approx(0.0, abs=1e-9) and np.abs(w).sum() == pytest.approx(1.0)


# --- 6. Missing data does not create false performance ----------------------------------------------


def test_missing_bars_do_not_create_or_destroy_performance():
    vals = [100.0, 100.0, 101.0, 102.0, np.nan, np.nan, np.nan, 110.0, 111.0]
    close = make_prices({"A": vals})
    res = run_backtest(build_strategy("equal_weight", {"rebalance": "monthly"}), close, close, None, costs=NO_COST)
    # Bought at 100 on day 1; final 111 → +11%, exactly the buy-and-hold result.
    assert res.equity.iloc[-1] / 1e6 - 1 == pytest.approx(0.11)
    assert res.missing_price_events == 3
    # While missing, the position is marked flat — no invented moves.
    assert res.net_returns.iloc[4:7].tolist() == pytest.approx([0.0, 0.0, 0.0])


def test_no_new_position_without_price_at_signal():
    close = make_prices({"A": [100.0, 100.0, 100.0, 100.0], "B": [np.nan, 50.0, 50.0, 50.0]})
    res = run_backtest(SpyStrategy(), close, close, None, costs=NO_COST)
    first = res.weights_history[0]
    assert "B" not in first["weights"]  # B had no close at the first signal date


def test_delisted_position_converted_to_cash():
    close = make_prices({"A": [100.0, 100.0, 90.0, np.nan, np.nan, np.nan], "B": [100.0] * 6})
    res = run_backtest(build_strategy("equal_weight", {"rebalance": "quarterly"}), close, close, None, costs=NO_COST)
    delist = [t for t in res.trades if t["side"] == "delist"]
    assert len(delist) == 1 and delist[0]["symbol"] == "A" and delist[0]["price"] == 90.0
    assert res.equity.iloc[-1] == pytest.approx(0.5e6 * 0.9 + 0.5e6)


# --- 7. Out-of-sample periods are respected ------------------------------------------------------------


def test_segments_are_chronological_and_disjoint():
    s = make_segments(pd.Timestamp("2020-01-01"), pd.Timestamp("2022-12-31"), "2021-06-30", "2022-03-31")
    assert [x[0] for x in s] == ["in_sample", "validation", "out_of_sample"]
    assert s[0][2] < s[1][1] and s[1][2] < s[2][1]
    with pytest.raises(ConfigurationError):
        make_segments(pd.Timestamp("2020-01-01"), pd.Timestamp("2022-12-31"), "2023-01-01", None)


def test_grid_search_ignores_post_training_data(panel):
    close, open_ = _universe(panel)
    data = MarketData(close, open_, close * 0, None)
    te = pd.Timestamp("2021-06-30")
    grid = {"lookback": [63, 126]}
    a = grid_search("momentum", {"top_n": 5}, grid, data, pd.Timestamp("2020-01-02"), te, NO_COST, "next_open", 0.0)
    shocked = close.copy()
    shocked.loc[shocked.index > te] *= 3.0
    b = grid_search(
        "momentum",
        {"top_n": 5},
        grid,
        MarketData(shocked, open_, close * 0, None),
        pd.Timestamp("2020-01-02"),
        te,
        NO_COST,
        "next_open",
        0.0,
    )
    assert a["best_params"] == b["best_params"] and a["results"] == b["results"]


def test_segment_metrics_use_only_segment_returns(panel):
    close, open_ = _universe(panel)
    res = run_backtest(build_strategy("equal_weight"), close, open_, None, "2020-01-02", "2022-12-30")
    segs = make_segments(res.dates[0], res.dates[-1], "2021-06-30", None)
    m = segment_metrics(res, segs, None, 0.0)
    expected = (1 + res.net_returns.loc[:"2021-06-30"]).prod() - 1
    assert m["in_sample"]["cumulative_return"] == pytest.approx(expected)
    assert m["out_of_sample"]["start_date"] > "2021-06-30"


def test_walk_forward_folds_do_not_overlap(panel):
    close, open_ = _universe(panel)
    wf = walk_forward(
        "momentum",
        {"top_n": 5},
        {"lookback": [63, 126]},
        MarketData(close, open_, close * 0, None),
        pd.Timestamp("2019-06-03"),
        pd.Timestamp("2022-12-30"),
        252,
        126,
        NO_COST,
        "next_open",
        0.0,
    )
    folds = wf["folds"]
    assert len(folds) >= 2
    for f in folds:
        assert f["train_end"] < f["test_start"]
    for a, b in itertools.pairwise(folds):
        assert a["test_end"] < b["test_start"]
    assert len(wf["series"]["dates"]) == len(set(wf["series"]["dates"]))  # stitched OOS has no duplicated days


def test_backtest_is_deterministic(panel):
    close, open_ = _universe(panel)
    a = run_backtest(build_strategy("mean_reversion"), close, open_, None, "2020-01-02", "2021-12-31")
    b = run_backtest(build_strategy("mean_reversion"), close, open_, None, "2020-01-02", "2021-12-31")
    assert a.equity.equals(b.equity) and a.trades == b.trades
