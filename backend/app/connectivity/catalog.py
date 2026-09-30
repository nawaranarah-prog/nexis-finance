"""Integration marketplace: implemented providers plus clearly-marked planned ones."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.connectivity.base import FinancialConnectionProvider, ProviderSpec
from app.connectivity.providers.alpaca import AlpacaProvider
from app.connectivity.providers.economic import FredProvider, USTreasuryProvider, WorldBankProvider
from app.connectivity.providers.sec import SecEdgarProvider
from app.core.errors import ConfigurationError, ProviderError
from app.data.providers import PublicMarketDataProvider


class YahooMarketConnection(FinancialConnectionProvider):
    spec = ProviderSpec(
        key="yahoo_market",
        name="Public Market Data (Yahoo Finance chart endpoint)",
        category="markets",
        description="Daily OHLCV and adjusted closes for stocks, ETFs, indices and FX pairs. Used to price imported holdings.",
        capabilities=("market_data",),
        auth_type="none",
        requirements=("Internet access", "NEXIS_PUBLIC_PROVIDER_ENABLED=true"),
        docs_url="https://finance.yahoo.com",
        implemented=True,
        verification="live",
        data_class="real_external",
        config_fields=(
            {"name": "symbols", "label": "Extra symbols (comma-separated)", "type": "str", "default": "SPY"},
            {"name": "start", "label": "History start", "type": "date", "default": "2019-01-01"},
        ),
        notes="Unofficial endpoint without an SLA. Symbols held in imported accounts are added automatically on sync.",
    )

    def verify(self) -> dict[str, Any]:
        df = PublicMarketDataProvider(timeout=self.timeout, client=self._client).fetch(["SPY"], date.today().replace(day=1), None)
        if df.empty:
            raise ProviderError("Yahoo Finance chart endpoint returned no rows for SPY")
        return {"latest_bar": str(df["date"].max().date())}


class SyntheticMarketConnection(FinancialConnectionProvider):
    spec = ProviderSpec(
        key="synthetic_market",
        name="Synthetic Research Universe",
        category="markets",
        description="Seeded, fully documented synthetic multi-sector universe for offline research (DEMO / SYNTHETIC DATA MODE).",
        capabilities=("market_data",),
        auth_type="none",
        requirements=(),
        docs_url="/market-data",
        implemented=True,
        verification="live",
        data_class="synthetic",
        notes="Clearly labelled synthetic data; never presented as real market history.",
    )

    def verify(self) -> dict[str, Any]:
        return {"generator": "deterministic"}


class FileImportConnection(FinancialConnectionProvider):
    spec = ProviderSpec(
        key="file_import",
        name="File Import (CSV / JSON / XLSX)",
        category="files",
        description="Import holdings, transactions or market data from brokerage statements and spreadsheets, with automatic column detection and manual mapping.",
        capabilities=("holdings", "transactions", "market_data"),
        auth_type="file",
        requirements=("A CSV, JSON or XLSX export",),
        docs_url="/connections",
        implemented=True,
        verification="live",
        data_class="user_imported",
        notes="Every imported row is kept verbatim for lineage. Re-importing the same file is detected by content hash.",
    )

    def verify(self) -> dict[str, Any]:
        return {"formats": ["csv", "json", "xlsx"]}


PROVIDERS: dict[str, type[FinancialConnectionProvider]] = {
    cls.spec.key: cls
    for cls in (
        YahooMarketConnection,
        SyntheticMarketConnection,
        USTreasuryProvider,
        WorldBankProvider,
        FredProvider,
        SecEdgarProvider,
        AlpacaProvider,
        FileImportConnection,
    )
}


def _planned(
    key: str, name: str, category: str, description: str, caps: tuple[str, ...], auth: str, docs: str, why: str
) -> ProviderSpec:
    return ProviderSpec(
        key=key,
        name=name,
        category=category,
        description=description,
        capabilities=caps,
        auth_type=auth,
        requirements=(why,),
        docs_url=docs,
        implemented=False,
        verification="n/a",
        data_class="real_external",
    )


PLANNED: list[ProviderSpec] = [
    _planned(
        "plaid_investments",
        "Plaid Investments",
        "accounts",
        "Aggregated holdings and investment transactions across thousands of institutions.",
        ("accounts", "holdings", "transactions"),
        "oauth",
        "https://plaid.com/docs/investments/",
        "Requires a Plaid client ID/secret and production approval",
    ),
    _planned(
        "snaptrade",
        "SnapTrade",
        "accounts",
        "OAuth-based connections to retail brokerages (read-only holdings and activity).",
        ("accounts", "holdings", "transactions"),
        "oauth",
        "https://docs.snaptrade.com",
        "Requires a SnapTrade partner key",
    ),
    _planned(
        "ibkr",
        "Interactive Brokers Client Portal",
        "accounts",
        "Positions and trades via the IBKR Client Portal Web API.",
        ("accounts", "holdings", "transactions"),
        "oauth",
        "https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/",
        "Requires the local Client Portal gateway session",
    ),
    _planned(
        "schwab",
        "Charles Schwab Trader API",
        "accounts",
        "Accounts, positions and transactions via Schwab's OAuth 2.0 developer API.",
        ("accounts", "holdings", "transactions"),
        "oauth",
        "https://developer.schwab.com",
        "Requires an approved Schwab developer app",
    ),
    _planned(
        "polygon",
        "Polygon.io",
        "markets",
        "Licensed equities, options and FX market data with SLAs.",
        ("market_data",),
        "api_key",
        "https://polygon.io/docs",
        "Requires a paid API key",
    ),
]


def catalog() -> list[dict[str, Any]]:
    items = [cls.spec.public() for cls in PROVIDERS.values()]
    items += [s.public() for s in PLANNED]
    return items


def get_provider_class(key: str) -> type[FinancialConnectionProvider]:
    cls = PROVIDERS.get(key)
    if cls is None:
        if any(p.key == key for p in PLANNED):
            raise ConfigurationError(f"'{key}' is listed as coming soon and is not implemented")
        raise ConfigurationError(f"unknown provider '{key}'")
    return cls
