"""SEC EDGAR: issuer profiles (SIC industry, business address) and XBRL company facts (keyless).

SEC's fair-access policy requires a descriptive User-Agent with contact details and ≤ 10 requests/s.
Classification is the issuer's own SIC code as filed — *not* a GICS sector — and geography is the
business-address location, which is not the same as revenue exposure. Both are labelled as such.
"""

from __future__ import annotations

import threading
import time
from datetime import date
from typing import Any

import pandas as pd

from app.connectivity.base import ProviderSpec, RegulatoryDataProvider
from app.core.config import get_settings
from app.core.errors import ProviderError

SIC_MAJOR_GROUPS = {
    "01": "Agricultural Production — Crops",
    "02": "Agricultural Production — Livestock",
    "07": "Agricultural Services",
    "08": "Forestry",
    "09": "Fishing, Hunting and Trapping",
    "10": "Metal Mining",
    "12": "Coal Mining",
    "13": "Oil and Gas Extraction",
    "14": "Mining of Nonmetallic Minerals",
    "15": "Building Construction",
    "16": "Heavy Construction",
    "17": "Construction Special Trade Contractors",
    "20": "Food and Kindred Products",
    "21": "Tobacco Products",
    "22": "Textile Mill Products",
    "23": "Apparel",
    "24": "Lumber and Wood Products",
    "25": "Furniture and Fixtures",
    "26": "Paper and Allied Products",
    "27": "Printing and Publishing",
    "28": "Chemicals and Allied Products",
    "29": "Petroleum Refining",
    "30": "Rubber and Plastics Products",
    "31": "Leather Products",
    "32": "Stone, Clay, Glass and Concrete",
    "33": "Primary Metal Industries",
    "34": "Fabricated Metal Products",
    "35": "Industrial Machinery and Computer Equipment",
    "36": "Electronic and Electrical Equipment",
    "37": "Transportation Equipment",
    "38": "Measuring, Medical and Optical Instruments",
    "39": "Miscellaneous Manufacturing",
    "40": "Railroad Transportation",
    "41": "Local Passenger Transit",
    "42": "Motor Freight and Warehousing",
    "44": "Water Transportation",
    "45": "Air Transportation",
    "46": "Pipelines",
    "47": "Transportation Services",
    "48": "Communications",
    "49": "Electric, Gas and Sanitary Services",
    "50": "Wholesale — Durable Goods",
    "51": "Wholesale — Nondurable Goods",
    "52": "Building Materials Retail",
    "53": "General Merchandise Stores",
    "54": "Food Stores",
    "55": "Automotive Dealers",
    "56": "Apparel Stores",
    "57": "Home Furnishing Stores",
    "58": "Eating and Drinking Places",
    "59": "Miscellaneous Retail",
    "60": "Depository Institutions",
    "61": "Nondepository Credit Institutions",
    "62": "Security and Commodity Brokers",
    "63": "Insurance Carriers",
    "64": "Insurance Agents",
    "65": "Real Estate",
    "67": "Holding and Investment Offices",
    "70": "Hotels and Lodging",
    "72": "Personal Services",
    "73": "Business Services",
    "75": "Automotive Repair",
    "76": "Miscellaneous Repair",
    "78": "Motion Pictures",
    "79": "Amusement and Recreation",
    "80": "Health Services",
    "81": "Legal Services",
    "82": "Educational Services",
    "83": "Social Services",
    "84": "Museums",
    "86": "Membership Organizations",
    "87": "Engineering, Research and Management Services",
    "89": "Services, Not Elsewhere Classified",
    "99": "Nonclassifiable Establishments",
}
_DIVISIONS = [
    (1, 9, "Agriculture, Forestry and Fishing"),
    (10, 14, "Mining"),
    (15, 17, "Construction"),
    (20, 39, "Manufacturing"),
    (40, 49, "Transportation, Communications and Utilities"),
    (50, 51, "Wholesale Trade"),
    (52, 59, "Retail Trade"),
    (60, 67, "Finance, Insurance and Real Estate"),
    (70, 89, "Services"),
    (91, 97, "Public Administration"),
    (99, 99, "Nonclassifiable"),
]
US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
    "PR",
}
# EDGAR foreign location codes for common non-US headquarters.
EDGAR_COUNTRIES = {
    "X0": "United Kingdom",
    "K3": "Hong Kong",
    "F4": "China",
    "L2": "Ireland",
    "E9": "Cayman Islands",
    "A6": "Canada (Ontario)",
    "A8": "Canada (Quebec)",
    "A1": "Canada (British Columbia)",
    "C3": "Bermuda",
    "L3": "Israel",
    "M0": "Japan",
    "N4": "Netherlands",
    "V8": "Switzerland",
    "2M": "Germany",
    "I0": "France",
    "U0": "Singapore",
    "F2": "Taiwan",
    "M5": "South Korea",
}

FACT_CONCEPTS = {
    ("us-gaap", "Revenues"): "Revenue",
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"): "Revenue",
    ("us-gaap", "NetIncomeLoss"): "Net income",
    ("us-gaap", "OperatingIncomeLoss"): "Operating income",
    ("us-gaap", "Assets"): "Total assets",
    ("us-gaap", "Liabilities"): "Total liabilities",
    ("us-gaap", "StockholdersEquity"): "Stockholders' equity",
    ("us-gaap", "EarningsPerShareDiluted"): "Diluted EPS",
    ("dei", "EntityCommonStockSharesOutstanding"): "Shares outstanding",
}

_ticker_map: dict[str, int] | None = None
_lock = threading.Lock()
_last_call = [0.0]


def sic_classification(sic: str | None) -> tuple[str | None, str | None]:
    if not sic or not str(sic).isdigit():
        return None, None
    s = str(sic).zfill(4)
    major = SIC_MAJOR_GROUPS.get(s[:2])
    code = int(s[:2])
    div = next((name for a, b, name in _DIVISIONS if a <= code <= b), None)
    return major, div


def location_to_country(code: str | None) -> str | None:
    if not code:
        return None
    return "United States" if code.upper() in US_STATES else EDGAR_COUNTRIES.get(code.upper(), f"EDGAR location {code}")


class SecEdgarProvider(RegulatoryDataProvider):
    spec = ProviderSpec(
        key="sec_edgar",
        name="SEC EDGAR — Filings & XBRL Company Facts",
        category="regulatory",
        description="Issuer profiles (SIC industry, business address) and reported financial facts from SEC filings for US-listed companies.",
        capabilities=("company_profiles", "fundamentals"),
        auth_type="none",
        requirements=("Internet access", "Descriptive User-Agent (NEXIS_SEC_USER_AGENT)"),
        docs_url="https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        implemented=True,
        verification="live",
        data_class="real_external",
        notes="ETFs and funds have no operating-company facts; they remain unclassified rather than guessed.",
    )

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": get_settings().sec_user_agent, "Accept-Encoding": "gzip, deflate"}

    def _get_json(self, url: str) -> Any:
        with _lock:  # stay well below the 10 requests/second fair-access limit
            wait = 0.15 - (time.monotonic() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.monotonic()
        resp = self.http_get(url, headers=self._headers())
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError("SEC EDGAR: invalid JSON response") from exc

    def ticker_map(self) -> dict[str, int]:
        global _ticker_map
        if _ticker_map is None:
            data = self._get_json("https://www.sec.gov/files/company_tickers.json")
            _ticker_map = {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in data.values()}
        return _ticker_map

    def verify(self) -> dict[str, Any]:
        return {"tickers_indexed": len(self.ticker_map())}

    def get_profile(self, symbol: str) -> dict[str, Any] | None:
        cik = self.ticker_map().get(symbol.upper())
        if cik is None:
            return None
        sub = self._get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        major, division = sic_classification(sub.get("sic"))
        biz = (sub.get("addresses") or {}).get("business") or {}
        loc = biz.get("stateOrCountry")
        return {
            "symbol": symbol.upper(),
            "cik": cik,
            "name": sub.get("name") or symbol,
            "sic": sub.get("sic") or None,
            "sic_description": sub.get("sicDescription") or None,
            "sic_major_group": major,
            "sic_division": division,
            "business_state_or_country": loc,
            "business_country": location_to_country(loc),
            "state_of_incorporation": sub.get("stateOfIncorporation") or None,
            "exchanges": sub.get("exchanges") or [],
            "fiscal_year_end": sub.get("fiscalYearEnd"),
        }

    def get_fundamentals(self, cik: int, years: int = 6) -> list[dict[str, Any]]:
        facts = self._get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json").get("facts", {})
        out: list[dict[str, Any]] = []
        cutoff = date.today().year - years
        for (ns, concept), label in FACT_CONCEPTS.items():
            node = facts.get(ns, {}).get(concept)
            if not node:
                continue
            for unit, rows in node.get("units", {}).items():
                for r in rows:
                    if r.get("form") not in ("10-K", "20-F", "40-F") or r.get("fp") not in ("FY", None):
                        continue
                    if (r.get("fy") or 0) < cutoff:
                        continue
                    out.append(
                        {
                            "concept": f"{ns}:{concept}",
                            "label": label,
                            "unit": unit,
                            "value": float(r["val"]),
                            "period_end": pd.Timestamp(r["end"]).date(),
                            "fy": r.get("fy"),
                            "fp": r.get("fp") or "FY",
                            "form": r.get("form"),
                            "filed": pd.Timestamp(r["filed"]).date() if r.get("filed") else None,
                        }
                    )
        # Keep the latest filing for each (concept, period end): amendments supersede originals.
        best: dict[tuple[str, date], dict[str, Any]] = {}
        for r in out:
            k = (r["concept"], r["period_end"])
            if k not in best or (r["filed"] or date.min) > (best[k]["filed"] or date.min):
                best[k] = r
        return sorted(best.values(), key=lambda r: (r["concept"], r["period_end"]))
