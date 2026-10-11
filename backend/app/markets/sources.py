"""Every external market-data and news source Nexis reads, behind one interface.

The app asks this module for "the price source", "the UAE exchange source" or "the news source" and never imports a
provider directly. That keeps each provider replaceable — in particular the two **unofficial** ones (Yahoo Finance and
TradingView, see AUDIT.md §2 and DATA-SOURCES.md) can be swapped for a licensed feed by adding an adapter here and
setting ``NEXIS_PRICE_SOURCE`` / ``NEXIS_UAE_SOURCE``, without touching the rest of the code.

The providers themselves are unchanged (the brief: leave existing endpoints as they are, put each behind the provider
interface). ``REGISTRY`` describes every source for the System page and ``DATA-SOURCES.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import ModuleType
from typing import Any, Protocol

from app.core.config import get_settings


class PriceSource(Protocol):
    """Quotes, history, fundamentals, search and peers for listed instruments."""

    def search(self, q: str, quotes: int = 10, news: int = 0) -> dict[str, Any]: ...
    def quotes(self, symbols: list[str]) -> list[dict[str, Any]]: ...
    def chart(self, symbol: str, interval: str, start: Any, end: Any) -> dict[str, Any]: ...
    def quote_summary(self, symbol: str, modules: str = ...) -> dict[str, Any]: ...
    def timeseries(self, symbol: str, types: str = ...) -> dict[str, list[tuple[str, float]]]: ...
    def peers(self, symbol: str) -> list[str]: ...


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    role: str
    official: bool  # an official, documented API/feed for this use
    licence: str
    terms_risk: str  # low | medium | high
    code: str
    notes: str = ""


REGISTRY: tuple[Source, ...] = (
    Source("yahoo", "Yahoo Finance (query1/query2 endpoints)", "prices", False, "No public API licence", "high", "app/markets/yahoo.py",
           "Main price, fundamentals and analyst source. Unofficial; to be replaced by a licensed feed (owner decision)."),
    Source("tradingview", "TradingView (scanner and chart endpoints)", "uae_exchange", False, "Widget use only without a licence", "high",
           "app/markets/tradingview.py", "ADX shares, UAE bonds and sukuk, UAE indices. Unofficial."),
    Source("publisher_rss", "Publisher RSS feeds", "news", True, "Published for reading; republishing limits vary by publisher", "medium",
           "app/markets/feeds.py", "Headline, short excerpt and link only; images are linked, never stored."),
    Source("google_news", "Google News RSS search", "news", False, "Personal, non-commercial use", "medium", "app/markets/news.py",
           "Regional headlines per company: headline, source and link only."),
    Source("youtube", "YouTube channel feeds and official embed", "news", True, "YouTube API Services / embed terms", "low",
           "app/markets/feeds.py"),
    Source("hacker_news", "Hacker News (Algolia API)", "discussions", True, "Public API", "low", "app/services/public_discussions.py"),
    Source("stocktwits", "StockTwits public streams", "discussions", False, "Commercial use restricted", "medium",
           "app/services/public_discussions.py"),
    Source("wikidata", "Wikidata (API and Query Service)", "relationships", True, "CC0", "low", "app/connectivity/providers/wikidata.py"),
    Source("sec_edgar", "SEC EDGAR", "filings", True, "Public data; fair-access policy", "low", "app/connectivity/providers/sec.py"),
    Source("us_treasury", "US Treasury Fiscal Data", "macro", True, "Public domain", "low", "app/connectivity/providers/economic.py"),
    Source("world_bank", "World Bank Indicators API", "macro", True, "CC BY 4.0", "low", "app/connectivity/providers/economic.py"),
    Source("fred", "FRED (St. Louis Fed)", "macro", True, "Free with an API key; series-level copyright varies", "low",
           "app/connectivity/providers/economic.py", "Needs NEXIS_FRED_API_KEY."),
)  # fmt: skip


def price_source(timeout: float = 15.0) -> PriceSource:
    """The configured price source (today only "yahoo" exists)."""
    name = (get_settings().price_source or "yahoo").lower()
    if name == "yahoo":
        from app.markets.yahoo import YahooClient

        return YahooClient(timeout=timeout)
    raise ValueError(f"unknown price source {name!r}")


def uae_exchange() -> ModuleType:
    """The configured source for ADX shares, UAE bonds and UAE indices (today only "tradingview" exists)."""
    name = (get_settings().uae_source or "tradingview").lower()
    if name == "tradingview":
        from app.markets import tradingview

        return tradingview
    raise ValueError(f"unknown UAE source {name!r}")


def describe() -> list[dict[str, Any]]:
    active = {"prices": get_settings().price_source or "yahoo", "uae_exchange": get_settings().uae_source or "tradingview"}
    return [{**asdict(s), "active": s.key == active.get(s.role, s.key)} for s in REGISTRY]
