"""Cross-sectional price momentum."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies.base import MarketHistory, Param, Strategy


class MomentumStrategy(Strategy):
    """Rank assets by their return from ``t-skip-lookback`` to ``t-skip`` and hold the top ``top_n``.

    The ``skip`` period excludes the most recent days, where short-term reversal tends to dominate.
    In long/short mode the bottom ``top_n`` are shorted; gross exposure is 100% (50% long, 50% short).
    """

    key = "momentum"
    name = "Cross-Sectional Momentum"
    description = (
        "Ranks assets by trailing return (skipping the most recent month), holds the strongest. "
        "Signals formed at the close of each rebalance date and executed on the next bar."
    )
    PARAMS = (
        Param("lookback", int, 126, "Momentum measurement window in trading days", 20, 504),
        Param("skip", int, 21, "Most recent days excluded from the momentum window", 0, 63),
        Param("top_n", int, 8, "Number of assets held on each side", 1, 50),
        Param("rebalance", str, "monthly", "Signal schedule", choices=("weekly", "monthly", "quarterly")),
        Param("long_only", bool, True, "Long-only (true) or long/short (false)"),
        Param("weighting", str, "equal", "Weighting of selected assets", choices=("equal", "inverse_vol")),
        Param("vol_lookback", int, 63, "Window for inverse-volatility weights", 20, 252),
        Param("absolute_filter", bool, False, "Only hold assets whose own momentum is positive (rest in cash)"),
    )

    @property
    def warmup(self) -> int:
        return self.params["lookback"] + self.params["skip"] + 1

    @property
    def allows_short(self) -> bool:
        return not self.params["long_only"]

    def scores(self, close: pd.DataFrame) -> pd.Series:
        lb, sk = self.params["lookback"], self.params["skip"]
        end = close.iloc[-1 - sk]
        start = close.iloc[-1 - sk - lb]
        current = close.iloc[-1]
        mom = end / start - 1.0
        # Require a price today (tradable) and at both ends of the window.
        return mom[current.notna() & end.notna() & start.notna()]

    def target_weights(self, history: MarketHistory) -> pd.Series | None:
        close = history.close
        if len(close) < self.warmup:
            return None
        s = self.scores(close).sort_values(ascending=False)
        n = self.params["top_n"]
        if len(s) < (n if self.params["long_only"] else 2 * n):
            return None
        longs = s.head(n)
        if self.params["absolute_filter"]:
            longs = longs[longs > 0]
        shorts = pd.Series(dtype=float) if self.params["long_only"] else s.tail(n)
        if self.params["absolute_filter"] and not shorts.empty:
            shorts = shorts[shorts < 0]

        def side_weights(idx: pd.Index, budget: float) -> pd.Series:
            if len(idx) == 0:
                return pd.Series(dtype=float)
            if self.params["weighting"] == "inverse_vol":
                vol = close[idx].pct_change(fill_method=None).iloc[-self.params["vol_lookback"] :].std()
                inv = 1.0 / vol.replace(0, np.nan)
                inv = inv.fillna(inv.mean() if inv.notna().any() else 1.0)
                return budget * inv / inv.sum()
            # Fixed slot size: filtered-out slots stay in cash rather than levering survivors.
            return pd.Series(budget / n, index=idx)

        long_budget = 1.0 if self.params["long_only"] else 0.5
        w = side_weights(longs.index, long_budget)
        if not self.params["long_only"]:
            w = pd.concat([w, -side_weights(shorts.index, 0.5)])
        self.signal_log.append(
            {
                "date": history.date.date().isoformat(),
                "selected_long": list(longs.index),
                "selected_short": list(shorts.index),
                "top_scores": {k: round(float(v), 5) for k, v in s.head(10).items()},
                "bottom_scores": {k: round(float(v), 5) for k, v in s.tail(5).items()},
                "universe_size": len(s),
            }
        )
        return w
