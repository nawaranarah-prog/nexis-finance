"""Connection-provider abstraction.

``FinancialConnectionProvider``
├── ``MarketDataConnection``      get_market_data()                    (wraps app.data.providers)
├── ``BrokerageProvider``         get_accounts(), get_holdings(), get_transactions(), get_balances()
├── ``EconomicDataProvider``      list_series(), get_series()
├── ``RegulatoryDataProvider``    get_profile(), get_fundamentals()
└── ``FileImportProvider``        (import is interactive: preview → map → import; see services.imports)

Every provider declares a :class:`ProviderSpec` (marketplace metadata, capabilities, auth type,
credential fields) and implements ``verify()`` — a real call that must succeed before a connection
is marked *connected*. Category mixins add only the methods that make sense for that category.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, ClassVar

import httpx
import pandas as pd

from app.core.errors import ProviderError

CATEGORIES = ("markets", "accounts", "economics", "regulatory", "files")


@dataclass(frozen=True)
class CredentialField:
    name: str
    label: str
    secret: bool = True
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    name: str
    category: str
    description: str
    capabilities: tuple[str, ...]
    auth_type: str  # none | api_key | oauth | file
    requirements: tuple[str, ...]
    docs_url: str
    implemented: bool
    # How the implementation has been verified in this project: "live" (against the real endpoint),
    # "mocked" (against documented response shapes only), or "n/a" (not implemented).
    verification: str
    data_class: str  # real_external | user_imported | synthetic
    credential_fields: tuple[CredentialField, ...] = ()
    config_fields: tuple[dict[str, Any], ...] = ()
    notes: str = ""

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["credential_fields"] = [asdict(c) for c in self.credential_fields]
        return d


class FinancialConnectionProvider(ABC):
    spec: ClassVar[ProviderSpec]

    def __init__(
        self,
        credentials: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.credentials = credentials or {}
        self.config = config or {}
        self._client = client
        self.timeout = timeout

    # --- HTTP helper with uniform error handling (never logs credentials) ------------------
    def http_get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
        client = self._client or httpx.Client(timeout=self.timeout, follow_redirects=True)
        try:
            resp = client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.spec.name}: network error ({exc.__class__.__name__})") from exc
        finally:
            if self._client is None:
                client.close()
        if resp.status_code in (401, 403):
            raise ProviderError(f"{self.spec.name}: authorisation rejected (HTTP {resp.status_code})", details={"auth": True})
        if resp.status_code == 429:
            raise ProviderError(f"{self.spec.name}: rate limited (HTTP 429) — try again later")
        if resp.status_code >= 400:
            raise ProviderError(f"{self.spec.name}: HTTP {resp.status_code}")
        return resp

    @abstractmethod
    def verify(self) -> dict[str, Any]:
        """Perform a lightweight authenticated/real request. Raise ProviderError on failure."""

    def disconnect(self) -> None:  # noqa: B027 - optional hook; default has nothing to revoke
        """Revoke provider-side authorisation where the provider supports it (default: nothing to revoke)."""


class BrokerageProvider(FinancialConnectionProvider):
    @abstractmethod
    def get_accounts(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_holdings(self, account_external_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_transactions(self, account_external_id: str, since: date | None) -> list[dict[str, Any]]: ...

    def get_balances(self, account_external_id: str) -> dict[str, Any]:
        return {}


@dataclass
class SeriesData:
    code: str
    title: str
    units: str | None
    frequency: str | None
    country: str | None
    observations: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["date", "value"]))


class EconomicDataProvider(FinancialConnectionProvider):
    @abstractmethod
    def list_series(self) -> list[str]: ...

    @abstractmethod
    def get_series(self, code: str, start: date | None) -> SeriesData: ...


class RegulatoryDataProvider(FinancialConnectionProvider):
    @abstractmethod
    def get_profile(self, symbol: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_fundamentals(self, cik: int) -> list[dict[str, Any]]: ...
