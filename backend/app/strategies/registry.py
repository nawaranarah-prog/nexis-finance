"""Strategy registry."""

from __future__ import annotations

from typing import Any

from app.core.errors import ConfigurationError
from app.strategies.base import Strategy
from app.strategies.baseline import EqualWeightStrategy, TrendFilterStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.momentum import MomentumStrategy

STRATEGIES: dict[str, type[Strategy]] = {
    cls.key: cls for cls in (MomentumStrategy, MeanReversionStrategy, TrendFilterStrategy, EqualWeightStrategy)
}


def build_strategy(key: str, params: dict[str, Any] | None = None) -> Strategy:
    cls = STRATEGIES.get(key)
    if cls is None:
        raise ConfigurationError(f"unknown strategy '{key}'", details={"available": list(STRATEGIES)})
    return cls(**(params or {}))


def list_strategies() -> list[dict[str, Any]]:
    return [cls.describe() for cls in STRATEGIES.values()]
