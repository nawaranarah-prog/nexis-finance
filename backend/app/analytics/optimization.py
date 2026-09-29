"""Research-grade portfolio allocation methods.

These are *in-sample* optimisations on an estimation window chosen by the user. Expected
returns estimated from historical means are notoriously noisy, so maximum-Sharpe weights are
highly sensitive to the window; results are research outputs, not investment advice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

from app.analytics.metrics import TRADING_DAYS, per_period_rate
from app.core.errors import ConfigurationError, InsufficientDataError

AllocationMethod = Literal["equal_weight", "custom", "inverse_volatility", "min_variance", "max_sharpe", "risk_parity"]
ALLOCATION_METHODS = ("equal_weight", "custom", "inverse_volatility", "min_variance", "max_sharpe", "risk_parity")


@dataclass
class AllocationResult:
    weights: pd.Series
    method: str
    diagnostics: dict[str, Any]


def estimate_covariance(
    returns: pd.DataFrame, shrinkage: bool = True, periods_per_year: int = TRADING_DAYS
) -> tuple[np.ndarray, float | None]:
    """Annualised covariance. Ledoit-Wolf shrinkage reduces estimation noise for many assets."""
    r = returns.dropna()
    if len(r) < max(30, r.shape[1] + 2):
        raise InsufficientDataError(
            "covariance estimation needs more observations than assets (minimum 30)",
            details={"observations": len(r), "assets": r.shape[1]},
        )
    if shrinkage:
        lw = LedoitWolf().fit(r.to_numpy())
        return lw.covariance_ * periods_per_year, float(lw.shrinkage_)
    return r.cov().to_numpy() * periods_per_year, None


def _bounds(n: int, min_weight: float, max_weight: float) -> list[tuple[float, float]]:
    if min_weight < 0 or max_weight > 1 or min_weight > max_weight:
        raise ConfigurationError("weight bounds must satisfy 0 <= min <= max <= 1")
    if n * max_weight < 1 - 1e-9 or n * min_weight > 1 + 1e-9:
        raise ConfigurationError(f"infeasible bounds: {n} assets cannot sum to 100% within [{min_weight:.1%}, {max_weight:.1%}]")
    return [(min_weight, max_weight)] * n


def _solve(objective: Any, n: int, bounds: list[tuple[float, float]], x0: np.ndarray | None = None) -> np.ndarray:
    x0 = np.full(n, 1.0 / n) if x0 is None else x0
    x0 = np.clip(x0, [b[0] for b in bounds], [b[1] for b in bounds])
    res = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}],
        options={"maxiter": 500, "ftol": 1e-12},
    )
    if not res.success:
        raise ConfigurationError(f"optimiser did not converge: {res.message}")
    w = np.clip(res.x, 0.0, None)
    return w / w.sum()


def allocate(
    method: str,
    returns: pd.DataFrame,
    custom_weights: dict[str, float] | None = None,
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    risk_free_rate: float = 0.0,
    shrinkage: bool = True,
    periods_per_year: int = TRADING_DAYS,
) -> AllocationResult:
    symbols = list(returns.columns)
    n = len(symbols)
    if n == 0:
        raise ConfigurationError("at least one asset is required")
    if method not in ALLOCATION_METHODS:
        raise ConfigurationError(f"unknown allocation method '{method}'", details={"allowed": ALLOCATION_METHODS})

    diag: dict[str, Any] = {"estimation_start": None, "estimation_end": None, "observations": int(returns.dropna().shape[0])}
    r = returns.dropna()
    if len(r):
        diag["estimation_start"] = _iso(r.index[0])
        diag["estimation_end"] = _iso(r.index[-1])

    if method == "custom":
        if not custom_weights:
            raise ConfigurationError("custom allocation requires weights")
        w = np.array([float(custom_weights.get(s, 0.0)) for s in symbols])
    elif method == "equal_weight":
        w = np.full(n, 1.0 / n)
    elif method == "inverse_volatility":
        vol = r.std(ddof=1).to_numpy() * math.sqrt(periods_per_year)
        if len(r) < 30:
            raise InsufficientDataError("inverse-volatility weighting needs at least 30 observations")
        if np.any(vol <= 1e-12):
            raise ConfigurationError("inverse-volatility weighting is undefined for zero-volatility assets")
        w = (1.0 / vol) / np.sum(1.0 / vol)
        diag["asset_volatility"] = dict(zip(symbols, vol.tolist(), strict=True))
    else:
        cov, shrink = estimate_covariance(r, shrinkage, periods_per_year)
        diag["ledoit_wolf_shrinkage"] = shrink
        if method == "min_variance":
            b = _bounds(n, min_weight, max_weight)
            w = _solve(lambda x: float(x @ cov @ x), n, b)
        elif method == "max_sharpe":
            b = _bounds(n, min_weight, max_weight)
            mu = r.mean().to_numpy() * periods_per_year
            rf = risk_free_rate
            diag["expected_returns_in_sample"] = dict(zip(symbols, mu.tolist(), strict=True))
            if np.all(mu <= rf):
                raise ConfigurationError(
                    "maximum-Sharpe is ill-posed: no asset has in-sample mean return above the risk-free rate"
                )

            def neg_sharpe(x: np.ndarray) -> float:
                vol = math.sqrt(max(float(x @ cov @ x), 1e-18))
                return -(float(x @ mu) - rf) / vol

            w = _solve(neg_sharpe, n, b)
        else:  # risk_parity
            w = _risk_parity(cov)
            if np.any(w > max_weight + 1e-9) or np.any(w < min_weight - 1e-9):
                diag["bound_warning"] = (
                    "Equal-risk-contribution weights violate the requested bounds; bounds not enforced for risk parity."
                )

        diag["ex_ante_volatility"] = float(math.sqrt(w @ cov @ w))
        rc = w * (cov @ w) / (w @ cov @ w)
        diag["risk_contributions"] = dict(zip(symbols, rc.tolist(), strict=True))

    if method in ("custom", "equal_weight", "inverse_volatility"):
        validate_weights(dict(zip(symbols, w.tolist(), strict=True)), min_weight, max_weight, enforce_bounds=method == "custom")
    if len(r) >= 2:
        mu_d = r.mean().to_numpy()
        diag["in_sample_annual_return"] = float((1 + w @ mu_d) ** periods_per_year - 1)
        rf_d = per_period_rate(risk_free_rate, periods_per_year)
        port = r.to_numpy() @ w
        sd = float(np.std(port, ddof=1))
        diag["in_sample_sharpe"] = float((port.mean() - rf_d) / sd * math.sqrt(periods_per_year)) if sd > 1e-12 else None
    return AllocationResult(weights=pd.Series(w, index=symbols), method=method, diagnostics=diag)


def _iso(v: Any) -> str | None:
    return pd.Timestamp(v).date().isoformat() if isinstance(v, (pd.Timestamp, np.datetime64)) or hasattr(v, "date") else None


def _risk_parity(cov: np.ndarray) -> np.ndarray:
    """Equal-risk-contribution weights via the convex formulation of Spinu (2013):
    minimise ½ yᵀΣy − (1/n) Σ log yᵢ over y > 0, then normalise w = y / Σy.
    """
    n = cov.shape[0]
    b = np.full(n, 1.0 / n)
    sd = np.sqrt(np.diag(cov))
    y0 = 1.0 / sd

    def f(y: np.ndarray) -> float:
        return 0.5 * float(y @ cov @ y) - float(b @ np.log(y))

    def g(y: np.ndarray) -> np.ndarray:
        return cov @ y - b / y

    res = minimize(f, y0, jac=g, method="L-BFGS-B", bounds=[(1e-10, None)] * n, options={"maxiter": 2000})
    if not res.success:
        raise ConfigurationError(f"risk-parity optimiser did not converge: {res.message}")
    y = res.x
    return y / y.sum()


def validate_weights(
    weights: dict[str, float],
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    enforce_bounds: bool = True,
    tolerance: float = 1e-6,
) -> None:
    """Raise ConfigurationError describing every problem with a proposed weight vector."""
    problems: list[str] = []
    for s, v in weights.items():
        if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
            problems.append(f"{s}: weight is not a finite number")
        elif enforce_bounds and (v < min_weight - tolerance or v > max_weight + tolerance):
            problems.append(f"{s}: weight {v:.4f} outside [{min_weight:.4f}, {max_weight:.4f}]")
    if not problems:
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-4:
            problems.append(f"weights sum to {total:.6f}; they must sum to 1.0")
    if problems:
        raise ConfigurationError("invalid portfolio weights", details={"problems": problems})
