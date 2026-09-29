"""Market-data provider abstraction.

``MarketDataProvider``
├── ``SyntheticMarketDataProvider``  deterministic generator, always available (DEMO mode)
├── ``PublicMarketDataProvider``     Yahoo Finance public chart endpoint (unofficial, no key)
└── ``CSVMarketDataProvider``        user-supplied CSV files

Providers only *fetch and normalise*; validation, deduplication and persistence are the
responsibility of the ingestion service so that every source goes through the same checks.
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pandas as pd

from app.core.errors import ConfigurationError, ProviderError
from app.data.synthetic import SyntheticConfig, generate_universe

CANONICAL_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]

COLUMN_ALIASES = {
    "ticker": "symbol",
    "sym": "symbol",
    "timestamp": "date",
    "datetime": "date",
    "day": "date",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "price": "close",
    "adjclose": "adj_close",
    "adj close": "adj_close",
    "adjusted_close": "adj_close",
    "adj_close_price": "adj_close",
    "vol": "volume",
    "v": "volume",
}


@dataclass
class AssetInfo:
    symbol: str
    name: str
    asset_type: str = "equity"
    sector: str | None = None
    is_benchmark: bool = False
    currency: str = "USD"
    attributes: dict[str, Any] = field(default_factory=dict)


class MarketDataProvider(ABC):
    name: str = "abstract"
    is_synthetic: bool = False

    @abstractmethod
    def list_assets(self, symbols: list[str] | None = None) -> list[AssetInfo]: ...

    @abstractmethod
    def fetch(self, symbols: list[str], start: date | None, end: date | None) -> pd.DataFrame:
        """Return bars in the canonical schema (may contain defects; validation happens later)."""

    def metadata(self) -> dict[str, Any]:
        return {"provider": self.name, "synthetic": self.is_synthetic}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [COLUMN_ALIASES.get(str(c).strip().lower(), str(c).strip().lower().replace(" ", "_")) for c in out.columns]
    for c in CANONICAL_COLUMNS:
        if c not in out.columns:
            out[c] = pd.NA
    return out[CANONICAL_COLUMNS]


class SyntheticMarketDataProvider(MarketDataProvider):
    name = "synthetic"
    is_synthetic = True

    def __init__(self, config: SyntheticConfig | None = None) -> None:
        self.config = config or SyntheticConfig()
        self._universe = generate_universe(self.config)

    @property
    def manifest(self) -> dict[str, Any]:
        return self._universe.manifest

    def list_assets(self, symbols: list[str] | None = None) -> list[AssetInfo]:
        out = [
            AssetInfo(
                **{k: a[k] for k in ("symbol", "name", "asset_type", "sector", "is_benchmark")},
                attributes=a.get("attributes") or {},
            )
            for a in self._universe.assets
        ]
        if symbols:
            wanted = set(symbols)
            out = [a for a in out if a.symbol in wanted]
        return out

    def fetch(self, symbols: list[str], start: date | None, end: date | None) -> pd.DataFrame:
        df = self._universe.bars
        mask = df["symbol"].isin(symbols)
        if start is not None:
            mask &= df["date"] >= pd.Timestamp(start)
        if end is not None:
            mask &= df["date"] <= pd.Timestamp(end)
        return df.loc[mask].copy()

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "synthetic": True,
            "config": self.config.to_dict(),
            "fingerprint": self.config.fingerprint(),
        }


class PublicMarketDataProvider(MarketDataProvider):
    """Daily bars from Yahoo Finance's public chart endpoint.

    This is an *unofficial* endpoint without an SLA; availability, rate limits and terms of use
    are outside this project's control. It is disabled unless ``NEXIS_PUBLIC_PROVIDER_ENABLED``.
    Adjusted close is taken from the endpoint's ``adjclose`` series when present.
    """

    name = "yahoo_chart"
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, timeout: float = 15.0, client: httpx.Client | None = None) -> None:
        self.timeout = timeout
        self._client = client
        self._meta: dict[str, dict[str, Any]] = {}

    def _get(self, symbol: str, start: date | None, end: date | None) -> dict[str, Any]:
        p1 = int(datetime.combine(start or date(2000, 1, 1), datetime.min.time(), UTC).timestamp())
        p2 = int(datetime.combine(end or date.today(), datetime.max.time(), UTC).timestamp())
        params = {"period1": p1, "period2": p2, "interval": "1d", "events": "div,splits", "includeAdjustedClose": "true"}
        client = self._client or httpx.Client(timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0 (NexisFinance research)"})
        try:
            resp = client.get(self.BASE_URL.format(symbol=symbol), params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error fetching {symbol}: {exc.__class__.__name__}") from exc
        finally:
            if self._client is None:
                client.close()
        if resp.status_code == 404:
            raise ProviderError(f"symbol '{symbol}' not found at provider")
        if resp.status_code != 200:
            raise ProviderError(f"provider returned HTTP {resp.status_code} for {symbol}")
        payload: Any = None
        try:
            payload = resp.json()
            result = payload["chart"]["result"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            err = payload.get("chart", {}).get("error") if isinstance(payload, dict) else None
            raise ProviderError(f"unexpected provider response for {symbol}", details={"provider_error": err}) from exc
        return result

    def list_assets(self, symbols: list[str] | None = None) -> list[AssetInfo]:
        out = []
        for s in symbols or []:
            meta = self._meta.get(s)
            if meta is None:
                meta = self._get(s, date.today().replace(day=1), None).get("meta", {})
                self._meta[s] = meta
            itype = str(meta.get("instrumentType", "EQUITY")).lower()
            out.append(
                AssetInfo(
                    symbol=s,
                    name=meta.get("longName") or meta.get("shortName") or s,
                    asset_type={"etf": "etf", "index": "index", "equity": "equity"}.get(itype, itype),
                    currency=meta.get("currency") or "USD",
                    attributes={"exchange": meta.get("fullExchangeName"), "source": "Yahoo Finance chart API (unofficial)"},
                )
            )
        return out

    def fetch(self, symbols: list[str], start: date | None, end: date | None) -> pd.DataFrame:
        frames = []
        for s in symbols:
            result = self._get(s, start, end)
            self._meta[s] = result.get("meta", {})
            ts = result.get("timestamp") or []
            if not ts:
                continue
            q = (result.get("indicators", {}).get("quote") or [{}])[0]
            adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
            tz = self._meta[s].get("exchangeTimezoneName") or "UTC"
            dates = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz).tz_localize(None).normalize()
            frames.append(
                pd.DataFrame(
                    {
                        "symbol": s,
                        "date": dates,
                        "open": q.get("open"),
                        "high": q.get("high"),
                        "low": q.get("low"),
                        "close": q.get("close"),
                        "adj_close": adj if adj is not None else q.get("close"),
                        "volume": q.get("volume"),
                    }
                )
            )
        if not frames:
            return pd.DataFrame(columns=CANONICAL_COLUMNS)
        return normalize_columns(pd.concat(frames, ignore_index=True))


class CSVMarketDataProvider(MarketDataProvider):
    """Bars from an uploaded CSV. Expected columns (case-insensitive, common aliases accepted):
    symbol, date, open, high, low, close, [adj_close], [volume]. A single-symbol file without a
    ``symbol`` column may supply ``default_symbol``.
    """

    name = "csv_upload"

    def __init__(self, content: bytes, default_symbol: str | None = None, max_rows: int = 2_000_000) -> None:
        try:
            raw = pd.read_csv(io.BytesIO(content), nrows=max_rows + 1)
        except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
            raise ConfigurationError(f"could not parse CSV: {exc.__class__.__name__}") from exc
        if len(raw) > max_rows:
            raise ConfigurationError(f"CSV exceeds the maximum of {max_rows:,} rows")
        df = normalize_columns(raw)
        if df["symbol"].isna().all():
            if not default_symbol:
                raise ConfigurationError("CSV has no 'symbol' column; provide a default symbol")
            df["symbol"] = default_symbol
        df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
        self._df = df

    def list_assets(self, symbols: list[str] | None = None) -> list[AssetInfo]:
        syms = sorted(self._df["symbol"].dropna().unique())
        return [AssetInfo(symbol=s, name=s, attributes={"source": "CSV upload"}) for s in syms if not symbols or s in symbols]

    def fetch(self, symbols: list[str], start: date | None, end: date | None) -> pd.DataFrame:
        df = self._df[self._df["symbol"].isin(symbols)].copy()
        dt = pd.to_datetime(df["date"], errors="coerce")
        keep = pd.Series(True, index=df.index)
        if start is not None:
            keep &= ~(dt < pd.Timestamp(start))
        if end is not None:
            keep &= ~(dt > pd.Timestamp(end))
        return df[keep]
