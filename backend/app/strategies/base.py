"""Strategy interface.

A strategy is called by the backtest engine on each *signal date* with a :class:`MarketHistory`
containing data **up to and including that date's close and nothing later**. It returns target
portfolio weights (or ``None`` to keep current holdings). The engine — not the strategy — decides
when and at what price those targets are executed (next open or next close), so a strategy
cannot trade at the price it used to form its signal.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd

from app.core.errors import ConfigurationError


@dataclass(frozen=True)
class Param:
    name: str
    type: type
    default: Any
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] | None = None

    def validate(self, value: Any) -> Any:
        if value is None:
            return self.default
        if self.type is bool:
            if not isinstance(value, bool):
                raise ConfigurationError(f"parameter '{self.name}' must be true or false")
            return value
        if self.type in (int, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ConfigurationError(f"parameter '{self.name}' must be a number")
            if self.type is int:
                if float(value) != int(value):
                    raise ConfigurationError(f"parameter '{self.name}' must be an integer")
                value = int(value)
            else:
                value = float(value)
            if self.minimum is not None and value < self.minimum:
                raise ConfigurationError(f"parameter '{self.name}' must be >= {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ConfigurationError(f"parameter '{self.name}' must be <= {self.maximum}")
            return value
        if self.choices is not None and value not in self.choices:
            raise ConfigurationError(f"parameter '{self.name}' must be one of {list(self.choices)}")
        return value

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type.__name__,
            "default": self.default,
            "description": self.description,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices) if self.choices else None,
        }


class MarketHistory:
    """Read-only view of market data truncated at the current signal date."""

    def __init__(self, close: pd.DataFrame, open_: pd.DataFrame, volume: pd.DataFrame) -> None:
        self.close = close
        self.open = open_
        self.volume = volume

    @property
    def date(self) -> pd.Timestamp:
        return self.close.index[-1]

    def __len__(self) -> int:
        return len(self.close)


class Strategy(ABC):
    key: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    PARAMS: ClassVar[tuple[Param, ...]]

    def __init__(self, **params: Any) -> None:
        unknown = set(params) - {p.name for p in self.PARAMS}
        if unknown:
            raise ConfigurationError(f"unknown parameter(s) for {self.key}: {sorted(unknown)}")
        self.params: dict[str, Any] = {p.name: p.validate(params.get(p.name)) for p in self.PARAMS}
        self.validate_params()
        self.signal_log: list[dict[str, Any]] = []
        self.reset()

    def validate_params(self) -> None:  # noqa: B027 - optional hook, intentionally empty
        """Cross-parameter validation hook."""

    def reset(self) -> None:
        """Clear internal state before a new run."""
        self.signal_log = []

    @property
    @abstractmethod
    def warmup(self) -> int:
        """Number of historical bars (including the current one) required to form a signal."""

    @property
    def rebalance_frequency(self) -> str:
        return str(self.params.get("rebalance", "monthly"))

    @property
    def allows_short(self) -> bool:
        return False

    @abstractmethod
    def target_weights(self, history: MarketHistory) -> pd.Series | None:
        """Target weights by symbol (missing symbols = 0). Return None to keep current holdings."""

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {"key": cls.key, "name": cls.name, "description": cls.description, "params": [p.schema() for p in cls.PARAMS]}

    def config(self) -> dict[str, Any]:
        return {"strategy": self.key, "params": dict(self.params)}
