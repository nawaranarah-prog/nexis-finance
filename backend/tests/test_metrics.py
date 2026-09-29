"""Financial math verified against hand-calculated values."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.analytics import metrics as M
from app.core.errors import InsufficientDataError


def test_simple_returns_and_cumulative():
    px = pd.Series([100.0, 110.0, 104.5, 106.59])
    r = M.simple_returns(px)
    assert r.tolist() == pytest.approx([0.10, -0.05, 0.02])
    assert M.cumulative_return(r) == pytest.approx(1.1 * 0.95 * 1.02 - 1)  # 0.0659


def test_returns_do_not_fill_missing_prices():
    px = pd.Series([100.0, np.nan, 110.0])
    r = M.simple_returns(px)
    assert r.isna().all()  # a gap never becomes a fake 0% or a fake jump


def test_non_positive_prices_rejected():
    with pytest.raises(InsufficientDataError):
        M.simple_returns(pd.Series([100.0, 0.0, 101.0]))


def test_annualized_return_geometric():
    r = [0.1, -0.05, 0.02]
    # With 3 periods per year the annualised return equals the cumulative return.
    assert M.annualized_return(r, periods_per_year=3) == pytest.approx(0.0659)
    assert M.annualized_return(r, periods_per_year=252) == pytest.approx(1.0659 ** (252 / 3) - 1)


def test_annualized_return_total_loss_bounded():
    assert M.annualized_return([-1.0, 0.1]) == -1.0


def test_volatility_hand_calculated():
    r = [0.1, -0.05, 0.02]
    # mean = 0.023333; squared deviations sum = 0.0112667; /2 -> 0.00563333
    assert M.annualized_volatility(r, 1) == pytest.approx(math.sqrt(0.0112666667 / 2), rel=1e-6)
    assert M.annualized_volatility(r) == pytest.approx(math.sqrt(0.0112666667 / 2) * math.sqrt(252), rel=1e-6)


def test_sharpe_known_values():
    assert M.sharpe_ratio([0.02, 0.0]) == pytest.approx(0.01 / math.sqrt(0.0002) * math.sqrt(252))
    assert M.sharpe_ratio([0.01, -0.01, 0.01, -0.01]) == pytest.approx(0.0)


def test_sharpe_with_risk_free_rate():
    r = [0.001, 0.002, 0.0015, 0.0005]
    rf_d = (1.02) ** (1 / 252) - 1
    ex = np.array(r) - rf_d
    assert M.sharpe_ratio(r, 0.02) == pytest.approx(ex.mean() / ex.std(ddof=1) * math.sqrt(252))


def test_zero_variance_gives_none_not_inf():
    flat = [0.0] * 50
    assert M.sharpe_ratio(flat) is None
    assert M.sortino_ratio(flat) is None
    assert M.annualized_volatility(flat) == 0.0
    assert M.max_drawdown(flat) == 0.0
    assert M.calmar_ratio(flat) is None


def test_sortino_hand_calculated():
    r = [0.02, -0.01, 0.03, -0.02]
    dd = math.sqrt((0.01**2 + 0.02**2) / 4) * math.sqrt(252)
    assert M.downside_deviation(r) == pytest.approx(dd)
    assert M.sortino_ratio(r) == pytest.approx(0.005 * 252 / dd)


def test_max_drawdown_prices():
    px = pd.Series([100.0, 120.0, 90.0, 130.0, 117.0], index=pd.bdate_range("2024-01-01", periods=5))
    r = M.simple_returns(px)
    assert M.max_drawdown(r) == pytest.approx(90 / 120 - 1)  # -25%
    d = M.max_drawdown_details(r)
    assert d["peak_date"] == "2024-01-02" and d["trough_date"] == "2024-01-03" and d["recovery_date"] == "2024-01-04"


def test_first_day_loss_counts_as_drawdown():
    assert M.max_drawdown([-0.1, 0.05]) == pytest.approx(-0.1)


def test_calmar():
    r = [0.1, -0.2, 0.3]
    assert M.calmar_ratio(r, 3) == pytest.approx(M.annualized_return(r, 3) / 0.2)


def test_beta_correlation_tracking_error():
    idx = pd.bdate_range("2024-01-01", periods=4)
    b = pd.Series([0.01, -0.02, 0.03, 0.0], index=idx)
    r = 2 * b
    assert M.beta(r, b) == pytest.approx(2.0)
    assert M.correlation(r, b) == pytest.approx(1.0)
    assert M.tracking_error(r, b) == pytest.approx(b.std(ddof=1) * math.sqrt(252))


def test_beta_zero_variance_benchmark_is_none():
    idx = pd.bdate_range("2024-01-01", periods=5)
    assert M.beta(pd.Series([0.01, 0.02, 0.0, 0.01, 0.0], index=idx), pd.Series(0.0, index=idx)) is None


def test_beta_uses_overlapping_observations_only():
    idx = pd.bdate_range("2024-01-01", periods=5)
    b = pd.Series([0.01, -0.02, 0.03, 0.0, 0.01], index=idx)
    r = 2 * b
    r.iloc[2] = np.nan
    assert M.beta(r, b) == pytest.approx(2.0)


def test_win_rate_and_profit_factor():
    r = [0.02, -0.01, 0.0, 0.03, -0.02]
    assert M.win_rate(r) == pytest.approx(0.5)  # zero-return day excluded
    assert M.profit_factor(r) == pytest.approx(0.05 / 0.03)
    assert M.profit_factor([0.01, 0.02]) is None


@pytest.mark.parametrize("fn", [M.annualized_return, M.annualized_volatility, M.sharpe_ratio])
def test_insufficient_observations_raise(fn):
    with pytest.raises(InsufficientDataError):
        fn([0.01])
    with pytest.raises(InsufficientDataError):
        fn([])


def test_negative_returns_series():
    r = [-0.01] * 10
    assert M.cumulative_return(r) == pytest.approx(0.99**10 - 1)
    assert M.max_drawdown(r) == pytest.approx(0.99**10 - 1)
    assert M.sharpe_ratio(r) is None  # constant negative excess return has zero variance


def test_performance_summary_contains_no_nan():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2020-01-01", periods=300)
    r = pd.Series(rng.normal(0.0004, 0.01, 300), index=idx)
    b = pd.Series(rng.normal(0.0003, 0.009, 300), index=idx)
    s = M.performance_summary(r, b, 0.02)
    for k, v in s.items():
        if isinstance(v, float):
            assert math.isfinite(v), k


def test_performance_summary_single_asset_constant_prices():
    idx = pd.bdate_range("2020-01-01", periods=30)
    r = pd.Series(0.0, index=idx)
    s = M.performance_summary(r)
    assert s["sharpe_ratio"] is None and s["annualized_volatility"] == 0.0 and s["max_drawdown"] == 0.0


def test_missing_dates_in_returns_are_dropped_not_filled():
    idx = pd.bdate_range("2020-01-01", periods=4)
    r = pd.Series([0.1, np.nan, -0.05, 0.02], index=idx)
    assert M.cumulative_return(r) == pytest.approx(1.1 * 0.95 * 1.02 - 1)
