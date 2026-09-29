"""Value-at-Risk and Expected Shortfall (CVaR).

Sign convention: VaR and CVaR are reported as **positive loss fractions**. A 95% one-day
VaR of 0.021 means that, under the stated methodology and over the lookback sample,
losses exceeded 2.1% on roughly 5% of days. VaR is *not* a maximum possible loss.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from app.core.errors import ConfigurationError, InsufficientDataError

ALLOWED_CONFIDENCE = (0.90, 0.95, 0.975, 0.99)


def _prepare(returns: pd.Series | np.ndarray, confidence: float) -> np.ndarray:
    if not 0.5 < confidence < 1.0:
        raise ConfigurationError("confidence must be between 0.5 and 1.0 (exclusive)")
    arr = np.asarray(pd.Series(returns).dropna(), dtype=float)
    # Need enough observations for at least one expected tail observation, and never fewer than 30.
    required = max(30, math.ceil(1.0 / (1.0 - confidence)))
    if arr.size < required:
        raise InsufficientDataError(
            f"VaR at {confidence:.1%} requires at least {required} observations; got {arr.size}",
            details={"required": required, "available": int(arr.size)},
        )
    return arr


def horizon_returns(returns: pd.Series, horizon: int) -> pd.Series:
    """Overlapping compounded h-period returns. Overlap induces autocorrelation; used for historical VaR only."""
    if horizon < 1:
        raise ConfigurationError("horizon must be >= 1")
    r = returns.dropna()
    if horizon == 1:
        return r
    return np.exp(np.log1p(r).rolling(horizon).sum()).sub(1.0).dropna()


def historical_var(returns: pd.Series | np.ndarray, confidence: float = 0.95) -> float:
    arr = _prepare(returns, confidence)
    return float(-np.quantile(arr, 1.0 - confidence, method="linear"))


def historical_cvar(returns: pd.Series | np.ndarray, confidence: float = 0.95) -> float:
    """Mean loss of observations at or beyond the historical VaR quantile."""
    arr = _prepare(returns, confidence)
    q = np.quantile(arr, 1.0 - confidence, method="linear")
    tail = arr[arr <= q]
    return float(-tail.mean())


def parametric_var(returns: pd.Series | np.ndarray, confidence: float = 0.95, horizon: int = 1) -> float:
    """Gaussian VaR: ``-(mu*h + z_(1-c) * sigma * sqrt(h))``. Assumes i.i.d. normal returns."""
    arr = _prepare(returns, confidence)
    mu, sigma = float(arr.mean()), float(arr.std(ddof=1))
    z = stats.norm.ppf(1.0 - confidence)
    return float(-(mu * horizon + z * sigma * math.sqrt(horizon)))


def parametric_cvar(returns: pd.Series | np.ndarray, confidence: float = 0.95, horizon: int = 1) -> float:
    """Gaussian expected shortfall: ``-(mu*h - sigma*sqrt(h) * phi(z) / (1-c))``."""
    arr = _prepare(returns, confidence)
    mu, sigma = float(arr.mean()), float(arr.std(ddof=1))
    z = stats.norm.ppf(1.0 - confidence)
    return float(-(mu * horizon - sigma * math.sqrt(horizon) * stats.norm.pdf(z) / (1.0 - confidence)))


def cornish_fisher_var(returns: pd.Series | np.ndarray, confidence: float = 0.95) -> float:
    """Modified VaR adjusting the Gaussian quantile for sample skewness and excess kurtosis."""
    arr = _prepare(returns, confidence)
    mu, sigma = float(arr.mean()), float(arr.std(ddof=1))
    s = float(stats.skew(arr, bias=False))
    k = float(stats.kurtosis(arr, fisher=True, bias=False))
    z = stats.norm.ppf(1.0 - confidence)
    z_cf = z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36
    return float(-(mu + z_cf * sigma))


def var_summary(
    returns: pd.Series, confidence: float = 0.95, horizon: int = 1, portfolio_value: float | None = None
) -> dict[str, Any]:
    """All VaR/CVaR estimates for one confidence level, with methodology notes."""
    r = returns.dropna()
    hr = horizon_returns(r, horizon)
    hist_var = historical_var(hr, confidence)
    hist_cvar = historical_cvar(hr, confidence)
    p_var = parametric_var(r, confidence, horizon)
    p_cvar = parametric_cvar(r, confidence, horizon)
    cf_var = cornish_fisher_var(r, confidence) * math.sqrt(horizon) if horizon > 1 else cornish_fisher_var(r, confidence)
    tail_obs = int((hr <= -hist_var).sum())

    def amt(x: float) -> float | None:
        return None if portfolio_value is None else x * portfolio_value

    return {
        "confidence": confidence,
        "horizon_days": horizon,
        "observations": len(r),
        "tail_observations": tail_obs,
        "small_tail_sample": tail_obs < 10,
        "historical": {"var": hist_var, "cvar": hist_cvar, "var_amount": amt(hist_var), "cvar_amount": amt(hist_cvar)},
        "parametric_normal": {"var": p_var, "cvar": p_cvar, "var_amount": amt(p_var), "cvar_amount": amt(p_cvar)},
        "cornish_fisher": {"var": cf_var, "cvar": None, "var_amount": amt(cf_var), "cvar_amount": None},
        "sample_skewness": float(stats.skew(r, bias=False)),
        "sample_excess_kurtosis": float(stats.kurtosis(r, fisher=True, bias=False)),
    }


def kupiec_test(exceptions: int, observations: int, confidence: float) -> dict[str, float | None]:
    """Kupiec proportion-of-failures likelihood-ratio test for VaR exceedances."""
    p = 1.0 - confidence
    if observations == 0:
        return {"lr_stat": None, "p_value": None}
    x, n = exceptions, observations
    phat = x / n

    # Log-likelihoods with the 0*log(0) = 0 convention.
    def ll(prob: float) -> float:
        a = (n - x) * math.log(1 - prob) if prob < 1 else (0.0 if n == x else -math.inf)
        b = x * math.log(prob) if prob > 0 else (0.0 if x == 0 else -math.inf)
        return a + b

    lr = -2.0 * (ll(p) - ll(phat))
    return {"lr_stat": float(lr), "p_value": float(1.0 - stats.chi2.cdf(lr, df=1))}


def rolling_var_backtest(returns: pd.Series, confidence: float = 0.95, window: int = 250) -> dict[str, Any]:
    """Out-of-sample check: VaR at t is estimated from the ``window`` returns strictly before t."""
    r = returns.dropna()
    if len(r) < window + 30:
        raise InsufficientDataError(f"VaR backtest needs at least {window + 30} observations; got {len(r)}")
    q = r.rolling(window).quantile(1.0 - confidence).shift(1)
    var_series = -q
    valid = var_series.dropna()
    realised = r.loc[valid.index]
    exceed = realised < -valid
    n, x = len(valid), int(exceed.sum())
    test = kupiec_test(x, n, confidence)
    return {
        "confidence": confidence,
        "window": window,
        "observations": n,
        "exceptions": x,
        "expected_exceptions": n * (1.0 - confidence),
        "exception_rate": x / n if n else None,
        "kupiec_lr": test["lr_stat"],
        "kupiec_p_value": test["p_value"],
        "dates": [d.date().isoformat() for d in valid.index],
        "var": valid.tolist(),
        "returns": realised.tolist(),
        "exception_flags": exceed.astype(bool).tolist(),
    }
