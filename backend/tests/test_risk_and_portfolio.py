"""VaR/CVaR, risk decomposition, stress scenarios, portfolio simulation and optimisers."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from app.analytics.correlation import correlation_matrix, highly_correlated_pairs
from app.analytics.optimization import _risk_parity, allocate, validate_weights
from app.analytics.portfolio import rebalance_mask, simulate_portfolio
from app.core.errors import ConfigurationError, InsufficientDataError
from app.risk import var as V
from app.risk.contribution import concentration, risk_contributions
from app.risk.stress import run_scenario
from tests.conftest import make_prices

# --- VaR / CVaR -------------------------------------------------------------------------


def test_historical_var_and_cvar_hand_calculated():
    r = np.linspace(-0.05, 0.049, 100)  # r_k = -0.05 + 0.001k
    # 5% quantile at position 4.95 → -0.046 + 0.95 * 0.001 = -0.04505
    assert V.historical_var(r, 0.95) == pytest.approx(0.04505)
    # tail = r_0..r_4 → mean(-0.050..-0.046) = -0.048
    assert V.historical_cvar(r, 0.95) == pytest.approx(0.048)


def test_parametric_cvar_to_var_ratio_normal():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.01, 200_000)
    r = r - r.mean()
    ratio = V.parametric_cvar(r, 0.95) / V.parametric_var(r, 0.95)
    z = stats.norm.ppf(0.95)
    assert ratio == pytest.approx(stats.norm.pdf(z) / (0.05 * z), rel=1e-6)  # ≈ 1.2541


def test_var_monotonic_in_confidence_and_cvar_exceeds_var():
    rng = np.random.default_rng(2)
    r = rng.standard_t(4, 2000) * 0.01
    for c1, c2 in ((0.90, 0.95), (0.95, 0.99)):
        assert V.historical_var(r, c2) > V.historical_var(r, c1)
    for c in V.ALLOWED_CONFIDENCE:
        assert V.historical_cvar(r, c) >= V.historical_var(r, c)


def test_var_insufficient_observations():
    with pytest.raises(InsufficientDataError):
        V.historical_var(np.zeros(20), 0.95)
    with pytest.raises(InsufficientDataError):
        V.historical_var(np.zeros(60), 0.99)  # needs >= 100 at 99%


def test_var_rejects_bad_confidence():
    with pytest.raises(ConfigurationError):
        V.historical_var(np.zeros(500), 1.2)


def test_kupiec_test_expected_rate_not_rejected():
    res = V.kupiec_test(50, 1000, 0.95)
    assert res["lr_stat"] == pytest.approx(0.0, abs=1e-9) and res["p_value"] == pytest.approx(1.0)
    assert V.kupiec_test(120, 1000, 0.95)["p_value"] < 0.001


def test_rolling_var_backtest_uses_only_past_window():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2018-01-01", periods=600)
    r = pd.Series(rng.normal(0, 0.01, 600), index=idx)
    bt = V.rolling_var_backtest(r, 0.95, 250)
    # First VaR estimate is dated at observation 251 (window 0..249 strictly before it).
    assert bt["dates"][0] == idx[250].date().isoformat()
    expected_first = -np.quantile(r.iloc[:250], 0.05)
    assert bt["var"][0] == pytest.approx(expected_first)


# --- Risk decomposition -----------------------------------------------------------------------


def test_risk_contributions_sum_to_volatility():
    rng = np.random.default_rng(4)
    r = pd.DataFrame(
        rng.multivariate_normal([0, 0, 0], [[1e-4, 2e-5, 0], [2e-5, 4e-4, 1e-5], [0, 1e-5, 9e-4]], 1000), columns=list("ABC")
    )
    w = pd.Series([0.5, 0.3, 0.2], index=list("ABC"))
    rc = risk_contributions(w, r)
    total = sum(a["contribution"] for a in rc["assets"])
    assert total == pytest.approx(rc["portfolio_volatility"])
    assert sum(a["pct_contribution"] for a in rc["assets"]) == pytest.approx(1.0)
    assert rc["diversification_ratio"] >= 1.0


def test_concentration():
    c = concentration(pd.Series([0.5, 0.25, 0.25]))
    assert c["herfindahl_index"] == pytest.approx(0.375)
    assert c["effective_number_of_assets"] == pytest.approx(1 / 0.375)


# --- Portfolio simulation ------------------------------------------------------------------------


def test_buy_and_hold_drift_vs_daily_rebalance():
    idx = pd.bdate_range("2024-01-01", periods=2)
    r = pd.DataFrame({"A": [0.1, 0.1], "B": [0.0, 0.0]}, index=idx)
    w = pd.Series({"A": 0.5, "B": 0.5})
    hold = simulate_portfolio(r, w, "none")
    assert hold.returns.tolist() == pytest.approx([0.05, 0.55 / 1.05 * 0.1])
    daily = simulate_portfolio(r, w, "daily")
    assert daily.returns.tolist() == pytest.approx([0.05, 0.05])
    # Day-1 rebalance trades back from (0.5238, 0.4762) to (0.5, 0.5): one-way turnover 0.0238
    assert daily.turnover.iloc[0] == pytest.approx(0.55 / 1.05 - 0.5)


def test_rebalance_mask_month_ends():
    idx = pd.bdate_range("2024-01-01", "2024-03-31")
    m = rebalance_mask(idx, "monthly")
    assert [d.date().isoformat() for d in idx[m]] == ["2024-01-31", "2024-02-29", "2024-03-29"]


def test_cash_residual_earns_zero():
    idx = pd.bdate_range("2024-01-01", periods=3)
    r = pd.DataFrame({"A": [0.1, 0.1, 0.1]}, index=idx)
    sim = simulate_portfolio(r, pd.Series({"A": 0.5}), "daily")
    assert sim.returns.tolist() == pytest.approx([0.05, 0.05, 0.05])


def test_weight_validation():
    validate_weights({"A": 0.6, "B": 0.4})
    with pytest.raises(ConfigurationError):
        validate_weights({"A": 0.6, "B": 0.5})
    with pytest.raises(ConfigurationError):
        validate_weights({"A": float("nan"), "B": 1.0})
    with pytest.raises(ConfigurationError):
        validate_weights({"A": 0.9, "B": 0.1}, max_weight=0.5)


def test_risk_parity_diagonal_covariance_is_inverse_vol():
    cov = np.diag([0.1**2, 0.2**2])
    w = _risk_parity(cov)
    assert w == pytest.approx([2 / 3, 1 / 3], abs=1e-6)


def test_risk_parity_equalises_contributions():
    cov = np.array([[0.04, 0.006, 0.0], [0.006, 0.09, 0.012], [0.0, 0.012, 0.01]])
    w = _risk_parity(cov)
    rc = w * (cov @ w)
    assert np.allclose(rc / rc.sum(), 1 / 3, atol=1e-5)


def test_min_variance_two_uncorrelated_assets():
    rng = np.random.default_rng(5)
    r = pd.DataFrame({"A": rng.normal(0, 0.01, 20000), "B": rng.normal(0, 0.02, 20000)})
    res = allocate("min_variance", r, shrinkage=False)
    # Analytic: w_A = σB² / (σA² + σB²) = 0.8
    assert res.weights["A"] == pytest.approx(0.8, abs=0.01)


def test_optimizer_respects_bounds_and_infeasible_bounds_error():
    rng = np.random.default_rng(6)
    r = pd.DataFrame(rng.normal(0.0005, 0.01, (500, 4)), columns=list("ABCD"))
    res = allocate("min_variance", r, max_weight=0.3)
    assert res.weights.max() <= 0.3 + 1e-6 and res.weights.sum() == pytest.approx(1.0)
    with pytest.raises(ConfigurationError):
        allocate("min_variance", r, max_weight=0.2)  # 4 × 20% < 100%


def test_inverse_volatility_rejects_zero_vol_asset():
    r = pd.DataFrame({"A": np.random.default_rng(0).normal(0, 0.01, 100), "B": np.zeros(100)})
    with pytest.raises(ConfigurationError):
        allocate("inverse_volatility", r)


# --- Correlation ------------------------------------------------------------------------------------


def test_correlation_matrix_exposes_pair_counts():
    rng = np.random.default_rng(8)
    df = pd.DataFrame(rng.normal(0, 1, (100, 3)), columns=list("ABC"))
    df.loc[:49, "C"] = np.nan
    m = correlation_matrix(df, cluster=False)
    i = m["symbols"].index("C")
    j = m["symbols"].index("A")
    assert m["pair_counts"][i][j] == 50 and m["complete_case_observations"] == 50
    df["D"] = df["A"] * 2 + 0.001
    pairs = highly_correlated_pairs(df, 0.99)
    assert pairs[0]["a"] == "A" and pairs[0]["b"] == "D"


# --- Stress -----------------------------------------------------------------------------------------


def test_market_shock_uses_beta():
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2020-01-01", periods=300)
    m = pd.Series(rng.normal(0, 0.01, 300), index=idx)
    assets = pd.DataFrame({"A": 1.5 * m, "B": 0.5 * m}, index=idx)
    w = pd.Series({"A": 0.5, "B": 0.5})
    res = run_scenario("market_shock", {"shock": -0.10}, w, assets, m, {"A": "X", "B": "Y"}, 1_000_000)
    assert res["portfolio_return"] == pytest.approx(0.5 * -0.15 + 0.5 * -0.05)
    assert res["portfolio_value_after"] == pytest.approx(900_000)
    assert res["largest_contributors"][0]["symbol"] == "A"


def test_custom_and_invalid_scenarios():
    idx = pd.bdate_range("2020-01-01", periods=100)
    assets = pd.DataFrame({"A": np.zeros(100), "B": np.zeros(100)}, index=idx)
    w = pd.Series({"A": 0.6, "B": 0.4})
    res = run_scenario("custom", {"asset_shocks": {"A": -0.2}}, w, assets, assets["A"], {}, 100.0)
    assert res["portfolio_return"] == pytest.approx(-0.12)
    with pytest.raises(ConfigurationError):
        run_scenario("market_shock", {"shock": -2}, w, assets, assets["A"], {}, 100.0)
    with pytest.raises(ConfigurationError):
        run_scenario("nonsense", {}, w, assets, assets["A"], {}, 100.0)


def test_correlation_stress_increases_volatility():
    rng = np.random.default_rng(10)
    idx = pd.bdate_range("2020-01-01", periods=400)
    assets = pd.DataFrame(rng.normal(0, 0.01, (400, 3)), columns=list("ABC"), index=idx)
    w = pd.Series({"A": 1 / 3, "B": 1 / 3, "C": 1 / 3})
    res = run_scenario("correlation_increase", {"intensity": 1.0}, w, assets, assets["A"], {}, 1.0)
    # Perfect correlation: portfolio vol = weighted average vol.
    sd = assets.std().to_numpy()
    assert res["stressed"]["daily_volatility"] == pytest.approx(float(sd @ w.to_numpy()), rel=1e-6)
    assert res["stressed"]["daily_volatility"] > res["baseline"]["daily_volatility"]


def test_historical_worst_window():
    px = make_prices({"A": [100, 100, 90, 80, 85, 100, 100, 100, 100, 100, 100, 100]})
    r = px.pct_change().iloc[1:]
    res = run_scenario("historical_worst", {"window_days": 2}, pd.Series({"A": 1.0}), r, r["A"], {}, 1.0)
    assert res["portfolio_return"] == pytest.approx(80 / 100 - 1)
    assert math.isclose(res["loss_pct"], 0.2)
