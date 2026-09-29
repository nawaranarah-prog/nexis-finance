"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from app.models.market import Asset, DataQualityIssue, DataQualityRun, Dataset, IngestionRun, MarketData
from app.models.ops import Job, Notification, Report
from app.models.portfolio import Portfolio, PortfolioPosition, PortfolioReturn, RiskMetric, StressTest
from app.models.research import (
    Anomaly,
    Backtest,
    BacktestTrade,
    Experiment,
    ExperimentMetric,
    MLPrediction,
    Strategy,
)

__all__ = [
    "Anomaly",
    "Asset",
    "Backtest",
    "BacktestTrade",
    "DataQualityIssue",
    "DataQualityRun",
    "Dataset",
    "Experiment",
    "ExperimentMetric",
    "IngestionRun",
    "Job",
    "MLPrediction",
    "MarketData",
    "Notification",
    "Portfolio",
    "PortfolioPosition",
    "PortfolioReturn",
    "Report",
    "RiskMetric",
    "Strategy",
    "StressTest",
]
