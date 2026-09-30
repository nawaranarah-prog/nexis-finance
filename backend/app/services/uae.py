"""The UAE market: every ADX and DFM listing, UAE government bonds and sukuk.

* Abu Dhabi (ADX) shares use the suffix ``.AD`` (e.g. ``FAB.AD``) and come from TradingView's
  public screener and charts; Dubai (DFM) shares keep Yahoo's ``.AE`` suffix (``EMAAR.AE``), with
  TradingView as a fallback.
* Bonds and sukuk use ``.BOND`` (e.g. ``UAE0734USD.BOND``). Their chart history is published as
  yields; prices are derived from the yield, coupon and maturity (semi-annual convention), and the
  total-return series adds the coupon carry, so bonds can be compared with shares.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ProviderError
from app.markets import tradingview as tv

SOURCE = "TradingView public screener and charts (delayed, unofficial)"

SECTORS: list[tuple[str, tuple[str, ...]]] = [
    ("Banks", ("Major Banks", "Regional Banks")),
    ("Real estate", ("Real Estate Development", "Real Estate Investment Trusts", "Homebuilding")),
    ("Energy & chemicals", ("Integrated Oil", "Contract Drilling", "Oil & Gas Production", "Chemicals: Specialty", "Chemicals: Agricultural")),
    ("Utilities", ("Electric Utilities", "Water Utilities", "Gas Distributors")),
    ("Telecom & technology", ("Specialty Telecommunications", "Major Telecommunications", "Packaged Software", "Data Processing Services", "Information Technology Services")),
    ("Transport & logistics", ("Other Transportation", "Air Freight/Couriers", "Airlines", "Marine Shipping", "Trucking")),
    ("Consumer & retail", ("Food Retail", "Specialty Stores", "Restaurants", "Internet Retail", "Beverages: Non-Alcoholic", "Food Distributors",
                           "Agricultural Commodities/Milling", "Hotels/Resorts/Cruise lines", "Other Consumer Services", "Movies/Entertainment",
                           "Recreational Products", "Publishing: Books/Magazines", "Food: Specialty/Candy")),
    ("Insurance", ("Multi-Line Insurance", "Property/Casualty Insurance", "Life/Health Insurance", "Specialty Insurance")),
    ("Financial services", ("Investment Banks/Brokers", "Investment Managers", "Financial Conglomerates", "Miscellaneous Commercial Services", "Finance/Rental/Leasing")),
    ("Industrials & construction", ("Construction Materials", "Engineering & Construction", "Industrial Machinery", "Steel", "Building Products")),
    ("Healthcare & education", ("Medical/Nursing Services", "Pharmaceuticals: Generic", "Hospital/Nursing Management")),
    ("Funds & ETFs", ("Investment Trusts/Mutual Funds",)),
]  # fmt: skip
# Holding companies are classed by what they are, not by their largest subsidiary.
OVERRIDES = {"IHC": "Holdings & conglomerates", "ALPHADHABI": "Holdings & conglomerates", "2POINTZERO": "Holdings & conglomerates",
             "MODON": "Holdings & conglomerates", "WAHA": "Holdings & conglomerates", "DIC": "Holdings & conglomerates",
             "ALEFEDT": "Healthcare & education", "TAALEEM": "Healthcare & education", "ADNOCLS": "Transport & logistics"}  # fmt: skip

# UAE sovereign and quasi-sovereign bonds and sukuk (fixed coupon, known maturity). More are discovered by search.
BONDS: list[str] = [
    "NASDAQDUBAI:UAE0732USD", "NASDAQDUBAI:UAE0734USD", "NASDAQDUBAI:UAE0752USD",
    "NASDAQDUBAI:UAEGS1027AED", "NASDAQDUBAI:UAEGS0628AED", "NASDAQDUBAI:UAEGS0233AED",
    "FWB:XS222704910", "FWB:XS106203814", "NASDAQDUBAI:EBIUH1027USD",
]  # fmt: skip
UAE_ISSUER = re.compile(
    r"\b(UAE|United Arab Emirates|Abu Dhabi|Dubai|Sharjah|Emirates|Ras Al Khaimah|Fujairah|Mashreq|ADNOC|Mubadala|DP World|Aldar|Emaar|"
    r"DEWA|Etisalat|Tabreed|Masdar|ADCB|ADIB|RAKBANK|Commercial Bank of Dubai|First Abu Dhabi)\b",
    re.I,
)
_BOND_DESC = re.compile(r"(\d+(?:\.\d+)?)%\s+(\d{2})-([A-Z]{3})-(\d{4})")
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def sector_of(code: str, industry: str | None) -> str:
    if code in OVERRIDES:
        return OVERRIDES[code]
    for name, inds in SECTORS:
        if industry in inds:
            return name
    return "Other"


def to_symbol(tv_symbol: str) -> str:
    exch, code = tv_symbol.split(":", 1)
    return f"{code}.AD" if exch == "ADX" else f"{code}.AE" if exch == "DFM" else f"{code}.BOND"


# The two market indices, served from the exchanges' own series.
INDICES = {"DFMGI.AE": ("DFM:DFMGI", "DFM General Index"), "FADGI.AD": ("ADX:FADGI", "FTSE ADX General Index")}


def is_uae_native(symbol: str) -> bool:
    return symbol.endswith((".AD", ".BOND")) or symbol in INDICES


def index_quote(db: Session, symbol: str) -> dict[str, Any]:
    from app.services.markets import cached

    tv_symbol, name = INDICES[symbol]

    def fetch() -> dict[str, Any]:
        bars = tv.history(tv_symbol, "1d", 260)["bars"]
        if len(bars) < 2:
            raise ProviderError(f"no data for {name}")
        closes = [b["close"] for b in bars]
        last, prev = closes[-1], closes[-2]
        return {"symbol": symbol, "name": name, "type": "index", "exchange": tv_symbol.split(":")[0], "currency": "AED", "price": last,
                "change": last - prev, "change_pct": last / prev - 1, "market_cap": None, "volume": None, "week52_high": max(closes),
                "week52_low": min(closes), "market_time": bars[-1]["t"], "market_state": None}  # fmt: skip

    value, _ = cached(db, f"uae:index:{symbol}", timedelta(minutes=10), fetch)
    return value


def index_details(db: Session, symbol: str) -> dict[str, Any]:
    q = index_quote(db, symbol)
    empty = dict.fromkeys(("count", "recommendation", "recommendation_mean", "target_mean", "target_median", "target_high", "target_low",
                           "upside_to_mean_target", "trend"))  # fmt: skip
    return {
        "symbol": symbol, "name": q["name"], "type": "index", "exchange": q["exchange"], "currency": "AED", "financial_currency": "AED", "market_state": None,
        "quote": {"price": q["price"], "change": q["change"], "change_pct": q["change_pct"], "previous_close": q["price"] - q["change"], "open": None,
                  "day_low": None, "day_high": None, "week52_low": q["week52_low"], "week52_high": q["week52_high"], "ma50": None, "ma200": None,
                  "volume": None, "avg_volume": None, "bid": None, "ask": None, "market_time": q["market_time"]},
        "valuation": dict.fromkeys(("market_cap", "enterprise_value", "pe_trailing", "pe_forward", "peg", "price_to_book", "price_to_sales",
                                    "ev_to_revenue", "ev_to_ebitda", "eps_trailing", "eps_forward", "book_value_per_share", "beta")),
        "dividends": {"yield": None, "rate": None, "payout_ratio": None, "ex_date": None, "five_year_avg_yield_pct": None},
        "financials": {}, "shares": {}, "analysts": empty,
        "profile": {"sector": "Index", "industry": None, "country": "United Arab Emirates", "city": None, "website": None, "employees": None,
                    "officers": [], "summary": f"{q['name']}: the benchmark index of the {'Dubai Financial Market' if symbol.endswith('.AE') else 'Abu Dhabi Securities Exchange'}."},
        "fund": None, "next_earnings": None, "partial": None,
    }  # fmt: skip


def _pct(v: Any) -> float | None:
    return v / 100 if isinstance(v, int | float) else None


def _row(r: dict[str, Any]) -> dict[str, Any]:
    code = r["tv"].split(":", 1)[1]
    return {
        "symbol": to_symbol(r["tv"]), "tv": r["tv"], "code": code, "name": r.get("description") or code, "exchange": r.get("exchange"),
        "type": "fund" if r.get("industry") == "Investment Trusts/Mutual Funds" else "equity", "currency": r.get("currency") or "AED",
        "price": r.get("close"), "change": r.get("change_abs"), "change_pct": _pct(r.get("change")), "market_cap": r.get("market_cap_basic"),
        "volume": r.get("volume"), "sector": sector_of(code, r.get("industry")), "industry": r.get("industry"), "raw": r,
    }  # fmt: skip


def universe(db: Session) -> list[dict[str, Any]]:
    """Every ADX and DFM listing with live quote, sector and fundamentals (cached for 5 minutes)."""
    from app.services.markets import cached

    def fetch() -> list[dict[str, Any]]:
        rows = tv.scan("uae", tv.STOCK_COLUMNS)
        return [_row(r) for r in rows if r.get("exchange") in ("ADX", "DFM") and r.get("close") is not None]

    value, _ = cached(db, "uae:universe", timedelta(minutes=5), fetch)
    return value


def lookup(db: Session, symbol: str) -> dict[str, Any] | None:
    for r in universe(db):
        if r["symbol"] == symbol:
            return r
    return None


def search(db: Session, q: str, limit: int = 8) -> list[dict[str, Any]]:
    t = q.strip().lower()
    if len(t) < 2:
        return []
    hits = []
    for r in universe(db):
        name, code = r["name"].lower(), r["code"].lower()
        score = 3 if code == t else 2 if code.startswith(t) or name.startswith(t) else 1 if t in name or t in code else 0
        if score:
            hits.append((score, r["market_cap"] or 0, r))
    hits.sort(key=lambda x: (-x[0], -x[1]))
    out = [{"symbol": r["symbol"], "name": r["name"], "exchange": r["exchange"], "type": r["type"], "sector": r["sector"], "industry": r["industry"]}
           for _, _, r in hits[:limit]]  # fmt: skip
    if any(w in t for w in ("bond", "sukuk", "treasury", "government", "gov")) or t.startswith("uae"):
        out += [{"symbol": b["symbol"], "name": b["name"], "exchange": b["exchange"], "type": "bond", "sector": "Bonds & sukuk", "industry": None}
                for b in bonds(db) if t in b["name"].lower() or any(w in t for w in ("bond", "sukuk"))][:6]  # fmt: skip
    return out


def by_sector(db: Session) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in universe(db):
        groups.setdefault(r["sector"], []).append(r)
    return groups


def peers(db: Session, symbol: str, limit: int = 8) -> list[str]:
    me = lookup(db, symbol)
    if me is None:
        return []
    same = [r for r in universe(db) if r["sector"] == me["sector"] and r["symbol"] != symbol and r["type"] == "equity"]
    same.sort(key=lambda r: -(r["market_cap"] or 0))
    return [r["symbol"] for r in same[:limit]]


def details(db: Session, symbol: str) -> dict[str, Any]:
    """Normalised instrument details (same shape as markets.details) for a UAE listing."""
    r = lookup(db, symbol)
    if r is None:
        raise NotFoundError(f"{symbol} is not a listed UAE security")
    d = r["raw"]
    buy, hold, sell = d.get("recommendation_buy"), d.get("recommendation_hold"), d.get("recommendation_sell")
    total = d.get("recommendation_total")
    mark = d.get("recommendation_mark")  # 1 = strong buy … 5 = strong sell
    rec = None if mark is None else "strong_buy" if mark <= 1.5 else "buy" if mark <= 2.5 else "hold" if mark <= 3.5 else "sell"
    px, target = r["price"], d.get("price_target_average")
    return {
        "symbol": symbol, "name": r["name"], "type": r["type"], "exchange": "Abu Dhabi (ADX)" if r["exchange"] == "ADX" else "Dubai (DFM)",
        "currency": r["currency"], "financial_currency": r["currency"], "market_state": None,
        "quote": {"price": px, "change": r["change"], "change_pct": r["change_pct"], "previous_close": px - r["change"] if px is not None and r["change"] is not None else None,
                  "open": d.get("open"), "day_low": d.get("low"), "day_high": d.get("high"), "week52_low": d.get("price_52_week_low"),
                  "week52_high": d.get("price_52_week_high"), "ma50": d.get("SMA50"), "ma200": d.get("SMA200"), "volume": r["volume"],
                  "avg_volume": d.get("average_volume_30d_calc"), "bid": None, "ask": None, "market_time": datetime.now(UTC).isoformat()},
        "valuation": {"market_cap": r["market_cap"], "enterprise_value": None, "pe_trailing": d.get("price_earnings_ttm"), "pe_forward": None,
                      "peg": None, "price_to_book": d.get("price_book_fq"), "price_to_sales": d.get("price_sales_current"), "ev_to_revenue": None,
                      "ev_to_ebitda": d.get("enterprise_value_ebitda_ttm"), "eps_trailing": d.get("earnings_per_share_basic_ttm"), "eps_forward": None,
                      "book_value_per_share": px / d["price_book_fq"] if px and d.get("price_book_fq") else None, "beta": d.get("beta_1_year")},
        "dividends": {"yield": _pct(d.get("dividends_yield_current")), "rate": px * d["dividends_yield_current"] / 100 if px and d.get("dividends_yield_current") else None,
                      "payout_ratio": None, "ex_date": None, "five_year_avg_yield_pct": None},
        "financials": {"revenue": d.get("total_revenue"), "gross_profit": None, "ebitda": d.get("ebitda"), "net_income": d.get("net_income"),
                       "free_cash_flow": d.get("free_cash_flow"), "operating_cash_flow": None, "cash": d.get("cash_n_short_term_invest_fq"),
                       "debt": d.get("total_debt"), "gross_margin": _pct(d.get("gross_margin")), "operating_margin": _pct(d.get("operating_margin")),
                       "ebitda_margin": None, "profit_margin": _pct(d.get("net_margin")), "return_on_equity": _pct(d.get("return_on_equity")),
                       "return_on_assets": None, "revenue_growth": None, "earnings_growth": None, "current_ratio": None,
                       "debt_to_equity_pct": d["debt_to_equity"] * 100 if d.get("debt_to_equity") is not None else None},
        "shares": {"outstanding": d.get("total_shares_outstanding"), "float": None, "insiders_pct": None, "institutions_pct": None, "short_pct_float": None},
        "analysts": {"count": total, "recommendation": rec, "recommendation_mean": mark, "target_mean": target, "target_median": None,
                     "target_high": d.get("price_target_high"), "target_low": d.get("price_target_low"),
                     "upside_to_mean_target": target / px - 1 if target and px else None,
                     "trend": {"strongBuy": 0, "buy": buy or 0, "hold": hold or 0, "sell": sell or 0, "strongSell": 0} if total else None},
        "profile": {"sector": r["sector"], "industry": r["industry"], "country": "United Arab Emirates", "city": "Abu Dhabi" if r["exchange"] == "ADX" else "Dubai",
                    "website": None, "employees": None, "summary": None, "officers": []},
        "fund": None, "next_earnings": None, "partial": None,
        "performance": {"1w": _pct(d.get("Perf.W")), "1m": _pct(d.get("Perf.1M")), "3m": _pct(d.get("Perf.3M")), "ytd": _pct(d.get("Perf.YTD")), "1y": _pct(d.get("Perf.Y"))},
    }  # fmt: skip


# ---------------------------------------------------------------------- bonds & sukuk


def _parse_bond(desc: str) -> tuple[float, date] | None:
    m = _BOND_DESC.search(desc.upper())
    if not m or "PERP" in desc.upper() or "FRN" in desc.upper():
        return None
    return float(m.group(1)), date(int(m.group(4)), _MONTHS[m.group(3)], int(m.group(2)))


def bonds(db: Session) -> list[dict[str, Any]]:
    """UAE government and quasi-government bonds/sukuk with price, yield, coupon and maturity (cached)."""
    from app.services.markets import cached

    def fetch() -> list[dict[str, Any]]:
        tickers = list(BONDS)
        for q in (
            "UAE Federal Government",
            "Government of United Arab Emirates",
            "Abu Dhabi Government",
            "Mubadala",
            "DP World",
            "Abu Dhabi National Oil",
            "Sharjah Sukuk",
            "Dubai DOF Sukuk",
        ):
            try:
                tickers += [b["tv"] for b in tv.search_bonds(q, 12)]
            except ProviderError:
                continue
        tickers = list(dict.fromkeys(tickers))
        rows = tv.scan("bond", tv.BOND_COLUMNS, tickers=tickers)
        out, seen = [], set()
        for r in rows:
            parsed = _parse_bond(r.get("description") or "")
            if parsed is None or r.get("close") is None or not UAE_ISSUER.search(r.get("description") or ""):
                continue
            coupon, maturity = parsed
            key = (r["description"].split()[0], coupon, maturity)
            if maturity <= datetime.now(UTC).date() or key in seen:
                continue
            seen.add(key)
            code = r["tv"].split(":", 1)[1]
            ccy = "AED" if code.endswith("AED") else "USD"
            out.append({
                "symbol": f"{code}.BOND", "tv": r["tv"], "name": r["description"], "exchange": r.get("exchange"), "type": "bond",
                "currency": ccy, "price": r["close"], "change_pct": _pct(r.get("change")), "yield_to_maturity": _pct(r.get("yield_to_maturity")),
                "coupon": coupon / 100, "maturity": maturity.isoformat(), "years_to_maturity": round((maturity - datetime.now(UTC).date()).days / 365.25, 2),
                "issuer": re.split(r"\s+\d", r["description"])[0],
            })  # fmt: skip
        out.sort(key=lambda b: (b["issuer"], b["maturity"]))
        return out

    value, _ = cached(db, "uae:bonds", timedelta(hours=6), fetch)
    return value


def bond(db: Session, symbol: str) -> dict[str, Any]:
    for b in bonds(db):
        if b["symbol"] == symbol:
            return b
    raise NotFoundError(f"{symbol} is not in the UAE bond list")


def price_from_yield(y: float, coupon: float, years: float) -> float:
    """Price per 100 face value of a semi-annual bond with ``years`` left (fractional periods allowed)."""
    n = max(years * 2, 1e-6)
    r = y / 2
    c = coupon * 100 / 2
    if abs(r) < 1e-9:
        return c * n + 100
    return c * (1 - (1 + r) ** -n) / r + 100 * (1 + r) ** -n


def bond_details(db: Session, symbol: str) -> dict[str, Any]:
    b = bond(db, symbol)
    return {
        "symbol": symbol, "name": b["name"], "type": "bond", "exchange": b["exchange"], "currency": b["currency"], "financial_currency": b["currency"],
        "market_state": None,
        "quote": {"price": b["price"], "change": None, "change_pct": b["change_pct"], "previous_close": None, "open": None, "day_low": None, "day_high": None,
                  "week52_low": None, "week52_high": None, "ma50": None, "ma200": None, "volume": None, "avg_volume": None, "bid": None, "ask": None,
                  "market_time": datetime.now(UTC).isoformat()},
        "valuation": {k: None for k in ("market_cap", "enterprise_value", "pe_trailing", "pe_forward", "peg", "price_to_book", "price_to_sales",
                                        "ev_to_revenue", "ev_to_ebitda", "eps_trailing", "eps_forward", "book_value_per_share", "beta")},
        "dividends": {"yield": b["coupon"] * 100 / b["price"] if b["price"] else None, "rate": b["coupon"] * 100, "payout_ratio": None, "ex_date": None,
                      "five_year_avg_yield_pct": None},
        "financials": {}, "shares": {}, "analysts": {"count": None, "recommendation": None, "recommendation_mean": None, "target_mean": None,
                                                     "target_median": None, "target_high": None, "target_low": None, "upside_to_mean_target": None, "trend": None},
        "profile": {"sector": "Bonds & sukuk", "industry": "Sukuk" if "sukuk" in b["name"].lower() else "Government bond", "country": "United Arab Emirates",
                    "city": None, "website": None, "employees": None, "officers": [],
                    "summary": (f"{b['name']}: fixed coupon {b['coupon'] * 100:.3f}% paid semi-annually, maturing {b['maturity']} "
                                f"({b['years_to_maturity']:.1f} years), quoted in {b['currency']} per 100 face value. "
                                f"Yield to maturity {b['yield_to_maturity'] * 100:.2f}%." if b.get("yield_to_maturity") else b["name"])},
        "bond": {k: b[k] for k in ("coupon", "maturity", "years_to_maturity", "yield_to_maturity", "issuer")},
        "fund": None, "next_earnings": None, "partial": None,
    }  # fmt: skip


def _bars_for(interval: str, start: date | None) -> int:
    days = (datetime.now(UTC).date() - start).days + 5 if start else 12000
    per_day = {"1h": 7, "1d": 5 / 7, "1wk": 1 / 7, "1mo": 1 / 30}[interval]
    return int(min(5000, max(30, math.ceil(days * per_day) + 5)))


def history(db: Session, symbol: str, interval: str, start: date | None, end: date | None) -> dict[str, Any]:
    """Bars for an ADX/DFM share (price) or a bond (price and total return derived from the yield)."""
    if symbol.endswith(".BOND"):
        b = bond(db, symbol)
        tv_symbol, currency = b["tv"], b["currency"]
    elif symbol in INDICES:
        tv_symbol, currency, b = INDICES[symbol][0], "AED", None
    else:
        code, suffix = symbol.rsplit(".", 1)
        tv_symbol, currency = f"{'ADX' if suffix == 'AD' else 'DFM'}:{code}", "AED"
        b = None
    res = tv.history(tv_symbol, interval, _bars_for(interval, start))
    bars = res["bars"]
    if start:
        bars = [x for x in bars if x["t"][:10] >= start.isoformat()]
    if end:
        bars = [x for x in bars if x["t"][:10] <= end.isoformat()]
    if b is not None:
        maturity = date.fromisoformat(b["maturity"])
        tr, prev_t, prev_p = None, None, None
        out = []
        for x in bars:
            t = datetime.fromisoformat(x["t"])
            y = x["close"] / 100
            years = max((maturity - t.date()).days / 365.25, 0.0)
            p = price_from_yield(y, b["coupon"], years)
            if tr is None:
                tr = p
            else:
                dt_years = (t - prev_t).total_seconds() / (365.25 * 86400)
                tr *= (p + b["coupon"] * 100 * dt_years) / prev_p
            out.append({"t": x["t"], "open": p, "high": p, "low": p, "close": p, "adj_close": tr, "volume": None, "yield": y})
            prev_t, prev_p = t, p
        bars = out
    meta = {"currency": currency, "longName": (res["info"] or {}).get("description") or symbol, "instrumentType": "BOND" if b else "EQUITY",
            "exchangeName": tv_symbol.split(":")[0]}  # fmt: skip
    return {"meta": meta, "bars": bars}
