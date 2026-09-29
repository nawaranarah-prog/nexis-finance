"""Rolling-window statistics. Every value at date t uses observations up to and including t only."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.analytics.metrics import TRADING_DAYS, per_period_rate


def rolling_volatility(
    returns: pd.Series | pd.DataFrame, window: int, periods_per_year: int = TRADING_DAYS
) -> pd.Series | pd.DataFrame:
    return returns.rolling(window, min_periods=window).std(ddof=1) * math.sqrt(periods_per_year)


def rolling_sharpe(
    returns: pd.Series, window: int, risk_free_rate: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> pd.Series:
    excess = returns - per_period_rate(risk_free_rate, periods_per_year)
    mean = excess.rolling(window, min_periods=window).mean()
    sd = excess.rolling(window, min_periods=window).std(ddof=1)
    out = mean / sd.where(sd > 1e-12) * math.sqrt(periods_per_year)
    return out


def rolling_beta(returns: pd.Series, benchmark: pd.Series, window: int) -> pd.Series:
    df = pd.concat([returns.rename("a"), benchmark.rename("b")], axis=1).dropna()
    cov = df["a"].rolling(window, min_periods=window).cov(df["b"])
    var = df["b"].rolling(window, min_periods=window).var()
    return cov / var.where(var > 1e-12)


def rolling_correlation(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    return df["a"].rolling(window, min_periods=window).corr(df["b"])


def moving_average(prices: pd.Series, window: int) -> pd.Series:
    return prices.rolling(window, min_periods=window).mean()


def return_distribution(returns: pd.Series, frequency: str = "daily") -> pd.Series:
    """Compound daily returns into weekly or monthly returns (calendar periods)."""
    r = returns.dropna()
    if frequency == "daily":
        return r
    rule = {"weekly": "W-FRI", "monthly": "ME"}[frequency]
    return (1.0 + r).resample(rule).prod().sub(1.0).dropna()


def monthly_return_table(returns: pd.Series) -> list[dict[str, float | int | None]]:
    """Year x month grid of compounded monthly returns (for heatmaps)."""
    m = (1.0 + returns.dropna()).resample("ME").prod() - 1.0
    rows = []
    for ts, v in m.items():
        rows.append({"year": int(ts.year), "month": int(ts.month), "return": float(v)})
    return rows


def histogram(values: pd.Series | np.ndarray, bins: int = 50) -> dict[str, list[float]]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"edges": [], "counts": []}
    counts, edges = np.histogram(arr, bins=bins)
    return {"edges": edges.tolist(), "counts": counts.tolist()}
