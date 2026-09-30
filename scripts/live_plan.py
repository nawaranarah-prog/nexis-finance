"""The research set-up of a public instance: real symbols, portfolios, backtests and models.

Pure data, shared by ``seed_live.py`` (direct database access) and ``bootstrap_public.py``
(through the REST API of a deployed instance), so both build exactly the same workspace.
"""

from __future__ import annotations

from typing import Any

EQUITIES = [
    # Technology & communication
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    # Financials
    "JPM", "BAC", "GS", "V",
    # Health care
    "JNJ", "UNH", "PFE", "MRK",
    # Energy
    "XOM", "CVX", "COP",
    # Industrials & materials
    "CAT", "HON", "GE", "LIN",
    # Consumer
    "PG", "KO", "PEP", "WMT", "COST", "HD", "MCD",
    # Utilities
    "NEE", "DUK", "SO",
]  # fmt: skip
ETFS = ["QQQ", "IWM", "EFA", "AGG", "TLT", "GLD", "VNQ"]
BENCHMARK = "SPY"
START = "2015-01-02"
MARKET_CONFIG = {"symbols": ",".join(EQUITIES + ETFS + [BENCHMARK]), "start": START}
SEC_CONFIG = {"symbols": ",".join(EQUITIES)}

_DIVERSIFIED = ["AAPL", "MSFT", "JPM", "V", "JNJ", "UNH", "XOM", "CAT", "LIN", "PG", "KO", "WMT", "NEE", "AGG", "TLT", "GLD"]


def portfolios(dataset_id: int) -> list[dict[str, Any]]:
    common = {
        "dataset_id": dataset_id,
        "benchmark_symbol": BENCHMARK,
        "initial_capital": 1_000_000,
        "rebalance_frequency": "monthly",
        "estimation_start": "2017-01-03",
        "estimation_end": "2021-12-31",
    }
    specs = [
        {"name": "Diversified Equal Weight", "symbols": _DIVERSIFIED, "allocation_method": "equal_weight",
         "notes": "Equal weight across sectors plus bonds and gold."},
        {"name": "Diversified Minimum Variance", "symbols": _DIVERSIFIED, "allocation_method": "min_variance", "max_weight": 0.25,
         "notes": "Long-only minimum variance, Ledoit-Wolf covariance, estimated 2017–2021."},
        {"name": "Diversified Risk Parity", "symbols": _DIVERSIFIED, "allocation_method": "risk_parity",
         "notes": "Equal risk contribution weights estimated 2017–2021."},
        {"name": "US Mega-Cap Tech (custom)", "symbols": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"],
         "allocation_method": "custom",
         "weights": {"AAPL": 0.2, "MSFT": 0.2, "NVDA": 0.2, "GOOGL": 0.15, "AMZN": 0.15, "META": 0.1},
         "notes": "Concentrated portfolio for concentration and stress analysis."},
    ]  # fmt: skip
    return [{**common, **s} for s in specs]


def backtests(dataset_id: int, end: str) -> list[tuple[str, dict[str, Any]]]:
    """(kind, request) pairs; kind is ``backtest`` or ``walk_forward``."""
    base = {
        "dataset_id": dataset_id,
        "benchmark_symbol": BENCHMARK,
        "start_date": "2016-01-04",
        "end_date": end,
        "initial_capital": 1_000_000,
        "commission_bps": 5,
        "slippage_bps": 5,
        "execution": "next_open",
        "risk_free_rate": 0.02,
    }
    return [
        ("backtest", {
            **base, "name": "Momentum top-8, IS-selected lookback", "strategy": "momentum",
            "params": {"top_n": 8, "rebalance": "monthly"}, "param_grid": {"lookback": [63, 126, 252], "skip": [0, 21]},
            "train_end": "2021-12-31", "validation_end": "2023-12-29",
            "notes": "Lookback/skip chosen by in-sample Sharpe only.",
        }),
        ("backtest", {
            **base, "name": "Mean reversion z=2.0/0.25, 20d", "strategy": "mean_reversion",
            "params": {"window": 20, "entry_z": 2.0, "exit_z": 0.25, "max_positions": 10},
            "train_end": "2021-12-31", "validation_end": "2023-12-29",
        }),
        ("backtest", {**base, "name": "Equal-weight monthly baseline", "strategy": "equal_weight",
                      "params": {"rebalance": "monthly"}}),
        ("walk_forward", {
            **base, "name": "Momentum walk-forward (2y train / 6m test)", "strategy": "momentum",
            "params": {"top_n": 8}, "param_grid": {"lookback": [63, 126, 252]}, "train_days": 504, "test_days": 126,
        }),
    ]  # fmt: skip


def ml_experiments(dataset_id: int) -> list[tuple[str, dict[str, Any]]]:
    """(kind, request) pairs; kind is ``volatility``, ``regime`` or ``anomaly``."""
    return [
        ("volatility", {
            "dataset_id": dataset_id, "symbols": ["AAPL", "NVDA", "JPM", "XOM", "JNJ", "KO", "NEE"],
            "benchmark_symbol": BENCHMARK, "horizon": 10, "train_end": "2021-12-31", "validation_end": "2023-12-29",
            "seed": 42, "name": "10-day realised volatility, 7 stocks pooled",
        }),
        ("regime", {
            "dataset_id": dataset_id, "benchmark_symbol": BENCHMARK, "n_regimes": 3, "method": "gmm",
            "train_end": "2022-12-30", "seed": 42, "name": "GMM 3-regime model on SPY",
        }),
        ("anomaly", {"dataset_id": dataset_id, "contamination": 0.002, "z_threshold": 6.0, "seed": 42,
                     "name": "Universe anomaly scan"}),
    ]  # fmt: skip


REPORT_TITLE = "Nexis Research Report — US large caps"
REPORT_NOTES = "Built from real market data (Yahoo Finance chart endpoint) and SEC EDGAR filings."
