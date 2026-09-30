"""Client for Yahoo Finance's public (unofficial) JSON endpoints.

Covers equities, ETFs, indices, currencies, crypto and futures on most exchanges, including the
Dubai Financial Market (``.AE``). The endpoints have no SLA and may change; every failure becomes
a :class:`ProviderError` so callers can degrade gracefully. ``quoteSummary`` and the fundamentals
time series need a session cookie plus "crumb", which is obtained once and reused.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.core.errors import ProviderError

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
Q1 = "https://query1.finance.yahoo.com"
Q2 = "https://query2.finance.yahoo.com"

# Chart API interval names and the longest history Yahoo serves for each.
INTERVALS = {"1h": ("60m", 729), "1d": ("1d", None), "1wk": ("1wk", None), "1mo": ("1mo", None)}

SUMMARY_MODULES = (
    "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile,recommendationTrend,"
    "calendarEvents,fundProfile,topHoldings"
)
TIMESERIES_TYPES = (
    "annualTotalRevenue,annualGrossProfit,annualEBITDA,annualOperatingIncome,annualNetIncome,"
    "annualFreeCashFlow,annualOperatingCashFlow,annualCapitalExpenditure,annualTotalDebt,"
    "annualCashAndCashEquivalents,annualStockholdersEquity,annualDilutedEPS,annualDilutedAverageShares"
)


def _raw(v: Any) -> Any:
    """Yahoo wraps numbers as {"raw": 1.2, "fmt": "1.20"}; unwrap, keeping plain values."""
    if isinstance(v, dict):
        return v.get("raw")
    return v


class YahooClient:
    _lock = threading.Lock()
    _session: tuple[httpx.Cookies, str, float] | None = None  # cookies, crumb, obtained_at

    def __init__(self, timeout: float = 15.0, client: httpx.Client | None = None) -> None:
        self.timeout = timeout
        self._client = client

    # ------------------------------------------------------------------ transport
    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=self.timeout, headers={"User-Agent": UA}, follow_redirects=True)

    def _get(self, url: str, params: dict[str, Any] | None = None, cookies: httpx.Cookies | None = None) -> Any:
        c = self._http()
        try:
            r = c.get(url, params=params, cookies=cookies)
        except httpx.HTTPError as exc:
            raise ProviderError(f"market data request failed ({exc.__class__.__name__})") from exc
        finally:
            if self._client is None:
                c.close()
        if r.status_code == 404:
            raise ProviderError("instrument not found at the market data provider")
        if r.status_code != 200:
            raise ProviderError(f"market data provider returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise ProviderError("unexpected response from the market data provider") from exc

    def _crumb(self, refresh: bool = False) -> tuple[httpx.Cookies, str]:
        with self._lock:
            s = YahooClient._session
            if s and not refresh and time.time() - s[2] < 6 * 3600:
                return s[0], s[1]
            c = self._http()
            try:
                c.get("https://fc.yahoo.com", headers={"User-Agent": UA})  # sets the session cookie (404 is normal)
                r = c.get(f"{Q2}/v1/test/getcrumb", headers={"User-Agent": UA})
                cookies = httpx.Cookies(c.cookies)
            except httpx.HTTPError as exc:
                raise ProviderError(f"could not open a market data session ({exc.__class__.__name__})") from exc
            finally:
                if self._client is None:
                    c.close()
            crumb = r.text.strip()
            if r.status_code != 200 or not crumb or "<" in crumb:
                raise ProviderError("market data provider refused a session (fundamentals unavailable right now)")
            YahooClient._session = (cookies, crumb, time.time())
            return cookies, crumb

    def _authed(self, url: str, params: dict[str, Any]) -> Any:
        for attempt in (0, 1):
            cookies, crumb = self._crumb(refresh=attempt == 1)
            try:
                return self._get(url, {**params, "crumb": crumb}, cookies=cookies)
            except ProviderError as exc:
                if attempt == 1 or "HTTP 401" not in exc.message:
                    raise
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ endpoints
    def search(self, q: str, quotes: int = 10, news: int = 0) -> dict[str, Any]:
        d = self._get(f"{Q1}/v1/finance/search", {"q": q, "quotesCount": quotes, "newsCount": news, "enableFuzzyQuery": "true"})
        out = []
        for x in d.get("quotes", []):
            if not x.get("symbol") or not x.get("isYahooFinance", True):
                continue
            out.append(
                {
                    "symbol": x["symbol"],
                    "name": x.get("longname") or x.get("shortname") or x["symbol"],
                    "exchange": x.get("exchDisp") or x.get("exchange"),
                    "type": (x.get("typeDisp") or x.get("quoteType") or "").lower(),
                    "sector": x.get("sector"),
                    "industry": x.get("industry"),
                }
            )
        items = [
            {
                "title": n.get("title"),
                "publisher": n.get("publisher"),
                "url": n.get("link"),
                "published_at": datetime.fromtimestamp(n["providerPublishTime"], UTC).isoformat()
                if n.get("providerPublishTime")
                else None,
                "related": n.get("relatedTickers") or [],
            }
            for n in d.get("news", [])
            if n.get("title") and n.get("link")
        ]
        return {"quotes": out, "news": items}

    def chart(
        self, symbol: str, interval: str = "1d", start: date | None = None, end: date | None = None, range_: str | None = None
    ) -> dict[str, Any]:
        """OHLCV bars plus the instrument's quote metadata. Timestamps are ISO strings in UTC."""
        if interval not in INTERVALS:
            raise ProviderError(f"unsupported interval {interval}")
        yi, _ = INTERVALS[interval]
        params: dict[str, Any] = {"interval": yi, "events": "div,splits", "includeAdjustedClose": "true"}
        if range_:
            params["range"] = range_
        else:
            p1 = datetime.combine(start or date(1990, 1, 1), datetime.min.time(), UTC)
            p2 = datetime.combine(end or date.today(), datetime.max.time(), UTC)
            params.update(period1=int(p1.timestamp()), period2=int(p2.timestamp()))
        d = self._get(f"{Q1}/v8/finance/chart/{symbol}", params)
        try:
            res = d["chart"]["result"][0]
        except (KeyError, IndexError, TypeError) as exc:
            err = (d.get("chart") or {}).get("error") or {}
            raise ProviderError(err.get("description") or f"no data for {symbol}") from exc
        ts = res.get("timestamp") or []
        q = (res.get("indicators", {}).get("quote") or [{}])[0]
        adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
        bars = []
        for i, t in enumerate(ts):
            close = (q.get("close") or [None] * len(ts))[i]
            if close is None:
                continue
            bars.append(
                {
                    "t": datetime.fromtimestamp(t, UTC).isoformat(),
                    "open": (q.get("open") or [None] * len(ts))[i],
                    "high": (q.get("high") or [None] * len(ts))[i],
                    "low": (q.get("low") or [None] * len(ts))[i],
                    "close": close,
                    "adj_close": adj[i] if adj and adj[i] is not None else close,
                    "volume": (q.get("volume") or [None] * len(ts))[i],
                }
            )
        return {"meta": res.get("meta", {}), "bars": bars}

    def quote_summary(self, symbol: str, modules: str = SUMMARY_MODULES) -> dict[str, Any]:
        d = self._authed(f"{Q2}/v10/finance/quoteSummary/{symbol}", {"modules": modules})
        res = (d.get("quoteSummary") or {}).get("result") or []
        if not res:
            raise ProviderError(f"no details available for {symbol}")
        return res[0]

    def timeseries(self, symbol: str, types: str = TIMESERIES_TYPES) -> dict[str, list[tuple[str, float]]]:
        now = int(time.time())
        d = self._authed(
            f"{Q2}/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}",
            {"type": types, "period1": now - 12 * 365 * 86400, "period2": now},
        )
        out: dict[str, list[tuple[str, float]]] = {}
        for r in (d.get("timeseries") or {}).get("result") or []:
            t = (r.get("meta", {}).get("type") or [None])[0]
            vals = [(v["asOfDate"], _raw(v.get("reportedValue"))) for v in (r.get(t) or []) if v and v.get("reportedValue")]
            if t and vals:
                out[t] = sorted(vals)
        return out

    def quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Snapshot quotes for up to ~50 symbols in one request."""
        d = self._authed(f"{Q2}/v7/finance/quote", {"symbols": ",".join(symbols)})
        out = []
        for q in (d.get("quoteResponse") or {}).get("result") or []:
            out.append(
                {
                    "symbol": q["symbol"],
                    "name": q.get("longName") or q.get("shortName") or q["symbol"],
                    "type": (q.get("quoteType") or "").lower(),
                    "exchange": q.get("fullExchangeName") or q.get("exchange"),
                    "currency": q.get("currency"),
                    "price": q.get("regularMarketPrice"),
                    "change": q.get("regularMarketChange"),
                    "change_pct": (q["regularMarketChangePercent"] / 100)
                    if q.get("regularMarketChangePercent") is not None
                    else None,
                    "market_cap": q.get("marketCap"),
                    "volume": q.get("regularMarketVolume"),
                    "market_time": datetime.fromtimestamp(q["regularMarketTime"], UTC).isoformat()
                    if q.get("regularMarketTime")
                    else None,
                    "market_state": q.get("marketState"),
                }
            )
        return out

    def peers(self, symbol: str) -> list[str]:
        d = self._get(f"{Q2}/v6/finance/recommendationsbysymbol/{symbol}")
        res = (d.get("finance") or {}).get("result") or []
        return [x["symbol"] for x in (res[0].get("recommendedSymbols") if res else []) or []]


__all__ = ["INTERVALS", "YahooClient", "_raw"]
