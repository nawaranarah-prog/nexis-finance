"""Correlation structure: matrices, clustering, rolling dependency."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

from app.core.errors import InsufficientDataError


def correlation_matrix(
    returns: pd.DataFrame, method: str = "pearson", min_periods: int = 30, cluster: bool = True
) -> dict[str, Any]:
    """Pairwise correlation with the number of overlapping observations behind each cell.

    Pairwise-complete estimation means different cells can rest on different samples; the
    ``pair_counts`` matrix exposes that so missing-data effects are not hidden.
    """
    if returns.shape[1] < 2:
        raise InsufficientDataError("correlation matrix requires at least two assets")
    if method not in ("pearson", "spearman"):
        raise InsufficientDataError("method must be 'pearson' or 'spearman'")
    corr = returns.corr(method=method, min_periods=min_periods)
    valid = returns.notna().astype(int)
    counts = valid.T @ valid
    symbols = list(corr.columns)
    order = symbols
    linkage_matrix = None
    if cluster and corr.notna().all().all() and len(symbols) > 2:
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr.to_numpy()), 0.0, None))
        np.fill_diagonal(dist, 0.0)
        z = linkage(squareform(dist, checks=False), method="average")
        order = [symbols[i] for i in leaves_list(z)]
        linkage_matrix = z.tolist()
    c = corr.loc[order, order]
    return {
        "symbols": order,
        "matrix": [[_f(v) for v in row] for row in c.to_numpy()],
        "pair_counts": counts.loc[order, order].to_numpy().astype(int).tolist(),
        "method": method,
        "clustered": linkage_matrix is not None,
        "linkage": linkage_matrix,
        "observations_total": len(returns),
        "complete_case_observations": len(returns.dropna()),
        "average_pairwise_correlation": _f(_offdiag_mean(corr.to_numpy())),
    }


def highly_correlated_pairs(returns: pd.DataFrame, threshold: float = 0.8, min_periods: int = 30) -> list[dict[str, Any]]:
    corr = returns.corr(min_periods=min_periods)
    valid = returns.notna().astype(int)
    counts = valid.T @ valid
    out = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            v = corr.iat[i, j]
            if pd.notna(v) and abs(v) >= threshold:
                out.append({"a": cols[i], "b": cols[j], "correlation": float(v), "observations": int(counts.iat[i, j])})
    out.sort(key=lambda d: abs(d["correlation"]), reverse=True)
    return out


def rolling_average_correlation(returns: pd.DataFrame, window: int = 60) -> pd.Series:
    """Average off-diagonal correlation in each trailing window (a market-wide dependency gauge)."""
    r = returns.dropna(how="all")
    if len(r) < window:
        raise InsufficientDataError(f"rolling correlation needs at least {window} observations")
    arr = r.to_numpy(dtype=float)
    out = np.full(len(r), np.nan)
    for t in range(window - 1, len(r)):
        block = arr[t - window + 1 : t + 1]
        block = block[:, ~np.isnan(block).any(axis=0)]
        if block.shape[1] < 2:
            continue
        sd = block.std(axis=0, ddof=1)
        block = block[:, sd > 1e-12]
        if block.shape[1] < 2:
            continue
        out[t] = _offdiag_mean(np.corrcoef(block, rowvar=False))
    return pd.Series(out, index=r.index, name="avg_correlation")


def _offdiag_mean(m: np.ndarray) -> float:
    n = m.shape[0]
    mask = ~np.eye(n, dtype=bool)
    vals = m[mask]
    vals = vals[np.isfinite(vals)]
    return float(vals.mean()) if vals.size else float("nan")


def _f(v: Any) -> float | None:
    return None if v is None or not np.isfinite(v) else float(v)
