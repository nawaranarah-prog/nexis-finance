# Quantitative methodology

All formulas below are implemented in `backend/app/analytics`, `app/risk`, `app/backtesting` and `app/ml`, and are unit-tested against hand-calculated values (`backend/tests`).

## 1. Returns and performance

| Quantity | Definition |
|---|---|
| Simple return | rₜ = Pₜ / Pₜ₋₁ − 1 on adjusted closes. A missing price gives NaN, never 0. |
| Cumulative return | ∏(1 + rₜ) − 1 |
| Annualised return (CAGR) | (∏(1 + rₜ))^(252/n) − 1, bounded at −100% |
| Annualised volatility | s(rₜ) · √252 (sample standard deviation, ddof = 1) |
| Per-period risk-free rate | r_f,d = (1 + r_f)^(1/252) − 1 |
| Sharpe ratio | mean(rₜ − r_f,d) / s(rₜ − r_f,d) · √252; `null` if s = 0 |
| Downside deviation | √(mean(min(rₜ − r_f,d, 0)²)) · √252 |
| Sortino ratio | mean(rₜ − r_f,d) · 252 / downside deviation |
| Drawdown | Wₜ / max(1, max_{s≤t} W_s) − 1, where W is the wealth index (the start counts as a peak) |
| Calmar ratio | CAGR / \|max drawdown\| |
| Beta | cov(r, r_b) / var(r_b) on overlapping, non-missing days |
| Tracking error | s(r − r_b) · √252 |
| Information ratio | mean(r − r_b) · 252 / tracking error |
| Jensen's alpha | (mean(r − r_f) − β · mean(r_b − r_f)) · 252, historical |
| Win rate | share of non-zero days with r > 0 |
| Profit factor | Σ positive daily returns / \|Σ negative daily returns\| |

## 2. Portfolio simulation

Weights drift with returns: wₜ₊ = wₜ(1 + rₜ)/(1 + r_p,t). On the last trading day of each rebalance period (weekly / monthly / quarterly / annually, or daily / none), weights reset to target after that day's return. One-way turnover is ½ Σ|w_target − w_drifted|. Missing prices within the analysis window are forward-filled for valuation (a 0% return while missing, with the full move when trading resumes), and the count of filled cells is disclosed.

### Allocation methods
* **Inverse volatility:** wᵢ ∝ 1/σᵢ.
* **Minimum variance:** min wᵀΣw subject to Σw = 1 and lo ≤ wᵢ ≤ hi (SLSQP). Infeasible bounds (n·hi < 1 or n·lo > 1) are rejected up front.
* **Maximum Sharpe:** max (wᵀμ − r_f) / √(wᵀΣw) under the same constraints. It is ill-posed, and rejected, if no asset's in-sample mean exceeds r_f. It is known to be highly sensitive to estimation error in μ.
* **Risk parity:** minimise ½yᵀΣy − (1/n)Σ log yᵢ over y > 0 (Spinu, 2013), then set w = y/Σy. This yields equal risk contributions exactly (tested).
* **Covariance:** Ledoit-Wolf shrinkage by default, annualised by ×252. The shrinkage intensity is stored with the portfolio.

### Risk decomposition
σ_p = √(wᵀΣw); MCRᵢ = (Σw)ᵢ/σ_p; CRᵢ = wᵢ·MCRᵢ; ΣCRᵢ = σ_p. The diversification ratio is Σwᵢσᵢ / σ_p. HHI is Σwᵢ², and the effective number of assets is 1/HHI.

## 3. VaR and Expected Shortfall

Losses are reported as positive fractions for confidence c ∈ {0.90, 0.95, 0.975, 0.99}.

* **Historical:** VaR = −Q_{1−c}(r) (linear interpolation). CVaR = −mean(r | r ≤ Q_{1−c}). For horizon h > 1, overlapping compounded h-day returns are used; the overlap is noted.
* **Parametric normal:** VaR = −(μh + z_{1−c} σ√h); CVaR = −(μh − σ√h · φ(z_{1−c})/(1−c)).
* **Cornish-Fisher:** z_cf = z + (z²−1)S/6 + (z³−3z)K/24 − (2z³−5z)S²/36, using sample skewness S and excess kurtosis K.
* **Sample size:** at least max(30, ⌈1/(1−c)⌉) observations are required. Estimates with fewer than 10 tail observations are flagged.
* **Backtest:** at each day t, VaRₜ is estimated from the preceding `window` returns only, and exceptions are counted. The Kupiec POF statistic is LR = −2[ln L(p) − ln L(x/n)] ~ χ²(1).

## 4. Stress scenarios (hypothetical)

These are applied to current drifted holdings at the latest valuation:

* **Market shock s:** the asset move is βᵢ·s, with βᵢ estimated against the benchmark over the lookback.
* **Sector shock s:** sector members move by s; other assets move by their beta to the equal-weight sector index × s.
* **Volatility spike m:** Σ′ = m²Σ; stressed parametric VaR/CVaR are reported.
* **Correlation increase λ:** ρ′ = ρ + λ(1 − ρ) with volatilities held fixed; stressed volatility and VaR are reported.
* **Worst historical window:** the minimum compounded h-day portfolio return in the lookback, replayed with per-asset returns.
* **Custom:** user-specified per-asset returns.

## 5. Factor proxies

Characteristics are computed at month-end using data up to that close only:

* **momentum:** P_{t−21}/P_{t−252} − 1
* **low_vol:** −σ₆₃
* **size:** −log of mean 63-day dollar volume, a liquidity proxy rather than market capitalisation
* **value:** −3-year return, a long-term-reversal proxy rather than book-to-market
* **quality:** 1-year worst drawdown, a stability proxy

Top-minus-bottom quintile, equal-weighted portfolios are held over the following month (weights constant within the month). Exposure is an OLS regression of portfolio returns on the market plus the factors, with Newey-West (5-lag) standard errors. Rolling betas use a trailing window.

## 6. Correlation
The Pearson or Spearman matrix is computed on pairwise-complete observations, with the observation count per cell exposed. Hierarchical clustering uses average linkage on d = √(½(1 − ρ)). The rolling average correlation is the mean off-diagonal ρ in each trailing window, using the assets with complete data in that window.

## 7. Strategies

* **Cross-sectional momentum:** score = P_{t−skip}/P_{t−skip−lookback} − 1 over assets with valid prices at t and at both window ends. Hold the top N, with equal slots or inverse volatility, and optionally an absolute-momentum filter where unused slots stay in cash. The long/short mode is 50% long / 50% short.
* **Z-score mean reversion:** zₜ = (Pₜ − mean_w)/sd_w. Enter long at z ≤ −entry and exit at z ≥ −exit, with the mirror rule for shorts when enabled, and a maximum holding period. Each position gets a fixed slot of 1/max_positions. The state machine only changes on events, so hysteresis limits churn.
* **Trend filter:** each asset gets a 1/N slot while Pₜ > SMA_N; otherwise the slot is held in cash.
* **Equal weight:** all tradable assets, rebalanced on schedule. This is the naive baseline.

## 8. ML experiments

See the README section *Machine-learning methodology* for features, targets, purging, baselines and the selection rule. Loss definitions: MAE and RMSE are in volatility units, R² = 1 − SSE/SST on the split, and QLIKE = mean(σ²/σ̂² − ln(σ²/σ̂²) − 1).
