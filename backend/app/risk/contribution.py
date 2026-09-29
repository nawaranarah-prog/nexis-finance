"""Portfolio risk decomposition and concentration statistics."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.metrics import TRADING_DAYS
from app.core.errors import InsufficientDataError


def portfolio_volatility(weights: np.ndarray, cov: np.ndarray) -> float:
    """sqrt(w' Σ w) with Σ in the same periodicity as the desired output."""
    return float(math.sqrt(max(float(weights @ cov @ weights), 0.0)))


def risk_contributions(weights: pd.Series, returns: pd.DataFrame, periods_per_year: int = TRADING_DAYS) -> dict[str, Any]:
    """Euler decomposition of annualised portfolio volatility.

    MCR_i = (Σw)_i / σ_p ;  CR_i = w_i · MCR_i ;  Σ CR_i = σ_p ;  %CR_i = CR_i / σ_p
    """
    cols = list(weights.index)
    r = returns[cols].dropna()
    if len(r) < max(30, len(cols) + 2):
        raise InsufficientDataError("risk decomposition requires more observations than assets (min 30)")
    cov = r.cov().to_numpy() * periods_per_year
    w = weights.to_numpy(dtype=float)
    sigma = portfolio_volatility(w, cov)
    if sigma < 1e-12:
        mcr = np.zeros_like(w)
    else:
        mcr = cov @ w / sigma
    cr = w * mcr
    pct = cr / sigma if sigma > 1e-12 else np.zeros_like(w)
    standalone = np.sqrt(np.diag(cov))
    return {
        "portfolio_volatility": sigma,
        "assets": [
            {
                "symbol": s,
                "weight": float(w[i]),
                "standalone_volatility": float(standalone[i]),
                "marginal_contribution": float(mcr[i]),
                "contribution": float(cr[i]),
                "pct_contribution": float(pct[i]),
            }
            for i, s in enumerate(cols)
        ],
        # Diversification ratio: weighted average standalone vol / portfolio vol (>= 1 for long-only).
        "diversification_ratio": float(w @ standalone / sigma) if sigma > 1e-12 else None,
        "observations": len(r),
    }


def concentration(weights: pd.Series) -> dict[str, float]:
    w = weights.to_numpy(dtype=float)
    hhi = float(np.sum(w**2))
    return {
        "herfindahl_index": hhi,
        "effective_number_of_assets": 1.0 / hhi if hhi > 0 else 0.0,
        "max_weight": float(np.max(w)) if w.size else 0.0,
        "top5_weight": float(np.sort(w)[::-1][:5].sum()),
        "gross_exposure": float(np.abs(w).sum()),
    }


def correlation_exposure(weights: pd.Series, returns: pd.DataFrame) -> dict[str, Any]:
    """Weight-averaged pairwise correlation among holdings (off-diagonal pairs only)."""
    cols = list(weights.index)
    if len(cols) < 2:
        return {"weighted_avg_correlation": None, "avg_correlation": None}
    corr = returns[cols].dropna().corr().to_numpy()
    w = np.abs(weights.to_numpy(dtype=float))
    ww = np.outer(w, w)
    mask = ~np.eye(len(cols), dtype=bool)
    denom = ww[mask].sum()
    return {
        "weighted_avg_correlation": float((ww[mask] * corr[mask]).sum() / denom) if denom > 0 else None,
        "avg_correlation": float(corr[mask].mean()),
    }
