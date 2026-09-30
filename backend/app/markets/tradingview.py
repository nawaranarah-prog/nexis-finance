"""TradingView public data: the UAE stock screener, symbol search and price history.

Used for what Yahoo does not carry — Abu Dhabi (ADX) listings, and UAE government bonds and sukuk
listed on Nasdaq Dubai and elsewhere — and as a fallback for Dubai (DFM) listings. These are the
public endpoints behind TradingView's own screener and charts (delayed data, no SLA); failures
raise :class:`ProviderError`.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import string
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.errors import ProviderError

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
SCAN = "https://scanner.tradingview.com/{market}/scan"
SEARCH = "https://symbol-search.tradingview.com/symbol_search/v3/"
WS = "wss://data.tradingview.com/socket.io/websocket?type=chart"
# Chart resolutions for the app's intervals.
RESOLUTION = {"1h": "60", "1d": "1D", "1wk": "1W", "1mo": "1M"}

STOCK_COLUMNS = [
    "name", "description", "exchange", "close", "change", "change_abs", "volume", "average_volume_30d_calc",
    "market_cap_basic", "price_earnings_ttm", "price_book_fq", "price_sales_current", "enterprise_value_ebitda_ttm",
    "dividends_yield_current", "earnings_per_share_basic_ttm", "total_revenue", "net_income", "free_cash_flow", "ebitda",
    "total_debt", "cash_n_short_term_invest_fq", "total_shares_outstanding", "beta_1_year", "price_52_week_high",
    "price_52_week_low", "SMA50", "SMA200", "gross_margin", "operating_margin", "net_margin", "return_on_equity",
    "debt_to_equity", "recommendation_mark", "recommendation_buy", "recommendation_hold", "recommendation_sell",
    "recommendation_total", "price_target_average", "price_target_high", "price_target_low", "sector", "industry",
    "Perf.W", "Perf.1M", "Perf.3M", "Perf.YTD", "Perf.Y", "currency", "type", "open", "high", "low",
]  # fmt: skip
BOND_COLUMNS = ["name", "description", "exchange", "close", "change", "maturity_date", "yield_to_maturity", "currency"]


def _post(url: str, body: dict[str, Any], timeout: float = 15.0) -> Any:
    try:
        r = httpx.post(url, json=body, headers={"User-Agent": UA, "Origin": "https://www.tradingview.com"}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ProviderError(f"UAE market data request failed ({exc.__class__.__name__})") from exc
    if r.status_code != 200:
        raise ProviderError(f"UAE market data provider returned HTTP {r.status_code}")
    return r.json()


def scan(market: str, columns: list[str], tickers: list[str] | None = None, limit: int = 600) -> list[dict[str, Any]]:
    body: dict[str, Any] = {"columns": columns, "range": [0, limit], "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}}
    if tickers:
        body = {"symbols": {"tickers": tickers}, "columns": columns}
    elif market == "uae":
        body["filter"] = [{"left": "type", "operation": "in_range", "right": ["stock", "dr", "fund"]}]
    data = _post(SCAN.format(market=market), body)
    return [{"tv": row["s"], **dict(zip(columns, row["d"], strict=False))} for row in data.get("data") or []]


def search_bonds(text: str, limit: int = 15) -> list[dict[str, Any]]:
    try:
        r = httpx.get(
            SEARCH,
            params={"text": text, "search_type": "bond", "hl": "0", "lang": "en", "domain": "production"},
            headers={"User-Agent": UA, "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"},
            timeout=12,
        )
        items = r.json().get("symbols", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError("bond search failed") from exc
    out = []
    for s in items[:limit]:
        desc = re.sub(r"<[^>]+>", "", s.get("description") or "")
        out.append(
            {
                "tv": f"{s.get('exchange')}:{re.sub(r'<[^>]+>', '', s.get('symbol') or '')}",
                "description": desc,
                "exchange": s.get("exchange"),
            }
        )
    return out


# ---------------------------------------------------------------------- history (chart websocket)


def _frame(fn: str, params: list[Any]) -> str:
    m = json.dumps({"m": fn, "p": params}, separators=(",", ":"))
    return f"~m~{len(m)}~m~{m}"


def _messages(raw: str) -> list[Any]:
    out: list[Any] = []
    for part in re.split(r"~m~\d+~m~", raw):
        if part.startswith("{"):
            try:
                out.append(json.loads(part))
            except ValueError:
                continue
        elif part.startswith("~h~"):
            out.append(part)
    return out


async def _history(tv_symbol: str, resolution: str, bars: int, timeout: float) -> dict[str, Any]:
    import websockets

    cs = "cs_" + "".join(random.choices(string.ascii_lowercase, k=12))
    deadline = time.monotonic() + timeout
    async with websockets.connect(
        WS, origin="https://www.tradingview.com", additional_headers={"User-Agent": UA}, open_timeout=10
    ) as ws:
        await ws.send(_frame("set_auth_token", ["unauthorized_user_token"]))
        await ws.send(_frame("chart_create_session", [cs, ""]))
        await ws.send(
            _frame(
                "resolve_symbol",
                [cs, "sym_1", "=" + json.dumps({"symbol": tv_symbol, "adjustment": "dividends"}, separators=(",", ":"))],
            )
        )
        await ws.send(_frame("create_series", [cs, "s1", "s1", "sym_1", resolution, bars, ""]))
        info: dict[str, Any] = {}
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), max(0.5, deadline - time.monotonic()))
            for msg in _messages(raw if isinstance(raw, str) else raw.decode()):
                if isinstance(msg, str):  # heartbeat: echo it back
                    await ws.send(f"~m~{len(msg)}~m~{msg}")
                    continue
                kind = msg.get("m")
                if kind == "symbol_resolved":
                    info = msg["p"][2]
                elif kind == "timescale_update":
                    series = ((msg["p"][1] or {}).get("s1") or {}).get("s") or []
                    return {"info": info, "bars": [x["v"] for x in series]}
                elif kind in ("symbol_error", "critical_error", "protocol_error", "series_error"):
                    raise ProviderError(f"no history for {tv_symbol}")
    raise ProviderError(f"history request for {tv_symbol} timed out")


def history(tv_symbol: str, interval: str, bars: int, timeout: float = 20.0) -> dict[str, Any]:
    """Bars as ``[{t, open, high, low, close, volume}]`` (UTC ISO timestamps), oldest first."""
    if interval not in RESOLUTION:
        raise ProviderError(f"unsupported interval {interval}")
    try:
        res = asyncio.run(_history(tv_symbol, RESOLUTION[interval], max(10, min(bars, 5000)), timeout))
    except ProviderError:
        raise
    except Exception as exc:  # network errors, closed sockets, event-loop issues
        raise ProviderError(f"history request for {tv_symbol} failed ({exc.__class__.__name__})") from exc
    out = []
    for v in res["bars"]:
        if len(v) < 5 or v[4] is None:
            continue
        out.append({
            "t": datetime.fromtimestamp(v[0], UTC).isoformat(),
            "open": v[1], "high": v[2], "low": v[3], "close": v[4], "adj_close": v[4],
            "volume": v[5] if len(v) > 5 and v[5] is not None and v[5] < 1e99 else None,
        })  # fmt: skip
    return {"info": res["info"], "bars": out}
