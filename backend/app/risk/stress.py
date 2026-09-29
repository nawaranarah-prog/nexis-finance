"""Hypothetical stress scenarios.

Every result is a *hypothetical scenario* under explicit linear assumptions — not a forecast.

Scenario types
--------------
market_shock       Benchmark moves by ``shock``; each asset moves by ``beta_i * shock`` where
                   beta is estimated over the lookback window (single-factor linear model).
sector_shock       Assets in ``sector`` move by ``shock`` directly; other assets move by their
                   beta to that sector's equal-weight index times the shock (spill-over).
volatility_spike   Covariance matrix scaled by ``multiplier``²  (vols × multiplier); reports
                   stressed parametric VaR/CVaR rather than a point loss.
correlation_increase  Correlations blended toward 1 by ``intensity`` (ρ' = ρ + λ(1-ρ)), vols held
                   fixed; reports stressed volatility and VaR.
custom             User-specified per-asset returns (unspecified assets move 0%).
historical_worst   Replays the worst realised ``window_days`` compounded portfolio return in the
                   lookback sample, with per-asset returns over the same window.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from app.core.errors import ConfigurationError, InsufficientDataError

SCENARIO_TYPES = ("market_shock", "sector_shock", "volatility_spike", "correlation_increase", "custom", "historical_worst")


def _betas(asset_returns: pd.DataFrame, factor: pd.Series) -> pd.Series:
    df = asset_returns.join(factor.rename("__f"), how="inner").dropna()
    if len(df) < 60:
        raise InsufficientDataError("stress betas require at least 60 overlapping observations")
    f = df.pop("__f")
    var = f.var(ddof=1)
    if var <= 1e-14:
        raise InsufficientDataError("factor has zero variance over the lookback window")
    return df.apply(lambda c: c.cov(f) / var)


def _point_result(
    weights: pd.Series, asset_shocks: pd.Series, value: float, method: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    asset_shocks = asset_shocks.reindex(weights.index).fillna(0.0)
    contrib = weights * asset_shocks
    port = float(contrib.sum())
    assets = [
        {
            "symbol": s,
            "weight": float(weights[s]),
            "shock_return": float(asset_shocks[s]),
            "contribution": float(contrib[s]),
            "pnl": float(contrib[s] * value),
            "position_value_before": float(weights[s] * value),
            "position_value_after": float(weights[s] * value * (1.0 + asset_shocks[s])),
        }
        for s in weights.index
    ]
    assets.sort(key=lambda a: a["contribution"])
    return {
        "method": method,
        "portfolio_return": port,
        "portfolio_value_before": value,
        "portfolio_value_after": value * (1.0 + port),
        "pnl": value * port,
        "loss_pct": -port,
        "assets": assets,
        "largest_contributors": assets[:5],
        **(extra or {}),
    }


def run_scenario(
    scenario_type: str,
    params: dict[str, Any],
    weights: pd.Series,
    asset_returns: pd.DataFrame,
    benchmark_returns: pd.Series,
    sectors: dict[str, str | None],
    portfolio_value: float,
    confidence: float = 0.99,
) -> dict[str, Any]:
    if scenario_type not in SCENARIO_TYPES:
        raise ConfigurationError(f"unknown scenario type '{scenario_type}'", details={"allowed": SCENARIO_TYPES})
    r = asset_returns[list(weights.index)]

    if scenario_type == "market_shock":
        shock = _shock(params)
        b = _betas(r, benchmark_returns)
        return _point_result(
            weights, b * shock, portfolio_value, "beta-scaled benchmark shock", {"betas": b.to_dict(), "benchmark_shock": shock}
        )

    if scenario_type == "sector_shock":
        shock = _shock(params)
        sector = params.get("sector")
        members = [s for s, sec in sectors.items() if sec == sector]
        if not members:
            raise ConfigurationError(f"no assets found in sector '{sector}'")
        sector_index = asset_returns[[m for m in members if m in asset_returns]].mean(axis=1)
        b = _betas(r, sector_index)
        shocks = b * shock
        for s in weights.index:
            if sectors.get(s) == sector:
                shocks[s] = shock
        return _point_result(
            weights,
            shocks,
            portfolio_value,
            "direct sector shock with beta spill-over",
            {
                "sector": sector,
                "sector_members_in_portfolio": [s for s in weights.index if sectors.get(s) == sector],
                "spillover_betas": b.to_dict(),
                "sector_shock": shock,
            },
        )

    if scenario_type == "custom":
        shocks_in = params.get("asset_shocks") or {}
        if not shocks_in:
            raise ConfigurationError("custom scenario requires 'asset_shocks' {symbol: return}")
        for k, v in shocks_in.items():
            if not isinstance(v, (int, float)) or v < -1.0 or v > 10.0:
                raise ConfigurationError(f"shock for {k} must be a number between -1.0 and 10.0")
        return _point_result(weights, pd.Series(shocks_in, dtype=float), portfolio_value, "user-specified asset returns")

    if scenario_type == "historical_worst":
        window = int(params.get("window_days", 10))
        if not 1 <= window <= 250:
            raise ConfigurationError("window_days must be between 1 and 250")
        log_r = np.log1p(r.fillna(0.0))
        cum = log_r.rolling(window).sum()
        port_cum = np.log1p((np.expm1(cum) * weights).sum(axis=1))
        port_cum = port_cum.iloc[window - 1 :]
        if port_cum.empty:
            raise InsufficientDataError("not enough history for the requested window")
        end = port_cum.idxmin()
        shocks = np.expm1(cum.loc[end])
        start = r.index[r.index.get_loc(end) - window + 1]
        return _point_result(
            weights,
            shocks,
            portfolio_value,
            "historical replay of worst window",
            {"window_start": start.date().isoformat(), "window_end": end.date().isoformat(), "window_days": window},
        )

    # Distributional scenarios: compare baseline vs stressed covariance.
    clean = r.dropna()
    if len(clean) < 60:
        raise InsufficientDataError("covariance-based stress needs at least 60 observations")
    cov = clean.cov().to_numpy()
    mu = clean.mean().to_numpy()
    w = weights.to_numpy()
    sd = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sd, sd)

    if scenario_type == "volatility_spike":
        m = float(params.get("multiplier", 2.0))
        if not 0.1 <= m <= 10:
            raise ConfigurationError("multiplier must be between 0.1 and 10")
        cov_s = cov * m**2
        label = f"volatilities × {m:g}"
    else:
        lam = float(params.get("intensity", 0.5))
        if not 0.0 <= lam <= 1.0:
            raise ConfigurationError("intensity must be between 0 and 1")
        corr_s = corr + lam * (1.0 - corr)
        np.fill_diagonal(corr_s, 1.0)
        cov_s = corr_s * np.outer(sd, sd)
        label = f"correlations blended {lam:.0%} toward 1"

    def stats_for(c: np.ndarray) -> dict[str, float]:
        vol_d = math.sqrt(float(w @ c @ w))
        m_d = float(w @ mu)
        z = stats.norm.ppf(1 - confidence)
        var = -(m_d + z * vol_d)
        cvar = -(m_d - vol_d * stats.norm.pdf(z) / (1 - confidence))
        return {
            "daily_volatility": vol_d,
            "annualized_volatility": vol_d * math.sqrt(252),
            "var": var,
            "cvar": cvar,
            "var_amount": var * portfolio_value,
            "cvar_amount": cvar * portfolio_value,
        }

    base, stressed = stats_for(cov), stats_for(cov_s)
    # Per-asset contribution to stressed volatility (Euler).
    vol_s = math.sqrt(float(w @ cov_s @ w))
    pct = (w * (cov_s @ w)) / (vol_s**2) if vol_s > 0 else np.zeros_like(w)
    assets = [
        {
            "symbol": s,
            "weight": float(w[i]),
            "pct_risk_contribution_stressed": float(pct[i]),
            "standalone_daily_vol_stressed": float(math.sqrt(cov_s[i, i])),
        }
        for i, s in enumerate(weights.index)
    ]
    assets.sort(key=lambda a: a["pct_risk_contribution_stressed"], reverse=True)
    return {
        "method": f"parametric normal with stressed covariance ({label})",
        "confidence": confidence,
        "baseline": base,
        "stressed": stressed,
        "portfolio_value_before": portfolio_value,
        # For distributional scenarios the 'loss' reported is the stressed one-day CVaR.
        "loss_pct": stressed["cvar"],
        "portfolio_return": -stressed["cvar"],
        "portfolio_value_after": portfolio_value * (1 - stressed["cvar"]),
        "pnl": -stressed["cvar_amount"],
        "assets": assets,
        "largest_contributors": assets[:5],
    }


def _shock(params: dict[str, Any]) -> float:
    shock = params.get("shock")
    if not isinstance(shock, (int, float)) or not -0.95 <= shock <= 1.0:
        raise ConfigurationError("'shock' must be a decimal return between -0.95 and 1.0 (e.g. -0.10)")
    return float(shock)
