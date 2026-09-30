"""Global markets: search, normalised instrument details, history at any interval, lists, news.

Everything is fetched on demand from the provider and cached in the database (serverless
instances share no memory). When the provider fails, the last cached copy is returned and marked
``stale`` rather than inventing values; with no cache the error is surfaced.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, NexisError, ProviderError
from app.db.base import utcnow
from app.markets import news as news_mod
from app.markets.yahoo import INTERVALS, YahooClient, _raw
from app.models import MarketCache

SOURCE = "Yahoo Finance public endpoints (unofficial, no SLA); news also from Google News"

LISTS: dict[str, dict[str, Any]] = {
    "uae": {
        "label": "UAE · Dubai Financial Market",
        "note": "DFM-listed shares, prices in AED. Abu Dhabi (ADX) listings are not available from the free data provider.",
        "symbols": [
            "DFMGI.AE", "EMAAR.AE", "EMAARDEV.AE", "EMIRATESNBD.AE", "DIB.AE", "DEWA.AE", "SALIK.AE", "DU.AE",
            "AIRARABIA.AE", "TECOM.AE", "PARKIN.AE", "TALABAT.AE", "EMPOWER.AE", "DTC.AE", "DFM.AE", "CBD.AE",
            "MASQ.AE", "ARMX.AE", "DIC.AE", "ALANSARI.AE", "SPINNEYS.AE", "GFH.AE", "AMLAK.AE", "UPP.AE",
        ],
    },
    "us": {
        "label": "US large caps",
        "note": "Largest US-listed companies, prices in USD.",
        "symbols": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "BRK-B", "JPM", "V", "LLY", "XOM", "WMT", "JNJ", "PG", "KO"],
    },
    "indices": {
        "label": "Global indices",
        "note": "Index levels in local currency.",
        "symbols": ["^GSPC", "^DJI", "^IXIC", "^FTSE", "^GDAXI", "^N225", "^HSI", "DFMGI.AE", "^TASI.SR", "^BSESN"],
    },
    "bonds": {
        "label": "Bonds & rates",
        "note": "US Treasury yields (in percent) and bond ETFs. Individual bonds are not quoted by the free provider.",
        "symbols": ["^IRX", "^FVX", "^TNX", "^TYX", "AGG", "BND", "TLT", "IEF", "SHY", "LQD", "HYG", "EMB"],
    },
    "commodities": {
        "label": "Commodities",
        "note": "Front-month futures.",
        "symbols": ["GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F"],
    },
    "fx": {
        "label": "Currencies",
        "note": "The UAE dirham is pegged to the US dollar at 3.6725.",
        "symbols": ["USDAED=X", "EURAED=X", "GBPAED=X", "INRAED=X", "EURUSD=X", "USDJPY=X"],
    },
    "crypto": {"label": "Crypto", "note": "24/7 markets, prices in USD.", "symbols": ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]},
}  # fmt: skip

PERIODS = {
    "1d": timedelta(days=1), "5d": timedelta(days=7), "1mo": timedelta(days=31), "3mo": timedelta(days=92),
    "6mo": timedelta(days=183), "1y": timedelta(days=366), "2y": timedelta(days=731), "3y": timedelta(days=1096),
    "5y": timedelta(days=1827), "10y": timedelta(days=3653), "max": None, "ytd": None,
}  # fmt: skip
DEFAULT_INTERVAL = {"1d": "1h", "5d": "1h", "1mo": "1h", "3mo": "1d", "6mo": "1d", "1y": "1d", "ytd": "1d", "2y": "1d",
                    "3y": "1wk", "5y": "1wk", "10y": "1mo", "max": "1mo"}  # fmt: skip

_SYMBOL = re.compile(r"^[A-Za-z0-9^=.\-]{1,24}$")


def clean_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if not _SYMBOL.match(s):
        raise ConfigurationError(f"invalid symbol '{symbol}'")
    return s


# ---------------------------------------------------------------------- cache


def cached(db: Session, key: str, ttl: timedelta, fetch: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    """Return ``(value, meta)``; meta says when it was fetched and whether it is a stale fallback."""
    row = db.get(MarketCache, key)
    now = utcnow()
    if row is not None and now - row.fetched_at < ttl:
        return row.payload["v"], {"fetched_at": row.fetched_at.isoformat() + "Z", "cached": True, "stale": False}
    try:
        value = fetch()
    except ProviderError as exc:
        if row is not None:
            return row.payload["v"], {
                "fetched_at": row.fetched_at.isoformat() + "Z",
                "cached": True,
                "stale": True,
                "error": exc.message,
            }
        raise
    if row is None:
        row = MarketCache(key=key, payload={"v": value}, fetched_at=now)
        db.add(row)
    else:
        row.payload, row.fetched_at = {"v": value}, now
    try:
        db.commit()
    except Exception:  # a concurrent writer inserted the same key first; the value is still good
        db.rollback()
    return value, {"fetched_at": now.isoformat() + "Z", "cached": False, "stale": False}


# ---------------------------------------------------------------------- search & lists


def search(db: Session, q: str) -> list[dict[str, Any]]:
    """UAE listings (ADX, DFM, bonds) first, then global matches from Yahoo."""
    q = q.strip()
    if not q:
        return []
    from app.services import uae

    try:
        local = uae.search(db, q)
    except ProviderError:
        local = []
    try:
        value, _ = cached(
            db, f"search:{q.lower()[:80]}", timedelta(hours=12), lambda: YahooClient().search(q, quotes=12)["quotes"]
        )
    except ProviderError:
        if not local:
            raise
        value = []
    seen = {x["symbol"] for x in local}
    return local + [x for x in value if x["symbol"] not in seen]


def _uae_quote(db: Session, sym: str) -> dict[str, Any] | None:
    from app.services import uae

    if sym in uae.INDICES:
        return uae.index_quote(db, sym)
    if sym.endswith(".BOND"):
        try:
            b = uae.bond(db, sym)
        except NexisError:
            return None
        return {"symbol": sym, "name": b["name"], "type": "bond", "exchange": b["exchange"], "currency": b["currency"], "price": b["price"],
                "change": None, "change_pct": b["change_pct"], "market_cap": None, "volume": None, "market_time": None, "market_state": None,
                "yield_to_maturity": b["yield_to_maturity"]}  # fmt: skip
    r = uae.lookup(db, sym)
    if r is None:
        return None
    return {k: r[k] for k in ("symbol", "name", "type", "exchange", "currency", "price", "change", "change_pct", "market_cap", "volume")} | {
        "market_time": None, "market_state": None, "sector": r["sector"]}  # fmt: skip


def quotes(db: Session, symbols: list[str]) -> list[dict[str, Any]]:
    from app.services import uae

    syms = [clean_symbol(s) for s in symbols][:80]
    native = [s for s in syms if uae.is_uae_native(s)]
    yahoo = [s for s in syms if s not in native]
    out: list[dict[str, Any]] = []
    if yahoo:
        key = "quotes:" + ",".join(sorted(yahoo))
        try:
            value, _ = cached(db, key[:300], timedelta(seconds=60), lambda: YahooClient().quotes(yahoo))
        except ProviderError:
            value = []
        out += value
        missing = set(yahoo) - {q["symbol"] for q in value}
        native += [s for s in missing if s.endswith(".AE")]  # Dubai listing Yahoo didn't return: use the UAE feed
    for s in native:
        try:
            q = _uae_quote(db, s)
        except ProviderError:
            q = None
        if q:
            out.append(q)
    order = {s: i for i, s in enumerate(syms)}
    return sorted(out, key=lambda q: order.get(q["symbol"], 999))


def market_list(db: Session, key: str) -> dict[str, Any]:
    from app.services import uae

    if key == "uae":
        rows = uae.universe(db)
        items = [{k: r[k] for k in ("symbol", "name", "type", "exchange", "currency", "price", "change", "change_pct", "market_cap", "volume", "sector")}
                 for r in rows if r["type"] == "equity"]  # fmt: skip
        return {"key": key, "label": "UAE · all ADX & DFM shares", "note": f"{len(items)} listed companies on Abu Dhabi (ADX) and Dubai (DFM), prices in AED.",
                "items": items, "sectors": sorted({i["sector"] for i in items}), "source": uae.SOURCE}  # fmt: skip
    if key == "uae_bonds":
        bs = uae.bonds(db)
        items = [{"symbol": b["symbol"], "name": b["name"], "type": "bond", "exchange": b["exchange"], "currency": b["currency"], "price": b["price"],
                  "change": None, "change_pct": b["change_pct"], "market_cap": None, "volume": None, "yield_to_maturity": b["yield_to_maturity"],
                  "coupon": b["coupon"], "maturity": b["maturity"]} for b in bs]  # fmt: skip
        items += quotes(db, ["SPSK", "SKUK.AS", "HBKU.L", "EMB"])
        return {"key": key, "label": "UAE bonds & sukuk", "note": "UAE federal government bonds, AED treasury sukuk, Dubai government sukuk and bank bonds "
                "(price per 100 face value, yield to maturity), plus global sukuk ETFs.", "items": items, "source": uae.SOURCE}  # fmt: skip
    if key not in LISTS:
        raise ConfigurationError(f"unknown list '{key}'")
    spec = LISTS[key]
    return {"key": key, "label": spec["label"], "note": spec["note"], "items": quotes(db, spec["symbols"]), "source": SOURCE}


# ---------------------------------------------------------------------- details


def _num(d: dict[str, Any], k: str) -> float | None:
    v = _raw(d.get(k))
    return float(v) if isinstance(v, int | float) else None


def _normalise(symbol: str, qs: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    price = qs.get("price") or {}
    sd = qs.get("summaryDetail") or {}
    ks = qs.get("defaultKeyStatistics") or {}
    fd = qs.get("financialData") or {}
    ap = qs.get("assetProfile") or {}
    fp = qs.get("fundProfile") or {}
    th = qs.get("topHoldings") or {}
    cal = qs.get("calendarEvents") or {}
    trend = ((qs.get("recommendationTrend") or {}).get("trend") or [{}])[0]
    px = _num(price, "regularMarketPrice") or meta.get("regularMarketPrice")
    target = _num(fd, "targetMeanPrice")
    earnings = [
        datetime.fromtimestamp(_raw(x), UTC).date().isoformat()
        for x in (cal.get("earnings") or {}).get("earningsDate", [])
        if _raw(x)
    ]
    return {
        "symbol": symbol,
        "name": price.get("longName") or price.get("shortName") or meta.get("longName") or symbol,
        "type": (price.get("quoteType") or meta.get("instrumentType") or "").lower(),
        "exchange": price.get("exchangeName") or meta.get("fullExchangeName"),
        "currency": price.get("currency") or meta.get("currency"),
        "financial_currency": fd.get("financialCurrency"),
        "market_state": price.get("marketState"),
        "quote": {
            "price": px,
            "change": _num(price, "regularMarketChange"),
            "change_pct": _num(price, "regularMarketChangePercent"),
            "previous_close": _num(sd, "previousClose") or meta.get("chartPreviousClose"),
            "open": _num(sd, "open"),
            "day_low": _num(sd, "dayLow") or meta.get("regularMarketDayLow"),
            "day_high": _num(sd, "dayHigh") or meta.get("regularMarketDayHigh"),
            "week52_low": _num(sd, "fiftyTwoWeekLow") or meta.get("fiftyTwoWeekLow"),
            "week52_high": _num(sd, "fiftyTwoWeekHigh") or meta.get("fiftyTwoWeekHigh"),
            "ma50": _num(sd, "fiftyDayAverage"),
            "ma200": _num(sd, "twoHundredDayAverage"),
            "volume": _num(sd, "volume") or meta.get("regularMarketVolume"),
            "avg_volume": _num(sd, "averageVolume"),
            "bid": _num(sd, "bid"),
            "ask": _num(sd, "ask"),
            "market_time": datetime.fromtimestamp(meta["regularMarketTime"], UTC).isoformat()
            if meta.get("regularMarketTime")
            else None,
        },
        "valuation": {
            "market_cap": _num(sd, "marketCap") or _num(price, "marketCap"),
            "enterprise_value": _num(ks, "enterpriseValue"),
            "pe_trailing": _num(sd, "trailingPE"),
            "pe_forward": _num(sd, "forwardPE") or _num(ks, "forwardPE"),
            "peg": _num(ks, "pegRatio"),
            "price_to_book": _num(ks, "priceToBook"),
            "price_to_sales": _num(sd, "priceToSalesTrailing12Months"),
            "ev_to_revenue": _num(ks, "enterpriseToRevenue"),
            "ev_to_ebitda": _num(ks, "enterpriseToEbitda"),
            "eps_trailing": _num(ks, "trailingEps"),
            "eps_forward": _num(ks, "forwardEps"),
            "book_value_per_share": _num(ks, "bookValue"),
            "beta": _num(sd, "beta") or _num(ks, "beta"),
        },
        "dividends": {
            "yield": _num(sd, "dividendYield") or _num(sd, "yield"),
            "rate": _num(sd, "dividendRate"),
            "payout_ratio": _num(sd, "payoutRatio"),
            "ex_date": datetime.fromtimestamp(_raw(sd["exDividendDate"]), UTC).date().isoformat()
            if _raw(sd.get("exDividendDate"))
            else None,
            "five_year_avg_yield_pct": _num(sd, "fiveYearAvgDividendYield"),
        },
        "financials": {
            "revenue": _num(fd, "totalRevenue"),
            "gross_profit": _num(fd, "grossProfits"),
            "ebitda": _num(fd, "ebitda"),
            "net_income": _num(ks, "netIncomeToCommon"),
            "free_cash_flow": _num(fd, "freeCashflow"),
            "operating_cash_flow": _num(fd, "operatingCashflow"),
            "cash": _num(fd, "totalCash"),
            "debt": _num(fd, "totalDebt"),
            "gross_margin": _num(fd, "grossMargins"),
            "operating_margin": _num(fd, "operatingMargins"),
            "ebitda_margin": _num(fd, "ebitdaMargins"),
            "profit_margin": _num(fd, "profitMargins"),
            "return_on_equity": _num(fd, "returnOnEquity"),
            "return_on_assets": _num(fd, "returnOnAssets"),
            "revenue_growth": _num(fd, "revenueGrowth"),
            "earnings_growth": _num(fd, "earningsGrowth"),
            "current_ratio": _num(fd, "currentRatio"),
            "debt_to_equity_pct": _num(fd, "debtToEquity"),
        },
        "shares": {
            "outstanding": _num(ks, "sharesOutstanding"),
            "float": _num(ks, "floatShares"),
            "insiders_pct": _num(ks, "heldPercentInsiders"),
            "institutions_pct": _num(ks, "heldPercentInstitutions"),
            "short_pct_float": _num(ks, "shortPercentOfFloat"),
        },
        "analysts": {
            "count": _num(fd, "numberOfAnalystOpinions"),
            "recommendation": fd.get("recommendationKey") if fd.get("recommendationKey") not in (None, "none") else None,
            "recommendation_mean": _num(fd, "recommendationMean"),  # 1 = strong buy … 5 = sell
            "target_mean": target,
            "target_median": _num(fd, "targetMedianPrice"),
            "target_high": _num(fd, "targetHighPrice"),
            "target_low": _num(fd, "targetLowPrice"),
            "upside_to_mean_target": (target / px - 1) if target and px else None,
            "trend": {k: trend.get(k) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")} if trend else None,
        },
        "profile": {
            "sector": ap.get("sector"),
            "industry": ap.get("industry"),
            "country": ap.get("country"),
            "city": ap.get("city"),
            "website": ap.get("website"),
            "employees": ap.get("fullTimeEmployees"),
            "summary": ap.get("longBusinessSummary") or (qs.get("summaryProfile") or {}).get("longBusinessSummary"),
            "officers": [
                {"name": o.get("name"), "title": o.get("title")} for o in (ap.get("companyOfficers") or [])[:4] if o.get("name")
            ],
        },
        "fund": {
            "family": fp.get("family"),
            "category": fp.get("categoryName"),
            "expense_ratio": _num((fp.get("feesExpensesInvestment") or {}), "annualReportExpenseRatio"),
            "top_holdings": [
                {"symbol": h.get("symbol"), "name": h.get("holdingName"), "weight": _num(h, "holdingPercent")}
                for h in (th.get("holdings") or [])[:10]
            ],
        }
        if fp or th
        else None,
        "next_earnings": earnings[0] if earnings else None,
    }


def _uae_details(db: Session, sym: str) -> dict[str, Any]:
    from app.services import uae

    if sym.endswith(".BOND"):
        return uae.bond_details(db, sym)
    if sym in uae.INDICES:
        return uae.index_details(db, sym)
    return uae.details(db, sym)


def details(db: Session, symbol: str) -> dict[str, Any]:
    from app.services import uae

    sym = clean_symbol(symbol)
    if uae.is_uae_native(sym):
        value, meta = cached(
            db,
            f"details:{sym}",
            timedelta(minutes=5),
            lambda: _uae_details(db, sym),
        )
        return {**value, "cache": meta, "source": uae.SOURCE}
    y = YahooClient()

    def fetch() -> dict[str, Any]:
        meta = y.chart(sym, "1d", range_="5d")["meta"]  # also validates that the symbol exists
        try:
            qs = y.quote_summary(sym)
            partial = None
        except ProviderError as exc:
            qs, partial = {}, exc.message
        out = _normalise(sym, qs, meta)
        out["partial"] = partial
        return out

    try:
        value, meta = cached(db, f"details:{sym}", timedelta(minutes=10), fetch)
    except ProviderError:
        if not sym.endswith(".AE") or uae.lookup(db, sym) is None:
            raise
        return {**uae.details(db, sym), "cache": {"fetched_at": None, "cached": False, "stale": False}, "source": uae.SOURCE}
    if sym.endswith(".AE"):
        # Yahoo carries no analyst consensus for most Dubai shares; take it from the UAE feed when missing.
        if not value["analysts"].get("count"):
            r = uae.lookup(db, sym)
            if r is not None:
                value = {**value, "analysts": uae.details(db, sym)["analysts"]}
        value["profile"]["sector"] = value["profile"].get("sector") or (uae.lookup(db, sym) or {}).get("sector")
    return {**value, "cache": meta, "source": SOURCE}


def statements(db: Session, symbol: str) -> dict[str, Any]:
    from app.services import uae

    sym = clean_symbol(symbol)
    if uae.is_uae_native(sym):
        return {"years": [], "rows": [], "symbol": sym, "cache": None, "source": uae.SOURCE,
                "note": "Annual statements are not published by the free UAE data feed; trailing figures are shown instead."}  # fmt: skip
    labels = {
        "annualTotalRevenue": "Revenue", "annualGrossProfit": "Gross profit", "annualEBITDA": "EBITDA",
        "annualOperatingIncome": "Operating income", "annualNetIncome": "Net income", "annualDilutedEPS": "Diluted EPS",
        "annualOperatingCashFlow": "Operating cash flow", "annualCapitalExpenditure": "Capital expenditure",
        "annualFreeCashFlow": "Free cash flow", "annualCashAndCashEquivalents": "Cash & equivalents",
        "annualTotalDebt": "Total debt", "annualStockholdersEquity": "Shareholders' equity",
        "annualDilutedAverageShares": "Diluted shares",
    }  # fmt: skip

    def fetch() -> dict[str, Any]:
        ts = YahooClient().timeseries(sym)
        years = sorted({d[:4] for vals in ts.values() for d, _ in vals})
        rows = []
        for k, label in labels.items():
            by_year = {d[:4]: v for d, v in ts.get(k, [])}
            if by_year:
                rows.append({"key": k, "label": label, "values": {yr: by_year.get(yr) for yr in years}})
        return {"years": years, "rows": rows}

    value, meta = cached(db, f"statements:{sym}", timedelta(hours=12), fetch)
    return {**value, "symbol": sym, "cache": meta, "source": SOURCE}


def peers(db: Session, symbol: str) -> list[str]:
    from app.services import uae

    sym = clean_symbol(symbol)
    if sym.endswith((".AD", ".AE")):
        try:
            local = uae.peers(db, sym)
        except ProviderError:
            local = []
        if local:
            return local
    value, _ = cached(db, f"peers:{sym}", timedelta(days=3), lambda: YahooClient().peers(sym))
    return value


def news(db: Session, symbol: str) -> dict[str, Any]:
    sym = clean_symbol(symbol)
    try:
        name = details(db, sym)["name"]
    except ProviderError:
        name = None
    value, meta = cached(db, f"news:{sym}", timedelta(minutes=20), lambda: news_mod.instrument_news(sym, name))
    return {**value, "symbol": sym, "cache": meta}


# ---------------------------------------------------------------------- history


def resolve_window(
    period: str | None, start: date | None, end: date | None, interval: str | None
) -> tuple[date | None, date | None, str]:
    if period and period not in PERIODS:
        raise ConfigurationError(f"unknown period '{period}' (use one of {', '.join(PERIODS)})")
    if interval and interval not in INTERVALS:
        raise ConfigurationError(f"unknown interval '{interval}' (use one of {', '.join(INTERVALS)})")
    today = datetime.now(UTC).date()
    if start or end:
        end = end or today
        start = start or end - timedelta(days=365)
        if start >= end:
            raise ConfigurationError("start must be before end")
        span = (end - start).days
        iv = interval or ("1h" if span <= 31 else "1d" if span <= 800 else "1wk" if span <= 3000 else "1mo")
    else:
        period = period or "1y"
        iv = interval or DEFAULT_INTERVAL[period]
        end = None
        if period == "ytd":
            start = date(today.year, 1, 1)
        elif PERIODS[period] is None:
            start = None
        else:
            start = today - PERIODS[period]
    if iv == "1h":
        limit = today - timedelta(days=INTERVALS["1h"][1])
        if start is None or start < limit:
            raise ConfigurationError(
                "hourly data is only available for roughly the last two years — pick a shorter window or a daily interval"
            )
    return start, end, iv


def history(db: Session, symbol: str, interval: str, start: date | None, end: date | None) -> dict[str, Any]:
    from app.services import uae

    sym = clean_symbol(symbol)
    ttl = timedelta(minutes=5) if interval == "1h" else timedelta(minutes=30)
    key = f"hist:{sym}:{interval}:{start}:{end}"
    if uae.is_uae_native(sym):
        fetch = lambda: uae.history(db, sym, interval, start, end)  # noqa: E731
    else:

        def fetch() -> dict[str, Any]:
            try:
                return YahooClient().chart(sym, interval, start, end)
            except ProviderError:
                if sym.endswith(".AE"):
                    return uae.history(db, sym, interval, start, end)
                raise

    value, meta = cached(db, key, ttl, fetch)
    return {"symbol": sym, "interval": interval, "bars": value["bars"], "meta": value["meta"], "cache": meta}


def close_frame(
    db: Session, symbols: list[str], interval: str, start: date | None, end: date | None
) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Adjusted closes aligned on a common timestamp index (outer join, no forward fill)."""
    series, metas = {}, {}
    for s in symbols:
        h = history(db, s, interval, start, end)
        if not h["bars"]:
            raise ProviderError(f"no {interval} bars for {s} in that window")
        idx = pd.to_datetime([b["t"] for b in h["bars"]], utc=True)
        if interval != "1h":
            idx = idx.tz_convert(None).normalize()
        ser = pd.Series([b["adj_close"] for b in h["bars"]], index=idx, dtype=float)
        series[h["symbol"]] = ser[~ser.index.duplicated(keep="last")]
        metas[h["symbol"]] = h["meta"]
    return pd.DataFrame(series).sort_index(), metas
