"""Transparent reference strategies used as baselines."""

from __future__ import annotations

import pandas as pd

from app.strategies.base import MarketHistory, Param, Strategy


class EqualWeightStrategy(Strategy):
    key = "equal_weight"
    name = "Equal-Weight Rebalanced (baseline)"
    description = "Holds every tradable asset at equal weight, rebalanced on schedule. A naive diversification baseline."
    PARAMS = (Param("rebalance", str, "monthly", "Signal schedule", choices=("weekly", "monthly", "quarterly")),)

    @property
    def warmup(self) -> int:
        return 1

    def target_weights(self, history: MarketHistory) -> pd.Series | None:
        live = history.close.iloc[-1].dropna().index
        if len(live) == 0:
            return None
        return pd.Series(1.0 / len(live), index=live)


class TrendFilterStrategy(Strategy):
    """Time-series trend: hold an asset only while its price is above its moving average."""

    key = "trend_filter"
    name = "Moving-Average Trend Filter"
    description = (
        "Each asset receives a fixed slot 1/N of capital while its close is above its N-day simple moving "
        "average, otherwise the slot is held in cash."
    )
    PARAMS = (
        Param("ma_window", int, 200, "Simple moving-average window (days)", 20, 400),
        Param("rebalance", str, "monthly", "Signal schedule", choices=("weekly", "monthly")),
    )

    @property
    def warmup(self) -> int:
        return self.params["ma_window"]

    def target_weights(self, history: MarketHistory) -> pd.Series | None:
        close = history.close
        if len(close) < self.warmup:
            return None
        block = close.iloc[-self.params["ma_window"] :]
        valid = block.notna().all()
        ma = block.mean()
        last = block.iloc[-1]
        eligible = valid[valid].index
        if len(eligible) == 0:
            return None
        above = [s for s in eligible if last[s] > ma[s]]
        self.signal_log.append({"date": history.date.date().isoformat(), "above_ma": above, "eligible": len(eligible)})
        return pd.Series(1.0 / len(eligible), index=above, dtype=float)
