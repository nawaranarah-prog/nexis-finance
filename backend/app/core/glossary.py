"""Metric definitions served to the UI for tooltips: what it means, how it is computed, assumptions."""

from __future__ import annotations

GLOSSARY: dict[str, dict[str, str]] = {
    "cumulative_return": {
        "label": "Cumulative return",
        "what": "Total compounded return over the period.",
        "how": "∏(1 + rₜ) − 1 on daily simple returns.",
        "assumptions": "Dividends included only if reflected in adjusted closes.",
    },
    "annualized_return": {
        "label": "Annualised return",
        "what": "Geometric average yearly growth rate (CAGR).",
        "how": "(∏(1 + rₜ))^(252/n) − 1.",
        "assumptions": "252 trading days per year; short samples make annualisation unstable.",
    },
    "annualized_volatility": {
        "label": "Annualised volatility",
        "what": "Dispersion of daily returns scaled to one year.",
        "how": "Sample standard deviation of daily returns × √252.",
        "assumptions": "Square-root-of-time scaling assumes i.i.d. returns (ignores autocorrelation).",
    },
    "downside_deviation": {
        "label": "Downside deviation",
        "what": "Volatility of returns below a minimum acceptable return (the risk-free rate).",
        "how": "√(mean(min(rₜ − MAR, 0)²)) × √252.",
        "assumptions": "MAR equals the per-day risk-free rate.",
    },
    "sharpe_ratio": {
        "label": "Sharpe ratio",
        "what": "Excess return per unit of total volatility.",
        "how": "mean(rₜ − r_f) / std(rₜ − r_f) × √252.",
        "assumptions": "Uses the configured annual risk-free rate converted to daily. Undefined (n/a) if volatility is zero.",
    },
    "sortino_ratio": {
        "label": "Sortino ratio",
        "what": "Excess return per unit of downside deviation.",
        "how": "mean(rₜ − r_f) × 252 / downside deviation.",
        "assumptions": "Penalises only returns below the risk-free rate.",
    },
    "max_drawdown": {
        "label": "Maximum drawdown",
        "what": "Largest peak-to-trough decline of the wealth index.",
        "how": "min(Wₜ / max_{s≤t} W_s − 1), with the starting value included as a peak.",
        "assumptions": "Measured on daily closes; intraday troughs are not captured.",
    },
    "calmar_ratio": {
        "label": "Calmar ratio",
        "what": "Annualised return relative to the worst drawdown.",
        "how": "Annualised return / |maximum drawdown|.",
        "assumptions": "Computed over the full selected period (not the conventional trailing 36 months).",
    },
    "beta": {
        "label": "Beta",
        "what": "Sensitivity of returns to the benchmark.",
        "how": "cov(r, r_b) / var(r_b) on overlapping days.",
        "assumptions": "Linear, constant relationship over the sample.",
    },
    "alpha": {
        "label": "Jensen's alpha (historical)",
        "what": "Annualised intercept of a CAPM regression — return not explained by benchmark exposure in-sample.",
        "how": "(mean(r − r_f) − β·mean(r_b − r_f)) × 252.",
        "assumptions": "Historical estimate with sampling error; not a forecast of future excess return.",
    },
    "correlation": {
        "label": "Correlation",
        "what": "Linear co-movement with the benchmark (−1 to 1).",
        "how": "Pearson correlation of daily returns on overlapping days.",
        "assumptions": "Captures linear dependence only; unstable in stress periods.",
    },
    "tracking_error": {
        "label": "Tracking error",
        "what": "Volatility of the return difference versus the benchmark.",
        "how": "std(r − r_b) × √252.",
        "assumptions": "Uses daily active returns.",
    },
    "information_ratio": {
        "label": "Information ratio",
        "what": "Active return per unit of tracking error.",
        "how": "mean(r − r_b) × 252 / tracking error.",
        "assumptions": "Historical; sensitive to the benchmark choice.",
    },
    "var": {
        "label": "Value-at-Risk (VaR)",
        "what": "Loss threshold exceeded on roughly (1 − confidence) of days in the sample/model.",
        "how": "Historical: −quantile(returns, 1 − c). Parametric: −(μ + z·σ).",
        "assumptions": "Not a maximum loss. Historical VaR assumes the lookback is representative; parametric assumes normality.",
    },
    "cvar": {
        "label": "CVaR / Expected Shortfall",
        "what": "Average loss on days at or beyond the VaR threshold.",
        "how": "Historical: −mean(rₜ | rₜ ≤ VaR quantile). Parametric: −(μ − σ·φ(z)/(1 − c)).",
        "assumptions": "Few tail observations at high confidence make estimates noisy.",
    },
    "win_rate": {
        "label": "Win rate",
        "what": "Share of non-flat days with a positive return.",
        "how": "count(rₜ > 0) / count(rₜ ≠ 0).",
        "assumptions": "Day-level, not trade-level.",
    },
    "profit_factor": {
        "label": "Profit factor",
        "what": "Gross gains divided by gross losses.",
        "how": "Σ positive daily returns / |Σ negative daily returns|.",
        "assumptions": "Day-level aggregation of simple returns.",
    },
    "annualized_turnover": {
        "label": "Annualised turnover",
        "what": "How much of the portfolio is traded per year.",
        "how": "Σ(traded notional / equity) per year (backtests); Σ one-way weight change per year (portfolios).",
        "assumptions": "A value of 1.0 means the full portfolio value was traded once over a year.",
    },
    "transaction_costs": {
        "label": "Transaction costs",
        "what": "Commission and slippage charged on every trade.",
        "how": "|notional| × (commission bps + slippage bps) / 10,000.",
        "assumptions": "Linear costs; no market impact, borrow fees or taxes.",
    },
    "risk_contribution": {
        "label": "Contribution to volatility",
        "what": "Each asset's share of portfolio volatility (Euler decomposition).",
        "how": "wᵢ·(Σw)ᵢ / σ_p; contributions sum to portfolio volatility.",
        "assumptions": "Sample covariance over the analysis window.",
    },
    "herfindahl_index": {
        "label": "Herfindahl index",
        "what": "Weight concentration.",
        "how": "Σ wᵢ²; its inverse is the effective number of assets.",
        "assumptions": "Uses target weights.",
    },
    "diversification_ratio": {
        "label": "Diversification ratio",
        "what": "Weighted average standalone volatility divided by portfolio volatility.",
        "how": "Σ wᵢσᵢ / σ_p.",
        "assumptions": "≥ 1 for long-only portfolios; higher means more diversification benefit.",
    },
    "rmse": {
        "label": "RMSE",
        "what": "Root mean squared forecast error.",
        "how": "√mean((y − ŷ)²).",
        "assumptions": "Penalises large errors heavily; in volatility units.",
    },
    "mae": {
        "label": "MAE",
        "what": "Mean absolute forecast error.",
        "how": "mean(|y − ŷ|).",
        "assumptions": "In volatility units.",
    },
    "r2": {
        "label": "R²",
        "what": "Share of target variance explained by the forecast.",
        "how": "1 − SSE / SST on the evaluation split.",
        "assumptions": "Can be negative when a forecast is worse than the split mean.",
    },
    "qlike": {
        "label": "QLIKE",
        "what": "Volatility-forecast loss robust to noise in realised-volatility proxies.",
        "how": "mean(σ²/σ̂² − log(σ²/σ̂²) − 1).",
        "assumptions": "Lower is better; 0 is a perfect forecast.",
    },
    "data_quality_score": {
        "label": "Data quality score",
        "what": "Weighted score of completeness, validity, uniqueness, consistency and freshness.",
        "how": "0.30·completeness + 0.25·validity + 0.15·uniqueness + 0.20·consistency + 0.10·freshness.",
        "assumptions": "Large price moves are flagged as potential anomalies and do not reduce the score.",
    },
}
