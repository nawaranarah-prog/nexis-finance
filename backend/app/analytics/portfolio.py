"""Buy-and-rebalance portfolio simulation.

Weights drift with asset returns between rebalance dates. On a rebalance date the
portfolio is reset to target weights *at that day's close*, after that day's return
has been earned. Turnover is one-way: ``sum(|w_target - w_drifted|) / 2``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from app.core.errors import ConfigurationError, InsufficientDataError

RebalanceFrequency = Literal["none", "daily", "weekly", "monthly", "quarterly", "annually"]
REBALANCE_FREQUENCIES: tuple[str, ...] = ("none", "daily", "weekly", "monthly", "quarterly", "annually")

_PERIOD_CODES = {"weekly": "W", "monthly": "M", "quarterly": "Q", "annually": "Y"}


def rebalance_mask(index: pd.DatetimeIndex, frequency: str) -> np.ndarray:
    """Boolean mask marking the last trading day of each calendar period."""
    if frequency not in REBALANCE_FREQUENCIES:
        raise ConfigurationError(f"unknown rebalance frequency '{frequency}'", details={"allowed": REBALANCE_FREQUENCIES})
    n = len(index)
    if frequency == "none" or n == 0:
        return np.zeros(n, dtype=bool)
    if frequency == "daily":
        return np.ones(n, dtype=bool)
    periods = index.to_period(_PERIOD_CODES[frequency])
    codes = np.asarray(periods.asi8)
    mask = np.zeros(n, dtype=bool)
    mask[:-1] = codes[1:] != codes[:-1]
    mask[-1] = True  # final day closes the last period
    return mask


@dataclass
class PortfolioSimulation:
    returns: pd.Series
    values: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    rebalance_dates: list[pd.Timestamp]
    missing_filled: int
    notes: list[str] = field(default_factory=list)


def prepare_asset_returns(prices: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Returns from forward-filled prices, starting when every asset has a price.

    Forward-filling a gap means the asset shows a 0% return while its price is missing and the
    full move is realised when trading resumes — cumulative performance is therefore preserved
    rather than dropped. The number of filled cells is returned so it can be disclosed.
    """
    if prices.empty:
        raise InsufficientDataError("no price data available for the selected assets")
    first_valid = prices.apply(lambda c: c.first_valid_index())
    if first_valid.isna().any():
        missing = list(first_valid[first_valid.isna()].index)
        raise InsufficientDataError("some assets have no price data", details={"symbols": missing})
    start = max(first_valid)
    p = prices.loc[start:]
    missing = int(p.isna().sum().sum())
    p = p.ffill()
    rets = p.pct_change(fill_method=None).iloc[1:]
    return rets, missing


def simulate_portfolio(
    asset_returns: pd.DataFrame,
    target_weights: pd.Series,
    rebalance_frequency: str = "monthly",
    initial_capital: float = 1_000_000.0,
    missing_filled: int = 0,
) -> PortfolioSimulation:
    if asset_returns.shape[0] < 2:
        raise InsufficientDataError("portfolio simulation requires at least 2 return observations")
    missing_cols = [c for c in target_weights.index if c not in asset_returns.columns]
    if missing_cols:
        raise ConfigurationError("weights reference assets without returns", details={"symbols": missing_cols})
    if initial_capital <= 0:
        raise ConfigurationError("initial capital must be positive")

    cols = list(target_weights.index)
    r = asset_returns[cols].fillna(0.0).to_numpy(dtype=float)
    target = target_weights.to_numpy(dtype=float)
    # Any residual (1 - sum(w)) is held as cash earning 0%.
    cash_target = 1.0 - float(target.sum())
    mask = rebalance_mask(asset_returns.index, rebalance_frequency)

    n, k = r.shape
    w = target.copy()
    cash = cash_target
    port_r = np.empty(n)
    weights_hist = np.empty((n, k))
    turnover = np.zeros(n)
    for t in range(n):
        gross = w * (1.0 + r[t])
        total = float(gross.sum() + cash)
        port_r[t] = total - 1.0
        if total <= 0:
            raise InsufficientDataError("portfolio value fell to zero or below; simulation stopped")
        w = gross / total
        cash = cash / total
        if mask[t]:
            turnover[t] = (np.abs(target - w).sum() + abs(cash_target - cash)) / 2.0
            w = target.copy()
            cash = cash_target
        weights_hist[t] = w

    idx = asset_returns.index
    returns = pd.Series(port_r, index=idx, name="portfolio")
    values = initial_capital * (1.0 + returns).cumprod()
    notes = []
    if missing_filled:
        notes.append(f"{missing_filled} missing price observations were forward-filled (0% return while missing).")
    if abs(cash_target) > 1e-9:
        notes.append(f"{cash_target:.2%} of capital held as non-interest-bearing cash.")
    return PortfolioSimulation(
        returns=returns,
        values=values,
        weights=pd.DataFrame(weights_hist, index=idx, columns=cols),
        turnover=pd.Series(turnover, index=idx, name="turnover"),
        rebalance_dates=[d for d, m in zip(idx, mask, strict=True) if m],
        missing_filled=missing_filled,
        notes=notes,
    )
