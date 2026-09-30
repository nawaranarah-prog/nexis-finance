"""Economic data: US Treasury par yield curve (keyless), World Bank indicators (keyless), FRED (API key)."""

from __future__ import annotations

import io
from datetime import date
from typing import Any, ClassVar

import pandas as pd

from app.connectivity.base import CredentialField, EconomicDataProvider, ProviderSpec, SeriesData
from app.core.errors import ConfigurationError, ProviderError

TREASURY_TENORS = {
    "1 Mo": "UST_1M",
    "3 Mo": "UST_3M",
    "6 Mo": "UST_6M",
    "1 Yr": "UST_1Y",
    "2 Yr": "UST_2Y",
    "5 Yr": "UST_5Y",
    "10 Yr": "UST_10Y",
    "20 Yr": "UST_20Y",
    "30 Yr": "UST_30Y",
}


class USTreasuryProvider(EconomicDataProvider):
    spec = ProviderSpec(
        key="us_treasury",
        name="US Treasury — Daily Par Yield Curve",
        category="economics",
        description="Daily Treasury par yield curve rates (1-month to 30-year) published by the US Department of the Treasury.",
        capabilities=("economic_series",),
        auth_type="none",
        requirements=("Internet access",),
        docs_url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates",
        implemented=True,
        verification="live",
        data_class="real_external",
        config_fields=({"name": "years", "label": "Years of history", "type": "int", "default": 5},),
        notes="The 3-month yield can be used as the risk-free rate in analytics.",
    )
    URL = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all"

    def _year(self, year: int) -> pd.DataFrame:
        resp = self.http_get(
            self.URL.format(year=year),
            params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": year, "_format": "csv"},
            headers={"User-Agent": "Mozilla/5.0 (NexisFinance research)"},
        )
        text = resp.text.strip()
        if not text or "," not in text.splitlines()[0]:
            return pd.DataFrame()
        df = pd.read_csv(io.StringIO(text))
        if "Date" not in df.columns:
            raise ProviderError("US Treasury: unexpected CSV layout (no Date column)")
        df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y", errors="coerce")
        return df.dropna(subset=["Date"])

    def verify(self) -> dict[str, Any]:
        df = self._year(date.today().year)
        if df.empty:
            df = self._year(date.today().year - 1)
        if df.empty:
            raise ProviderError("US Treasury: no yield-curve rows returned")
        return {"latest_date": df["Date"].max().date().isoformat(), "rows": len(df)}

    def list_series(self) -> list[str]:
        return list(TREASURY_TENORS.values())

    def fetch_all(self, start: date | None) -> list[SeriesData]:
        years = int(self.config.get("years", 5))
        first = start.year if start else date.today().year - years + 1
        frames = [self._year(y) for y in range(first, date.today().year + 1)]
        df = (
            pd.concat([f for f in frames if not f.empty], ignore_index=True)
            if any(not f.empty for f in frames)
            else pd.DataFrame()
        )
        out = []
        for col, code in TREASURY_TENORS.items():
            if df.empty or col not in df.columns:
                continue
            obs = pd.DataFrame({"date": df["Date"].dt.date, "value": pd.to_numeric(df[col], errors="coerce")}).dropna()
            if start:
                obs = obs[obs["date"] >= start]
            out.append(
                SeriesData(
                    code,
                    f"US Treasury par yield, {col}",
                    "percent",
                    "daily",
                    "United States",
                    obs.sort_values("date").drop_duplicates("date"),
                )
            )
        return out

    def get_series(self, code: str, start: date | None) -> SeriesData:
        for s in self.fetch_all(start):
            if s.code == code:
                return s
        raise ConfigurationError(f"unknown Treasury series {code}")


class WorldBankProvider(EconomicDataProvider):
    DEFAULT: ClassVar[dict[str, str]] = {
        "NY.GDP.MKTP.KD.ZG": "GDP growth (annual %)",
        "FP.CPI.TOTL.ZG": "Inflation, consumer prices (annual %)",
        "SL.UEM.TOTL.ZS": "Unemployment (% of labour force, modelled ILO)",
        "FR.INR.RINR": "Real interest rate (%)",
    }
    spec = ProviderSpec(
        key="world_bank",
        name="World Bank — World Development Indicators",
        category="economics",
        description="Annual macroeconomic indicators (growth, inflation, unemployment, real rates) by country.",
        capabilities=("economic_series",),
        auth_type="none",
        requirements=("Internet access",),
        docs_url="https://datahelpdesk.worldbank.org/knowledgebase/articles/889392",
        implemented=True,
        verification="live",
        data_class="real_external",
        config_fields=({"name": "country", "label": "Country (ISO3)", "type": "str", "default": "USA"},),
    )
    URL = "https://api.worldbank.org/v2/country/{country}/indicator/{code}"

    def _get(self, code: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        country = str(self.config.get("country", "USA")).upper()
        resp = self.http_get(self.URL.format(country=country, code=code), params={"format": "json", "per_page": 200})
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError("World Bank: invalid JSON") from exc
        if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
            msg = payload[0].get("message") if isinstance(payload, list) and payload and isinstance(payload[0], dict) else None
            raise ProviderError(f"World Bank: no data for {code}", details={"provider_message": msg})
        return payload[0], payload[1]

    def verify(self) -> dict[str, Any]:
        meta, rows = self._get("NY.GDP.MKTP.KD.ZG")
        return {"observations": len(rows), "last_updated": (meta or {}).get("lastupdated")}

    def list_series(self) -> list[str]:
        return list(self.config.get("indicators") or self.DEFAULT)

    def get_series(self, code: str, start: date | None) -> SeriesData:
        _, rows = self._get(code)
        obs = pd.DataFrame(
            [
                {"date": date(int(r["date"]), 12, 31), "value": r["value"]}
                for r in rows
                if r.get("value") is not None and str(r.get("date", "")).isdigit()
            ]
        )
        if start is not None and not obs.empty:
            obs = obs[obs["date"] >= start]
        title = rows[0]["indicator"]["value"] if rows else self.DEFAULT.get(code, code)
        country = rows[0]["country"]["value"] if rows else None
        return SeriesData(
            code, title, "percent" if "%" in title else None, "annual", country, obs.sort_values("date") if not obs.empty else obs
        )


class FredProvider(EconomicDataProvider):
    DEFAULT: ClassVar[list[str]] = ["DGS3MO", "DGS10", "T10Y2Y", "CPIAUCSL", "UNRATE", "FEDFUNDS"]
    spec = ProviderSpec(
        key="fred",
        name="FRED — Federal Reserve Economic Data",
        category="economics",
        description="Hundreds of thousands of US and international economic time series from the St. Louis Fed.",
        capabilities=("economic_series",),
        auth_type="api_key",
        requirements=("Free FRED API key (fredaccount.stlouisfed.org)",),
        docs_url="https://fred.stlouisfed.org/docs/api/fred/",
        implemented=True,
        verification="mocked",
        data_class="real_external",
        credential_fields=(CredentialField("api_key", "FRED API key", True, True, "32-character key from your FRED account"),),
        config_fields=({"name": "series", "label": "Series IDs (comma-separated)", "type": "str", "default": ",".join(DEFAULT)},),
        notes="Verified against FRED's documented JSON responses in automated tests; requires your own key to run live.",
    )
    BASE = "https://api.stlouisfed.org/fred"

    def _params(self, **kw: Any) -> dict[str, Any]:
        key = self.credentials.get("api_key")
        if not key:
            raise ConfigurationError("FRED requires an API key")
        return {"api_key": key, "file_type": "json", **kw}

    def verify(self) -> dict[str, Any]:
        data = self.http_get(f"{self.BASE}/series", params=self._params(series_id="DGS10")).json()
        if "seriess" not in data:
            raise ProviderError("FRED: unexpected response", details={"provider_error": data.get("error_message")})
        return {"series_checked": "DGS10"}

    def list_series(self) -> list[str]:
        raw = self.config.get("series") or self.DEFAULT
        return [s.strip().upper() for s in (raw.split(",") if isinstance(raw, str) else raw) if s.strip()]

    def get_series(self, code: str, start: date | None) -> SeriesData:
        meta = self.http_get(f"{self.BASE}/series", params=self._params(series_id=code)).json().get("seriess", [{}])[0]
        obs = self.http_get(
            f"{self.BASE}/series/observations",
            params=self._params(series_id=code, observation_start=start.isoformat() if start else None),
        ).json()
        rows = [
            {"date": pd.Timestamp(o["date"]).date(), "value": float(o["value"])}
            for o in obs.get("observations", [])
            if o.get("value") not in (None, ".", "")
        ]
        return SeriesData(
            code,
            meta.get("title", code),
            meta.get("units_short") or meta.get("units"),
            meta.get("frequency_short") or meta.get("frequency"),
            "United States",
            pd.DataFrame(rows),
        )
