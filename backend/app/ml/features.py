"""Point-in-time feature engineering.

Every feature value at date *t* is a function of bars dated ≤ *t* only (rolling windows end at
*t*; normalisation statistics for z-scores end at *t−1*). Targets are the only quantities that
look forward, and they are constructed separately so the boundary is explicit and testable.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

ANN = math.sqrt(252)

VOL_FEATURES = {
    "ret_1": "Return on day t",
    "abs_ret_1": "Absolute return on day t",
    "ret_lag_1": "Return on day t-1",
    "ret_5": "Sum of returns over t-4..t",
    "rv_5": "Realised volatility, 5 days ending t (annualised)",
    "rv_20": "Realised volatility, 20 days ending t (annualised)",
    "rv_60": "Realised volatility, 60 days ending t (annualised)",
    "rv_ratio_5_60": "rv_5 / rv_60 (volatility regime shift)",
    "parkinson_20": "Parkinson high-low range volatility, 20 days (annualised)",
    "mean_ret_20": "Mean daily return over 20 days",
    "mom_20": "20-day price momentum",
    "mom_60": "60-day price momentum",
    "volume_change": "log(volume_t / mean volume over t-20..t-1)",
    "bench_ret_1": "Benchmark return on day t",
    "bench_rv_20": "Benchmark 20-day realised volatility (annualised)",
    "ewma_vol": "RiskMetrics EWMA volatility, lambda=0.94 (annualised)",
}


def asset_features(
    close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series, bench_close: pd.Series | None
) -> pd.DataFrame:
    r = close.pct_change(fill_method=None)
    lr = np.log(close / close.shift(1))
    hl = np.log(high / low) ** 2
    f = pd.DataFrame(index=close.index)
    f["ret_1"] = r
    f["abs_ret_1"] = r.abs()
    f["ret_lag_1"] = r.shift(1)
    f["ret_5"] = lr.rolling(5).sum()
    f["rv_5"] = lr.rolling(5).std() * ANN
    f["rv_20"] = lr.rolling(20).std() * ANN
    f["rv_60"] = lr.rolling(60).std() * ANN
    f["rv_ratio_5_60"] = f["rv_5"] / f["rv_60"]
    f["parkinson_20"] = np.sqrt(hl.rolling(20).mean() / (4 * math.log(2))) * ANN
    f["mean_ret_20"] = r.rolling(20).mean()
    f["mom_20"] = close / close.shift(20) - 1
    f["mom_60"] = close / close.shift(60) - 1
    v = volume.astype(float).where(volume > 0)
    f["volume_change"] = np.log(v / v.shift(1).rolling(20).mean())
    if bench_close is not None:
        br = bench_close.reindex(close.index).pct_change(fill_method=None)
        f["bench_ret_1"] = br
        f["bench_rv_20"] = np.log1p(br).rolling(20).std() * ANN
    else:
        f["bench_ret_1"] = 0.0
        f["bench_rv_20"] = 0.0
    # EWMA variance recursion uses r_t, so the value at t is known at t's close.
    f["ewma_vol"] = np.sqrt((lr**2).ewm(alpha=1 - 0.94, adjust=False).mean()) * ANN
    return f


def forward_realized_vol(close: pd.Series, horizon: int) -> pd.Series:
    """Target: annualised std of log returns over (t+1 … t+horizon). NaN for the final ``horizon`` rows."""
    lr = np.log(close / close.shift(1))
    fwd = lr.rolling(horizon).std().shift(-horizon)
    return fwd * ANN


def trailing_realized_vol(close: pd.Series, horizon: int) -> pd.Series:
    """Naive forecast: realised vol over the same horizon ending at t (a random-walk forecast of vol)."""
    lr = np.log(close / close.shift(1))
    return lr.rolling(horizon).std() * ANN


def build_volatility_panel(
    close: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    volume: pd.DataFrame,
    bench_close: pd.Series | None,
    symbols: list[str],
    horizon: int,
) -> pd.DataFrame:
    frames = []
    for s in symbols:
        c = close[s].dropna()
        if len(c) < 120:
            continue
        f = asset_features(c, high[s].reindex(c.index), low[s].reindex(c.index), volume[s].reindex(c.index), bench_close)
        f["naive_hist_vol"] = trailing_realized_vol(c, horizon)
        f["target"] = forward_realized_vol(c, horizon)
        f["symbol"] = s
        frames.append(f)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames).rename_axis("date").reset_index()
    return panel.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)


REGIME_FEATURES = {
    "rv_20": "Benchmark 20-day realised volatility (annualised)",
    "mom_60": "Benchmark 60-day return",
    "drawdown": "Benchmark drawdown from trailing 252-day peak",
    "ma_ratio": "Benchmark close / 200-day SMA − 1",
    "avg_corr_60": "Average pairwise 60-day correlation across the universe",
}


def regime_features(bench_close: pd.Series, universe_returns: pd.DataFrame | None) -> pd.DataFrame:
    lr = np.log(bench_close / bench_close.shift(1))
    f = pd.DataFrame(index=bench_close.index)
    f["rv_20"] = lr.rolling(20).std() * ANN
    f["mom_60"] = bench_close / bench_close.shift(60) - 1
    f["drawdown"] = bench_close / bench_close.rolling(252, min_periods=60).max() - 1
    f["ma_ratio"] = bench_close / bench_close.rolling(200).mean() - 1
    if universe_returns is not None and universe_returns.shape[1] >= 3:
        from app.analytics.correlation import rolling_average_correlation

        f["avg_corr_60"] = rolling_average_correlation(universe_returns.reindex(bench_close.index), 60)
    return f


ANOMALY_FEATURES = {
    "ret_z": "Return z-score vs trailing 60 days (stats end t-1)",
    "volume_z": "log-volume z-score vs trailing 60 days (stats end t-1)",
    "range_z": "log(high/low) z-score vs trailing 60 days (stats end t-1)",
    "gap_z": "Overnight gap (open / previous close − 1) z-score",
    "rv_ratio": "5-day / 60-day realised volatility",
}


def anomaly_features(
    close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series, window: int = 60
) -> pd.DataFrame:
    r = close.pct_change(fill_method=None)
    lv = np.log(volume.astype(float).where(volume > 0))
    rng = np.log(high / low)
    gap = open_ / close.shift(1) - 1

    def z(x: pd.Series) -> pd.Series:
        m = x.shift(1).rolling(window, min_periods=window // 2).mean()
        sd = x.shift(1).rolling(window, min_periods=window // 2).std()
        return (x - m) / sd.where(sd > 1e-12)

    f = pd.DataFrame(index=close.index)
    f["ret_z"] = z(r)
    f["volume_z"] = z(lv)
    f["range_z"] = z(rng)
    f["gap_z"] = z(gap)
    lr = np.log(close / close.shift(1))
    f["rv_ratio"] = lr.rolling(5).std() / lr.rolling(60).std()
    f["ret"] = r
    f["next_ret"] = r.shift(-1)  # used only for ex-post data-quality diagnosis, never as a model input
    return f
