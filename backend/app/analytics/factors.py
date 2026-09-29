"""Market-derived factor proxies and factor-exposure regressions.

No accounting fundamentals are available in the datasets supported here, so every factor is
built from prices and volumes only and is labelled as a *proxy*:

* ``momentum``   — 12-1 month total return (252-day return skipping the latest 21 days)
* ``low_vol``    — negative of 63-day realised volatility (low-volatility anomaly proxy)
* ``size``       — negative log of 63-day average dollar volume (liquidity-based size proxy;
                   small = less traded). Not market capitalisation.
* ``value``      — negative 3-year (756-day) return, i.e. long-term reversal, a common price-based
                   stand-in for value. It is *not* book-to-market.
* ``quality``    — negative 252-day maximum drawdown magnitude (return stability proxy).
                   Not profitability or balance-sheet quality.

Factor returns are long-short quintile spreads: at each month-end the cross-section is ranked
using information available at that close; the top-minus-bottom equal-weighted portfolio is held
over the following month. Characteristic dates therefore strictly precede return dates.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from app.core.errors import InsufficientDataError

FACTOR_DEFINITIONS: dict[str, str] = {
    "momentum": "12-1 month price momentum (return from t-252 to t-21)",
    "low_vol": "Negative 63-day realised volatility (low-volatility proxy)",
    "size": "Negative log 63-day average dollar volume (liquidity-based size proxy, not market cap)",
    "value": "Negative 3-year return (long-term reversal proxy, not book-to-market)",
    "quality": "Negative 1-year maximum drawdown magnitude (stability proxy, not accounting quality)",
}


def factor_characteristics(close: pd.DataFrame, volume: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Daily cross-sectional characteristics; each value at t uses data up to t only."""
    rets = close.pct_change(fill_method=None)
    dollar_vol = (close * volume).rolling(63, min_periods=40).mean()
    rolling_max = close.rolling(252, min_periods=200).max()
    dd = (close / rolling_max - 1.0).rolling(252, min_periods=200).min()
    return {
        "momentum": close.shift(21) / close.shift(252) - 1.0,
        "low_vol": -rets.rolling(63, min_periods=50).std(),
        "size": -np.log(dollar_vol.where(dollar_vol > 0)),
        "value": -(close / close.shift(756) - 1.0),
        "quality": dd,  # max drawdown is negative; larger (closer to 0) = more stable
    }


def factor_returns(
    close: pd.DataFrame, volume: pd.DataFrame, quantile: float = 0.2, min_assets: int = 10
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Daily long-short factor return series from monthly-rebalanced quintile sorts."""
    if close.shape[1] < min_assets:
        raise InsufficientDataError(f"factor construction needs at least {min_assets} assets; got {close.shape[1]}")
    chars = factor_characteristics(close, volume)
    daily = close.pct_change(fill_method=None)
    month_ends = close.groupby(close.index.to_period("M")).tail(1).index
    out = pd.DataFrame(index=daily.index, columns=list(chars), dtype=float)
    coverage: dict[str, int] = {}
    for name, ch in chars.items():
        long_w = pd.DataFrame(0.0, index=daily.index, columns=close.columns)
        short_w = long_w.copy()
        formed = 0
        for i, me in enumerate(month_ends[:-1]):
            x = ch.loc[me].dropna()
            if len(x) < min_assets:
                continue
            k = max(1, round(len(x) * quantile))
            ranked = x.sort_values()
            longs, shorts = ranked.index[-k:], ranked.index[:k]
            nxt = month_ends[i + 1]
            period = (daily.index > me) & (daily.index <= nxt)
            long_w.loc[period, longs] = 1.0 / k
            short_w.loc[period, shorts] = 1.0 / k
            formed += 1
        coverage[name] = formed
        r = daily.fillna(0.0)
        active = long_w.sum(axis=1) > 0
        series = (long_w * r).sum(axis=1) - (short_w * r).sum(axis=1)
        out[name] = series.where(active)
    out = out.dropna(how="all")
    return out, {"formation_months": coverage, "quantile": quantile}


def factor_exposure(portfolio_returns: pd.Series, factors: pd.DataFrame, market: pd.Series | None = None) -> dict[str, Any]:
    """OLS of portfolio returns on (market +) factor returns with HAC (Newey-West) standard errors."""
    X = factors.copy()
    if market is not None:
        X.insert(0, "market", market)
    df = pd.concat([portfolio_returns.rename("y"), X], axis=1, join="inner").dropna()
    if len(df) < 60:
        raise InsufficientDataError(
            "factor regression needs at least 60 overlapping observations", details={"available": len(df)}
        )
    y = df["y"]
    Xc = sm.add_constant(df.drop(columns="y"))
    model = sm.OLS(y, Xc).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    rows = []
    for name in Xc.columns:
        rows.append(
            {
                "factor": "intercept (daily)" if name == "const" else name,
                "beta": float(model.params[name]),
                "t_stat": float(model.tvalues[name]),
                "p_value": float(model.pvalues[name]),
                "std_error": float(model.bse[name]),
            }
        )
    return {
        "coefficients": rows,
        "r_squared": float(model.rsquared),
        "adj_r_squared": float(model.rsquared_adj),
        "observations": int(model.nobs),
        "start_date": df.index[0].date().isoformat(),
        "end_date": df.index[-1].date().isoformat(),
        "standard_errors": "Newey-West HAC, 5 lags",
    }


def rolling_factor_betas(portfolio_returns: pd.Series, factors: pd.DataFrame, window: int = 126) -> pd.DataFrame:
    """Rolling multivariate OLS betas (trailing window, no look-ahead)."""
    df = pd.concat([portfolio_returns.rename("y"), factors], axis=1, join="inner").dropna()
    if len(df) < window + 5:
        raise InsufficientDataError(f"rolling factor betas need at least {window + 5} observations")
    y = df["y"].to_numpy()
    X = np.column_stack([np.ones(len(df)), df.drop(columns="y").to_numpy()])
    names = list(df.columns.drop("y"))
    out = np.full((len(df), len(names)), np.nan)
    for t in range(window - 1, len(df)):
        xs, ys = X[t - window + 1 : t + 1], y[t - window + 1 : t + 1]
        coef, *_ = np.linalg.lstsq(xs, ys, rcond=None)
        out[t] = coef[1:]
    return pd.DataFrame(out, index=df.index, columns=names).dropna()
