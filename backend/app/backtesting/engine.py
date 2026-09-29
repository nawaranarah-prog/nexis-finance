"""Event-driven daily backtest engine.

Timeline for trading day *t* (index ``i``)
------------------------------------------
1. **Execute** orders generated at the close of *t−1*, if ``execution == "next_open"``,
   at *t*'s open (slippage-adjusted).
2. **Mark to market** at *t*'s close (last available close is carried for missing bars).
3. **Execute** orders from *t−1* if ``execution == "next_close"`` at *t*'s close.
4. **Signal**: if *t* is a signal date, call the strategy with data truncated at *t*'s close.
   The resulting order can only be executed on *t+1* — never at the price used to form it.

Costs
-----
``commission_bps`` and ``slippage_bps`` are charged on the absolute traded notional. Slippage is
modelled as an adverse execution price (buy at ``p·(1+s)``, sell at ``p·(1−s)``). Target position
values are sized off equity *net of estimated costs* so cash never goes materially negative.

Missing data
------------
* An asset without a price on the execution bar cannot be traded; its existing position is held.
* Positions are valued at the last available close while bars are missing, so the full price move
  is recognised when trading resumes (no performance is created or dropped by the gap).
* Once an asset's history ends (delisting), a held position is converted to cash at its final
  close on the next bar, and recorded as a ``delist`` trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from app.analytics.portfolio import rebalance_mask
from app.core.errors import ConfigurationError, InsufficientDataError
from app.strategies.base import MarketHistory, Strategy

Execution = Literal["next_open", "next_close"]


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 5.0
    slippage_bps: float = 5.0

    def __post_init__(self) -> None:
        for name in ("commission_bps", "slippage_bps"):
            v = getattr(self, name)
            if not math.isfinite(v) or v < 0 or v > 500:
                raise ConfigurationError(f"{name} must be between 0 and 500 basis points")

    @property
    def total_rate(self) -> float:
        return (self.commission_bps + self.slippage_bps) / 1e4


@dataclass
class BacktestResult:
    dates: pd.DatetimeIndex
    equity: pd.Series
    net_returns: pd.Series
    gross_returns: pd.Series
    costs: pd.Series
    turnover: pd.Series
    exposure: pd.DataFrame  # gross/net/long/short exposure by day
    trades: list[dict[str, Any]]
    weights_history: list[dict[str, Any]]
    signal_log: list[dict[str, Any]]
    missing_price_events: int
    notes: list[str] = field(default_factory=list)

    @property
    def total_costs(self) -> float:
        return float(self.costs.sum())


def run_backtest(
    strategy: Strategy,
    close: pd.DataFrame,
    open_: pd.DataFrame | None = None,
    volume: pd.DataFrame | None = None,
    start: pd.Timestamp | str | None = None,
    end: pd.Timestamp | str | None = None,
    initial_capital: float = 1_000_000.0,
    costs: CostModel = CostModel(),
    execution: Execution = "next_open",
    record_trades: bool = True,
) -> BacktestResult:
    """Run ``strategy`` over ``[start, end]``. Data before ``start`` is available as signal warm-up."""
    if execution not in ("next_open", "next_close"):
        raise ConfigurationError("execution must be 'next_open' or 'next_close'")
    if initial_capital <= 0:
        raise ConfigurationError("initial capital must be positive")
    if close.empty:
        raise InsufficientDataError("no price data supplied to the backtest")
    if not close.index.is_monotonic_increasing or close.index.has_duplicates:
        raise ConfigurationError("price index must be strictly increasing")
    open_ = close if open_ is None else open_.reindex_like(close)
    volume = pd.DataFrame(np.nan, index=close.index, columns=close.columns) if volume is None else volume.reindex_like(close)
    if execution == "next_open" and open_ is close:
        raise ConfigurationError("next_open execution requires open prices")

    start_ts = pd.Timestamp(start) if start is not None else close.index[0]
    end_ts = pd.Timestamp(end) if end is not None else close.index[-1]
    all_idx = close.index
    i0 = int(all_idx.searchsorted(start_ts, side="left"))
    i1 = int(all_idx.searchsorted(end_ts, side="right")) - 1
    if i1 - i0 < 2:
        raise InsufficientDataError("backtest window must contain at least 3 trading days")
    if i0 + 1 < strategy.warmup:
        # Not enough warm-up history before the start: the first signals are delayed until warm.
        pass

    symbols = list(close.columns)
    k = len(symbols)
    mark_close = close.ffill()
    close_np = close.to_numpy(dtype=float)
    open_np = open_.to_numpy(dtype=float)
    mark_np = mark_close.to_numpy(dtype=float)
    last_valid_pos = np.array(
        [all_idx.get_loc(close[c].last_valid_index()) if close[c].last_valid_index() is not None else -1 for c in symbols]
    )
    window_idx = all_idx[i0 : i1 + 1]
    signal_days = rebalance_mask(window_idx, strategy.rebalance_frequency)
    signal_days[0] = True  # form an initial portfolio at the first close
    signal_days[-1] = False  # a signal on the final day could never be executed

    strategy.reset()
    shares = np.zeros(k)
    cash = float(initial_capital)
    prev_value = float(initial_capital)
    n = i1 - i0 + 1
    equity = np.empty(n)
    net_r = np.empty(n)
    cost_arr = np.zeros(n)
    turn_arr = np.zeros(n)
    expo = np.zeros((n, 4))
    trades: list[dict[str, Any]] = []
    weights_hist: list[dict[str, Any]] = []
    pending: tuple[pd.Timestamp, np.ndarray] | None = None
    missing_events = 0
    warmup_skipped = 0
    slip = costs.slippage_bps / 1e4
    comm = costs.commission_bps / 1e4

    def execute(j: int, px_raw: np.ndarray, target_w: np.ndarray, signal_date: pd.Timestamp, pos: int) -> tuple[float, float]:
        nonlocal cash, shares
        marks = mark_np[j - 1] if j > 0 else px_raw
        valuation_px = np.where(np.isfinite(px_raw), px_raw, marks)
        valuation_px = np.where(np.isfinite(valuation_px), valuation_px, 0.0)
        value = cash + float(shares @ valuation_px)
        if value <= 0:
            raise InsufficientDataError("portfolio equity is non-positive; backtest halted")
        tradable = np.isfinite(px_raw) & (px_raw > 0)
        cur_val = shares * valuation_px
        desired = target_w * value
        # Untradable assets keep their current value; the rest is sized on equity net of estimated costs.
        est_cost = costs.total_rate * float(np.abs(np.where(tradable, desired - cur_val, 0.0)).sum())
        desired = target_w * (value - est_cost)
        delta_val = np.where(tradable, desired - cur_val, 0.0)
        delta_val[np.abs(delta_val) < 1e-8 * value] = 0.0
        day_cost = day_notional = 0.0
        date = all_idx[j]
        w_before = cur_val / value
        for a in np.nonzero(delta_val)[0]:
            p = px_raw[a]
            side = 1.0 if delta_val[a] > 0 else -1.0
            fill = p * (1.0 + side * slip)
            qty = delta_val[a] / p  # shares traded
            notional = abs(qty) * p
            slip_cost = notional * slip
            commission = notional * comm
            shares[a] += qty
            cash -= qty * fill + commission
            day_cost += slip_cost + commission
            day_notional += notional
            if record_trades:
                trades.append(
                    {
                        "signal_date": signal_date.date().isoformat(),
                        "date": date.date().isoformat(),
                        "symbol": symbols[a],
                        "side": "buy" if side > 0 else "sell",
                        "shares": float(qty),
                        "price": float(fill),
                        "notional": float(notional),
                        "commission": float(commission),
                        "slippage": float(slip_cost),
                        "weight_before": float(w_before[a]),
                        "weight_after": float(target_w[a]),
                    }
                )
        weights_hist.append(
            {
                "date": date.date().isoformat(),
                "signal_date": signal_date.date().isoformat(),
                "weights": {symbols[a]: round(float(target_w[a]), 6) for a in np.nonzero(target_w)[0]},
            }
        )
        return day_cost, day_notional / value

    for pos in range(n):
        j = i0 + pos
        date = all_idx[j]
        day_cost = 0.0
        day_turn = 0.0
        # Delisted positions: convert to cash at the final close on the first bar after history ends.
        for a in np.nonzero(shares)[0]:
            if last_valid_pos[a] >= 0 and j > last_valid_pos[a]:
                px = float(mark_np[last_valid_pos[a], a])
                cash += shares[a] * px
                if record_trades:
                    trades.append(
                        {
                            "signal_date": date.date().isoformat(),
                            "date": date.date().isoformat(),
                            "symbol": symbols[a],
                            "side": "delist",
                            "shares": float(-shares[a]),
                            "price": px,
                            "notional": float(abs(shares[a]) * px),
                            "commission": 0.0,
                            "slippage": 0.0,
                            "weight_before": float(shares[a] * px / max(prev_value, 1e-9)),
                            "weight_after": 0.0,
                        }
                    )
                shares[a] = 0.0
        if pending is not None and execution == "next_open":
            c, t = execute(j, open_np[j], pending[1], pending[0], pos)
            day_cost += c
            day_turn += t
            pending = None

        raw_close = close_np[j]
        held = shares != 0
        missing_events += int(np.sum(held & ~np.isfinite(raw_close)))
        marks = mark_np[j]
        marks = np.where(np.isfinite(marks), marks, 0.0)

        if pending is not None and execution == "next_close":
            c, t = execute(j, raw_close, pending[1], pending[0], pos)
            day_cost += c
            day_turn += t
            pending = None

        value = cash + float(shares @ marks)
        if value <= 0:
            raise InsufficientDataError(f"portfolio equity became non-positive on {date.date()}")
        equity[pos] = value
        net_r[pos] = value / prev_value - 1.0
        cost_arr[pos] = day_cost
        turn_arr[pos] = day_turn
        pv = shares * marks / value
        expo[pos] = [np.abs(pv).sum(), pv.sum(), pv[pv > 0].sum(), pv[pv < 0].sum()]
        prev_value = value

        if signal_days[pos]:
            if j + 1 < strategy.warmup:
                warmup_skipped += 1
            else:
                # Only the trailing warm-up window up to and including today's close is exposed.
                lo = max(0, j + 1 - strategy.warmup)
                hist = MarketHistory(close.iloc[lo : j + 1], open_.iloc[lo : j + 1], volume.iloc[lo : j + 1])
                w = strategy.target_weights(hist)
                if w is not None:
                    pending = (date, _validate_targets(w, symbols, raw_close, strategy.allows_short))

    idx = window_idx
    net = pd.Series(net_r, index=idx, name="net_return")
    prev_eq = np.concatenate([[initial_capital], equity[:-1]])
    gross = pd.Series((equity + cost_arr) / prev_eq - 1.0, index=idx, name="gross_return")
    notes = [f"Signals formed at the close; orders executed at the {execution.replace('_', ' ')}."]
    if missing_events:
        notes.append(f"{missing_events} position-days were valued at the last available close due to missing bars.")
    if warmup_skipped:
        notes.append(f"{warmup_skipped} early signal date(s) skipped: insufficient history for warm-up ({strategy.warmup} bars).")
    if not trades:
        notes.append("The strategy generated no trades in this window.")
    return BacktestResult(
        dates=idx,
        equity=pd.Series(equity, index=idx, name="equity"),
        net_returns=net,
        gross_returns=gross,
        costs=pd.Series(cost_arr, index=idx, name="costs"),
        turnover=pd.Series(turn_arr, index=idx, name="turnover"),
        exposure=pd.DataFrame(expo, index=idx, columns=["gross", "net", "long", "short"]),
        trades=trades,
        weights_history=weights_hist,
        signal_log=list(strategy.signal_log),
        missing_price_events=missing_events,
        notes=notes,
    )


def _validate_targets(w: pd.Series, symbols: list[str], current_close: np.ndarray, allow_short: bool) -> np.ndarray:
    unknown = set(w.index) - set(symbols)
    if unknown:
        raise ConfigurationError(f"strategy returned weights for unknown symbols: {sorted(unknown)[:5]}")
    vec = w.reindex(symbols).fillna(0.0).to_numpy(dtype=float)
    if not np.all(np.isfinite(vec)):
        raise ConfigurationError("strategy returned non-finite weights")
    if not allow_short and np.any(vec < -1e-12):
        raise ConfigurationError("long-only strategy returned negative weights")
    if np.abs(vec).sum() > 1.0 + 1e-6:
        raise ConfigurationError(f"strategy gross exposure {np.abs(vec).sum():.4f} exceeds 100% (no leverage allowed)")
    # A strategy cannot hold a new position in an asset that has no price at the signal close.
    return np.where(np.isfinite(current_close), vec, 0.0)
