"""Z-score mean reversion with hysteresis (separate entry and exit thresholds)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.core.errors import ConfigurationError
from app.strategies.base import MarketHistory, Param, Strategy


def zscore_last(close: pd.DataFrame, window: int) -> pd.Series:
    """z_t = (P_t − mean(P_{t−w+1..t})) / std(P_{t−w+1..t}) for the last row only."""
    arr = close.to_numpy(dtype=float)[-window:]
    valid = ~np.isnan(arr).any(axis=0) & (arr.shape[0] == window)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = arr.mean(axis=0)
        sd = arr.std(axis=0, ddof=1)
        z = np.where(sd > 1e-12, (arr[-1] - mean) / sd, np.nan)
    return pd.Series(z, index=close.columns)[valid]


def zscore_series(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """Vectorised equivalent used for charts; identical to calling :func:`zscore_last` at every t."""
    mean = close.rolling(window, min_periods=window).mean()
    sd = close.rolling(window, min_periods=window).std(ddof=1)
    return (close - mean) / sd.where(sd > 1e-12)


class MeanReversionStrategy(Strategy):
    """Enter long when z ≤ −entry_z; exit when z ≥ −exit_z (or after ``max_holding_days``).
    With shorts enabled, the mirror rule applies. Each open position gets a fixed slot of
    ``1 / max_positions`` of capital; unused slots are held in cash.
    """

    key = "mean_reversion"
    name = "Z-Score Mean Reversion"
    description = (
        "Buys assets trading far below their rolling mean (in standard deviations) and exits "
        "as the price reverts. Entry/exit thresholds create hysteresis to limit churn."
    )
    PARAMS = (
        Param("window", int, 20, "Rolling window for mean and standard deviation", 5, 250),
        Param("entry_z", float, 2.0, "Absolute z-score required to open a position", 0.5, 5.0),
        Param("exit_z", float, 0.25, "Position closes once |z| falls to this level (0 = at the mean)", 0.0, 4.0),
        Param("max_positions", int, 10, "Maximum simultaneous positions (fixed slot size 1/N)", 1, 50),
        Param("max_holding_days", int, 20, "Force exit after this many signal days (0 disables)", 0, 250),
        Param("allow_short", bool, False, "Also short assets trading far above their mean"),
        Param("rebalance", str, "daily", "Signal schedule", choices=("daily", "weekly")),
    )

    def validate_params(self) -> None:
        if self.params["exit_z"] >= self.params["entry_z"]:
            raise ConfigurationError("exit_z must be smaller than entry_z")

    def reset(self) -> None:
        super().reset()
        self.positions: dict[str, dict[str, Any]] = {}

    @property
    def warmup(self) -> int:
        return self.params["window"]

    @property
    def allows_short(self) -> bool:
        return bool(self.params["allow_short"])

    def target_weights(self, history: MarketHistory) -> pd.Series | None:
        close = history.close
        if len(close) < self.warmup:
            return None
        p = self.params
        z = zscore_last(close, p["window"])
        today = history.date.date().isoformat()
        changed = False
        events: list[dict[str, Any]] = []

        for sym in list(self.positions):
            pos = self.positions[sym]
            pos["days"] += 1
            zs = z.get(sym, np.nan)
            reason = None
            if np.isnan(zs):
                reason = "no_price"
            elif (pos["side"] > 0 and zs >= -p["exit_z"]) or (pos["side"] < 0 and zs <= p["exit_z"]):
                reason = "reverted"
            elif p["max_holding_days"] and pos["days"] >= p["max_holding_days"]:
                reason = "max_holding"
            if reason:
                events.append({"symbol": sym, "action": "exit", "side": pos["side"], "z": _r(zs), "reason": reason})
                del self.positions[sym]
                changed = True

        slots = p["max_positions"] - len(self.positions)
        if slots > 0:
            cands = []
            for sym, zs in z.items():
                if sym in self.positions or np.isnan(zs):
                    continue
                if zs <= -p["entry_z"]:
                    cands.append((abs(zs), sym, 1))
                elif p["allow_short"] and zs >= p["entry_z"]:
                    cands.append((abs(zs), sym, -1))
            for _, sym, side in sorted(cands, reverse=True)[:slots]:
                self.positions[sym] = {"side": side, "entry": today, "days": 0}
                events.append({"symbol": sym, "action": "enter", "side": side, "z": _r(z[sym])})
                changed = True

        if events:
            self.signal_log.append({"date": today, "events": events, "open_positions": len(self.positions)})
        if not changed:
            return None
        slot = 1.0 / p["max_positions"]
        return pd.Series({s: slot * v["side"] for s, v in self.positions.items()}, dtype=float)


def _r(v: float) -> float | None:
    return None if v is None or np.isnan(v) else round(float(v), 4)
