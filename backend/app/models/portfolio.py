"""Portfolio configuration, computed returns, risk snapshots and stress tests."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow


class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    benchmark_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False, default=1_000_000.0)
    rebalance_frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    allocation_method: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    constraints: Mapped[dict | None] = mapped_column(JSON)
    # Optimisation metadata: estimation window, method diagnostics.
    allocation_details: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    positions: Mapped[list[PortfolioPosition]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan", order_by="PortfolioPosition.symbol"
    )


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"
    __table_args__ = (UniqueConstraint("portfolio_id", "symbol", name="uq_positions_portfolio_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")


class PortfolioReturn(Base):
    """Materialised daily return series of a portfolio (refreshed when analytics are computed)."""

    __tablename__ = "portfolio_returns"
    __table_args__ = (UniqueConstraint("portfolio_id", "date", name="uq_portfolio_returns_portfolio_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    daily_return: Mapped[float] = mapped_column(Float, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)


class RiskMetric(Base, TimestampMixin):
    """A persisted VaR / CVaR estimate with its methodology parameters."""

    __tablename__ = "risk_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lookback_days: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    var: Mapped[float] = mapped_column(Float, nullable=False)
    cvar: Mapped[float | None] = mapped_column(Float)
    var_amount: Mapped[float | None] = mapped_column(Float)
    cvar_amount: Mapped[float | None] = mapped_column(Float)


class StressTest(Base, TimestampMixin):
    __tablename__ = "stress_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    portfolio_impact_pct: Mapped[float | None] = mapped_column(Float)
    results: Mapped[dict] = mapped_column(JSON, nullable=False)
