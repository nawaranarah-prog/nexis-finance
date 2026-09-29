"""Performance and risk-adjusted return metrics.

Conventions
-----------
* Inputs are periodic *simple* returns (e.g. daily), as a ``pd.Series`` or array.
* NaNs are dropped before calculation; the caller is responsible for aligning series.
* ``periods_per_year`` defaults to 252 trading days.
* When a statistic is mathematically undefined (e.g. Sharpe with zero variance)
  the function returns ``None`` rather than ``inf``/``nan``. When there are too
  few observations for a meaningful estimate, :class:`InsufficientDataError` is raised.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from app.core.errors import InsufficientDataError

TRADING_DAYS = 252
_EPS = 1e-12

ArrayLike = pd.Series | np.ndarray | list[float]


def _as_series(returns: ArrayLike, name: str = "returns") -> pd.Series:
    s = returns if isinstance(returns, pd.Series) else pd.Series(np.asarray(returns, dtype=float))
    s = s.astype(float)
    if np.isinf(s.to_numpy()).any():
        raise InsufficientDataError(f"{name} contain infinite values")
    return s.dropna()


def _require(s: pd.Series, n: int, what: str) -> None:
    if len(s) < n:
        raise InsufficientDataError(
            f"{what} requires at least {n} observations; got {len(s)}",
            details={"required": n, "available": len(s)},
        )


def per_period_rate(annual_rate: float, periods_per_year: int = TRADING_DAYS) -> float:
    """Convert an annual compounded rate into its per-period equivalent."""
    return (1.0 + annual_rate) ** (1.0 / periods_per_year) - 1.0


# --- Returns ---------------------------------------------------------------------------


def simple_returns(prices: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """Close-to-close simple returns. Missing prices propagate as NaN (never silently filled)."""
    if (prices <= 0).to_numpy().any():
        raise InsufficientDataError("prices must be strictly positive to compute returns")
    return prices.pct_change(fill_method=None).iloc[1:]


def log_returns(prices: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    if (prices <= 0).to_numpy().any():
        raise InsufficientDataError("prices must be strictly positive to compute log returns")
    return np.log(prices / prices.shift(1)).iloc[1:]


def cumulative_return(returns: ArrayLike) -> float:
    s = _as_series(returns)
    _require(s, 1, "cumulative return")
    return float(np.prod(1.0 + s.to_numpy()) - 1.0)


def wealth_index(returns: pd.Series, start: float = 1.0) -> pd.Series:
    return start * (1.0 + returns.fillna(0.0)).cumprod()


def annualized_return(returns: ArrayLike, periods_per_year: int = TRADING_DAYS) -> float:
    """Geometric (CAGR-style) annualised return: ``prod(1+r)^(P/n) - 1``."""
    s = _as_series(returns)
    _require(s, 2, "annualised return")
    growth = float(np.prod(1.0 + s.to_numpy()))
    if growth <= 0:
        return -1.0  # total loss; the geometric rate is bounded at -100%
    return growth ** (periods_per_year / len(s)) - 1.0


def annualized_volatility(returns: ArrayLike, periods_per_year: int = TRADING_DAYS) -> float:
    s = _as_series(returns)
    _require(s, 2, "volatility")
    return float(s.std(ddof=1) * math.sqrt(periods_per_year))


def downside_deviation(returns: ArrayLike, mar: float = 0.0, periods_per_year: int = TRADING_DAYS) -> float:
    """Annualised root-mean-square of returns below the per-period minimum acceptable return."""
    s = _as_series(returns)
    _require(s, 2, "downside deviation")
    shortfall = np.minimum(s.to_numpy() - mar, 0.0)
    return float(math.sqrt(np.mean(shortfall**2)) * math.sqrt(periods_per_year))


# --- Risk-adjusted ---------------------------------------------------------------------


def sharpe_ratio(returns: ArrayLike, risk_free_rate: float = 0.0, periods_per_year: int = TRADING_DAYS) -> float | None:
    """Annualised Sharpe ratio: ``mean(r - rf) / std(r - rf) * sqrt(P)``. ``None`` if variance is zero."""
    s = _as_series(returns)
    _require(s, 2, "Sharpe ratio")
    excess = s.to_numpy() - per_period_rate(risk_free_rate, periods_per_year)
    sd = float(np.std(excess, ddof=1))
    if sd < _EPS:
        return None
    return float(np.mean(excess) / sd * math.sqrt(periods_per_year))


def sortino_ratio(returns: ArrayLike, risk_free_rate: float = 0.0, periods_per_year: int = TRADING_DAYS) -> float | None:
    """Annualised mean excess return divided by annualised downside deviation (MAR = risk-free)."""
    s = _as_series(returns)
    _require(s, 2, "Sortino ratio")
    rf = per_period_rate(risk_free_rate, periods_per_year)
    dd = downside_deviation(s, mar=rf, periods_per_year=periods_per_year)
    if dd < _EPS:
        return None
    return float(np.mean(s.to_numpy() - rf) * periods_per_year / dd)


def drawdown_series(returns: pd.Series) -> pd.Series:
    """Percentage decline of the wealth index from its running peak (0 at new highs).

    The peak includes the initial wealth of 1.0, so a loss on the first day is a drawdown.
    """
    s = returns.fillna(0.0).astype(float)
    wealth = (1.0 + s).cumprod()
    peak = np.maximum.accumulate(np.concatenate([[1.0], wealth.to_numpy()]))[1:]
    return pd.Series(wealth.to_numpy() / peak - 1.0, index=s.index, name="drawdown")


def max_drawdown(returns: ArrayLike) -> float:
    """Largest peak-to-trough decline, returned as a non-positive number (e.g. -0.25)."""
    s = _as_series(returns)
    _require(s, 1, "maximum drawdown")
    return float(drawdown_series(s).min())


def max_drawdown_details(returns: pd.Series) -> dict[str, Any]:
    s = returns.dropna()
    _require(s, 1, "maximum drawdown")
    dd = drawdown_series(s)
    trough = dd.idxmin()
    mdd = float(dd.loc[trough])
    if mdd == 0.0:
        return {"max_drawdown": 0.0, "peak_date": None, "trough_date": None, "recovery_date": None, "duration_days": 0}
    wealth = (1.0 + s).cumprod()
    before = wealth.loc[:trough]
    peak_level = max(1.0, float(before.max()))
    peak_date = before.idxmax() if float(before.max()) >= 1.0 else None
    after = wealth.loc[trough:]
    recovered = after[after >= peak_level]
    recovery_date = recovered.index[0] if len(recovered) else None
    end_ref = recovery_date if recovery_date is not None else s.index[-1]
    start_ref = peak_date if peak_date is not None else s.index[0]
    duration = len(s.loc[start_ref:end_ref])
    return {
        "max_drawdown": mdd,
        "peak_date": _iso(peak_date),
        "trough_date": _iso(trough),
        "recovery_date": _iso(recovery_date),
        "duration_days": duration,
    }


def calmar_ratio(returns: ArrayLike, periods_per_year: int = TRADING_DAYS) -> float | None:
    s = _as_series(returns)
    mdd = max_drawdown(s)
    if abs(mdd) < _EPS:
        return None
    return annualized_return(s, periods_per_year) / abs(mdd)


# --- Relative to benchmark -----------------------------------------------------------------


def _align(a: pd.Series, b: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    return df["a"].to_numpy(dtype=float), df["b"].to_numpy(dtype=float)


def beta(returns: pd.Series, benchmark: pd.Series) -> float | None:
    """OLS beta: ``cov(r, b) / var(b)`` on overlapping, non-missing observations."""
    a, b = _align(returns, benchmark)
    if len(a) < 3:
        raise InsufficientDataError("beta requires at least 3 overlapping observations", details={"available": len(a)})
    var_b = float(np.var(b, ddof=1))
    if var_b < _EPS:
        return None
    return float(np.cov(a, b, ddof=1)[0, 1] / var_b)


def correlation(returns: pd.Series, benchmark: pd.Series) -> float | None:
    a, b = _align(returns, benchmark)
    if len(a) < 3:
        raise InsufficientDataError("correlation requires at least 3 overlapping observations")
    if np.std(a) < _EPS or np.std(b) < _EPS:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def tracking_error(returns: pd.Series, benchmark: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    a, b = _align(returns, benchmark)
    if len(a) < 3:
        raise InsufficientDataError("tracking error requires at least 3 overlapping observations")
    return float(np.std(a - b, ddof=1) * math.sqrt(periods_per_year))


def information_ratio(returns: pd.Series, benchmark: pd.Series, periods_per_year: int = TRADING_DAYS) -> float | None:
    a, b = _align(returns, benchmark)
    te = tracking_error(returns, benchmark, periods_per_year)
    if te < _EPS:
        return None
    return float(np.mean(a - b) * periods_per_year / te)


def jensens_alpha(
    returns: pd.Series, benchmark: pd.Series, risk_free_rate: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> float | None:
    """Annualised intercept of the CAPM regression ``(r - rf) = a + b (rb - rf) + e`` (historical, not a forecast)."""
    a, b = _align(returns, benchmark)
    if len(a) < 3:
        raise InsufficientDataError("alpha requires at least 3 overlapping observations")
    rf = per_period_rate(risk_free_rate, periods_per_year)
    ea, eb = a - rf, b - rf
    var_b = float(np.var(eb, ddof=1))
    if var_b < _EPS:
        return None
    bt = float(np.cov(ea, eb, ddof=1)[0, 1] / var_b)
    return float((np.mean(ea) - bt * np.mean(eb)) * periods_per_year)


# --- Trade-level style statistics ----------------------------------------------------------


def win_rate(returns: ArrayLike) -> float | None:
    """Share of non-zero periods with a positive return (flat periods are excluded)."""
    s = _as_series(returns)
    active = s[np.abs(s) > _EPS]
    if active.empty:
        return None
    return float((active > 0).mean())


def profit_factor(returns: ArrayLike) -> float | None:
    """Sum of positive period returns divided by the absolute sum of negative period returns."""
    s = _as_series(returns)
    gains = float(s[s > 0].sum())
    losses = float(-s[s < 0].sum())
    if losses < _EPS:
        return None
    return gains / losses


# --- Summary ---------------------------------------------------------------------------------


def performance_summary(
    returns: pd.Series,
    benchmark: pd.Series | None = None,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    """Compute the standard metric set. Raises InsufficientDataError for < 2 observations."""
    s = returns.dropna()
    _require(s, 2, "performance summary")
    out: dict[str, Any] = {
        "observations": len(s),
        "start_date": _iso(s.index[0]),
        "end_date": _iso(s.index[-1]),
        "cumulative_return": cumulative_return(s),
        "annualized_return": annualized_return(s, periods_per_year),
        "annualized_volatility": annualized_volatility(s, periods_per_year),
        "downside_deviation": downside_deviation(s, per_period_rate(risk_free_rate, periods_per_year), periods_per_year),
        "sharpe_ratio": sharpe_ratio(s, risk_free_rate, periods_per_year),
        "sortino_ratio": sortino_ratio(s, risk_free_rate, periods_per_year),
        "max_drawdown": max_drawdown(s),
        "calmar_ratio": calmar_ratio(s, periods_per_year),
        "best_day": float(s.max()),
        "worst_day": float(s.min()),
        "skewness": _safe(lambda: float(s.skew())),
        "excess_kurtosis": _safe(lambda: float(s.kurt())),
        "win_rate": win_rate(s),
        "profit_factor": profit_factor(s),
    }
    if benchmark is not None:
        out.update(
            {
                "beta": _safe(lambda: beta(s, benchmark)),
                "correlation": _safe(lambda: correlation(s, benchmark)),
                "tracking_error": _safe(lambda: tracking_error(s, benchmark, periods_per_year)),
                "information_ratio": _safe(lambda: information_ratio(s, benchmark, periods_per_year)),
                "alpha": _safe(lambda: jensens_alpha(s, benchmark, risk_free_rate, periods_per_year)),
            }
        )
    return out


def _safe(fn: Any) -> Any:
    try:
        v = fn()
    except InsufficientDataError:
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _iso(d: Any) -> str | None:
    if d is None:
        return None
    return pd.Timestamp(d).date().isoformat()
