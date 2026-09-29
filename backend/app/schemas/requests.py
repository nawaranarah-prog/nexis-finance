"""Request schemas. Validation here rejects malformed input before it reaches business logic."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Symbol = str


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _symbols(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    out = []
    for s in v:
        s = s.strip().upper()
        if not s or len(s) > 32 or not all(c.isalnum() or c in ".-^=_" for c in s):
            raise ValueError(f"invalid symbol '{s}'")
        out.append(s)
    return out


class IngestRequest(_Base):
    provider: Literal["synthetic", "public"] = "synthetic"
    dataset_code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    dataset_name: str = Field(min_length=2, max_length=200)
    symbols: list[str] | None = Field(default=None, max_length=100)
    benchmark_symbols: list[str] | None = None
    start: date | None = None
    end: date | None = None
    mode: Literal["incremental", "full"] = "incremental"
    seed: int = Field(default=42, ge=0, le=2**31 - 1)

    @field_validator("symbols", "benchmark_symbols")
    @classmethod
    def normalize_symbols(cls, v: list[str] | None) -> list[str] | None:
        return _symbols(v)

    @model_validator(mode="after")
    def _check(self) -> IngestRequest:
        if self.provider == "public" and not self.symbols:
            raise ValueError("the public provider requires an explicit list of symbols")
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class PortfolioCreate(_Base):
    name: str = Field(min_length=1, max_length=120)
    dataset_id: int
    benchmark_symbol: str
    symbols: list[str] = Field(min_length=1, max_length=60)
    allocation_method: Literal["custom", "equal_weight", "inverse_volatility", "min_variance", "max_sharpe", "risk_parity"] = (
        "equal_weight"
    )
    weights: dict[str, float] | None = None
    min_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    max_weight: float = Field(default=1.0, gt=0.0, le=1.0)
    estimation_start: date | None = None
    estimation_end: date | None = None
    initial_capital: float = Field(default=1_000_000.0, ge=1_000, le=1e12)
    rebalance_frequency: Literal["none", "daily", "weekly", "monthly", "quarterly", "annually"] = "monthly"
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: list[str] | None) -> list[str] | None:
        return _symbols(v)

    @field_validator("benchmark_symbol")
    @classmethod
    def _b(cls, v: str) -> str:
        return (_symbols([v]) or [v])[0]

    @field_validator("weights")
    @classmethod
    def _w(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        return None if v is None else {k.strip().upper(): float(x) for k, x in v.items()}

    @model_validator(mode="after")
    def _check(self) -> PortfolioCreate:
        if self.min_weight > self.max_weight:
            raise ValueError("min_weight must not exceed max_weight")
        if self.estimation_start and self.estimation_end and self.estimation_start >= self.estimation_end:
            raise ValueError("estimation_start must be before estimation_end")
        return self


class RiskAnalyzeRequest(_Base):
    portfolio_id: int
    confidences: list[float] = Field(default=[0.95, 0.99], min_length=1, max_length=4)
    lookback_days: int = Field(default=756, ge=60, le=5000)
    horizon_days: int = Field(default=1, ge=1, le=20)
    backtest_window: int = Field(default=250, ge=60, le=1000)


class StressTestRequest(_Base):
    portfolio_id: int
    name: str = Field(min_length=1, max_length=160)
    scenario_type: Literal[
        "market_shock", "sector_shock", "volatility_spike", "correlation_increase", "custom", "historical_worst"
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)
    lookback_days: int = Field(default=504, ge=60, le=5000)
    confidence: float = Field(default=0.99, ge=0.9, le=0.995)
    save: bool = True


class BacktestRequest(_Base):
    name: str | None = Field(default=None, max_length=200)
    dataset_id: int
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    universe: list[str] | None = Field(default=None, max_length=200)
    benchmark_symbol: str | None = None
    start_date: date
    end_date: date
    initial_capital: float = Field(default=1_000_000.0, ge=1_000, le=1e12)
    commission_bps: float = Field(default=5.0, ge=0, le=500)
    slippage_bps: float = Field(default=5.0, ge=0, le=500)
    execution: Literal["next_open", "next_close"] = "next_open"
    train_end: date | None = None
    validation_end: date | None = None
    param_grid: dict[str, list[Any]] | None = None
    objective: Literal["sharpe_ratio", "annualized_return", "calmar_ratio", "sortino_ratio"] = "sharpe_ratio"
    risk_free_rate: float = Field(default=0.02, ge=-0.05, le=0.25)
    seed: int | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("universe")
    @classmethod
    def normalize_symbols(cls, v: list[str] | None) -> list[str] | None:
        return _symbols(v)

    @model_validator(mode="after")
    def _check(self) -> BacktestRequest:
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if self.validation_end and not self.train_end:
            raise ValueError("validation_end requires train_end")
        return self


class WalkForwardRequest(_Base):
    name: str | None = Field(default=None, max_length=200)
    dataset_id: int
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    param_grid: dict[str, list[Any]]
    universe: list[str] | None = Field(default=None, max_length=200)
    benchmark_symbol: str | None = None
    start_date: date
    end_date: date
    train_days: int = Field(default=504, ge=126, le=2520)
    test_days: int = Field(default=126, ge=21, le=504)
    anchored: bool = False
    initial_capital: float = Field(default=1_000_000.0, ge=1_000, le=1e12)
    commission_bps: float = Field(default=5.0, ge=0, le=500)
    slippage_bps: float = Field(default=5.0, ge=0, le=500)
    execution: Literal["next_open", "next_close"] = "next_open"
    objective: Literal["sharpe_ratio", "annualized_return", "calmar_ratio", "sortino_ratio"] = "sharpe_ratio"
    risk_free_rate: float = Field(default=0.02, ge=-0.05, le=0.25)
    seed: int | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("universe")
    @classmethod
    def normalize_symbols(cls, v: list[str] | None) -> list[str] | None:
        return _symbols(v)


class VolatilityExperimentRequest(_Base):
    name: str | None = Field(default=None, max_length=200)
    dataset_id: int
    symbols: list[str] = Field(min_length=1, max_length=20)
    benchmark_symbol: str | None = None
    horizon: int = Field(default=10, ge=1, le=63)
    start: date | None = None
    end: date | None = None
    train_end: date
    validation_end: date | None = None
    features: list[str] | None = None
    models: list[Literal["naive_hist_vol", "ewma_vol", "ridge", "random_forest", "gradient_boosting"]] | None = None
    min_improvement: float = Field(default=0.02, ge=0, le=0.5)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: list[str] | None) -> list[str] | None:
        return _symbols(v)

    @model_validator(mode="after")
    def _check(self) -> VolatilityExperimentRequest:
        if self.validation_end and self.validation_end <= self.train_end:
            raise ValueError("validation_end must be after train_end")
        return self


class RegimeExperimentRequest(_Base):
    name: str | None = Field(default=None, max_length=200)
    dataset_id: int
    benchmark_symbol: str | None = None
    n_regimes: int = Field(default=4, ge=2, le=8)
    method: Literal["gmm", "kmeans"] = "gmm"
    features: list[str] | None = None
    start: date | None = None
    end: date | None = None
    train_end: date
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    notes: str | None = Field(default=None, max_length=2000)


class AnomalyExperimentRequest(_Base):
    name: str | None = Field(default=None, max_length=200)
    dataset_id: int
    symbols: list[str] | None = Field(default=None, max_length=200)
    contamination: float = Field(default=0.002, ge=0.0001, le=0.05)
    z_threshold: float = Field(default=6.0, ge=3, le=20)
    start: date | None = None
    end: date | None = None
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: list[str] | None) -> list[str] | None:
        return _symbols(v)


class ReportRequest(_Base):
    title: str = Field(min_length=3, max_length=200)
    portfolio_id: int | None = None
    backtest_id: int | None = None
    experiment_ids: list[int] = Field(default_factory=list, max_length=10)
    stress_test_ids: list[int] = Field(default_factory=list, max_length=10)
    notes: str | None = Field(default=None, max_length=4000)


class NotesUpdate(_Base):
    notes: str | None = Field(default=None, max_length=4000)


class MarkReadRequest(_Base):
    ids: list[int] | None = None
