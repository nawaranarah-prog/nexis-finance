"""Research registry: strategies, backtests, experiments, ML outputs, anomalies."""

from __future__ import annotations

from datetime import date

from sqlalchemy import JSON, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Strategy(Base, TimestampMixin):
    """Catalogue of implemented strategy classes (seeded from the code registry)."""

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    default_params: Mapped[dict] = mapped_column(JSON, nullable=False)


class Experiment(Base, TimestampMixin):
    """Umbrella registry entry for every reproducible research run."""

    __tablename__ = "experiments"
    __table_args__ = (Index("ix_experiments_type_created", "experiment_type", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    experiment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset_hash: Mapped[str | None] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(120))
    features: Mapped[list | None] = mapped_column(JSON)
    train_start: Mapped[date | None] = mapped_column(Date)
    train_end: Mapped[date | None] = mapped_column(Date)
    test_start: Mapped[date | None] = mapped_column(Date)
    test_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id", ondelete="SET NULL"))
    reproducibility: Mapped[dict | None] = mapped_column(JSON)
    summary: Mapped[dict | None] = mapped_column(JSON)
    # Chart-ready diagnostics (series, matrices). Kept as JSON to avoid a table per chart.
    artifacts: Mapped[dict | None] = mapped_column(JSON)

    metrics: Mapped[list[ExperimentMetric]] = relationship(back_populates="experiment", cascade="all, delete-orphan")


class ExperimentMetric(Base):
    """Normalised metric rows so experiments can be compared with plain SQL."""

    __tablename__ = "experiment_metrics"
    __table_args__ = (Index("ix_experiment_metrics_exp", "experiment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False)  # full | train | validation | test | fold_1 ...
    model: Mapped[str | None] = mapped_column(String(120))
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)

    experiment: Mapped[Experiment] = relationship(back_populates="metrics")


class Backtest(Base, TimestampMixin):
    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    strategy_key: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    benchmark_symbol: Mapped[str | None] = mapped_column(String(32))
    execution: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    series: Mapped[dict] = mapped_column(JSON, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    trades: Mapped[list[BacktestTrade]] = relationship(cascade="all, delete-orphan", order_by="BacktestTrade.id")


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    __table_args__ = (Index("ix_backtest_trades_bt_date", "backtest_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_id: Mapped[int] = mapped_column(ForeignKey("backtests.id", ondelete="CASCADE"), nullable=False)
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    notional: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, nullable=False)
    slippage: Mapped[float] = mapped_column(Float, nullable=False)
    weight_before: Mapped[float] = mapped_column(Float, nullable=False)
    weight_after: Mapped[float] = mapped_column(Float, nullable=False)


class MLPrediction(Base):
    __tablename__ = "ml_predictions"
    __table_args__ = (Index("ix_ml_predictions_exp_model", "experiment_id", "model"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    split: Mapped[str] = mapped_column(String(16), nullable=False)
    y_true: Mapped[float | None] = mapped_column(Float)
    y_pred: Mapped[float] = mapped_column(Float, nullable=False)


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (Index("ix_anomalies_exp_score", "experiment_id", "score"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # market_behaviour | data_quality
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    top_feature: Mapped[str | None] = mapped_column(String(64))
    features: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Only populated for synthetic datasets, where injected events are known.
    matches_injected_event: Mapped[bool | None] = mapped_column(Boolean)
