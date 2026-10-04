"""Where Nexis Pulse evidence comes from: one connector per source.

Every connector turns real, verifiable information about one asset into ``EvidenceItem`` rows — title, link,
publisher and publication time exactly as the source gave them, never the full text of an article. Connectors only
run when :meth:`SourceConnector.available` says Nexis has legitimate access; the rest are registered so the
interface can say what is (and isn't) connected.

Connected today (no paid service, no API key):

* ``market_data`` — delayed quotes and daily history from the existing market-data providers;
* ``news_search`` — per-asset headlines from Yahoo Finance's search API and Google News RSS search (the same feeds
  the Markets pages already use);
* ``sec_edgar`` — recent 8-K / 10-Q / 10-K / 20-F / 6-K filings from SEC EDGAR's free public API (US issuers).

Registered but not connected: ``reddit`` and ``x``. They never fetch: Reddit needs an approved API application
and X needs a paid API plan. To add one when access exists:

1. implement ``fetch`` to call the official API with the configured credentials, honouring its rate limits;
2. return ``EvidenceItem(kind="social", ...)`` with a link to the public post and its real timestamp — summarise
   the post, don't copy it, and never store or display account names as if they were Nexis participants;
3. make ``available`` return ``True`` only when the credentials are configured.

Nothing else changes: the pipeline in ``market_pulse`` stores, deduplicates, scores and cites whatever connectors
return. Social items get their own source weight in ``pulse_analysis.SOURCE_WEIGHT``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError

# 8-K items worth naming (https://www.sec.gov/files/form8-k.pdf). 9.01 (exhibits) is omitted on purpose.
EIGHT_K_ITEMS = {
    "1.01": "material agreement", "1.02": "terminated agreement", "1.03": "bankruptcy or receivership",
    "2.01": "acquisition or disposition", "2.02": "results of operations (earnings)", "2.03": "new financial obligation",
    "2.05": "restructuring costs", "2.06": "material impairment", "3.01": "listing notice", "4.01": "auditor change",
    "4.02": "non-reliance on prior financials", "5.01": "change in control", "5.02": "executive or director change",
    "5.03": "charter or bylaw amendment", "5.07": "shareholder vote results", "7.01": "Regulation FD disclosure",
    "8.01": "other events",
}  # fmt: skip
FORMS = {"8-K": "Current report", "10-Q": "Quarterly report", "10-K": "Annual report", "20-F": "Annual report (foreign issuer)",
         "6-K": "Report of foreign issuer"}  # fmt: skip


@dataclass
class AssetRef:
    symbol: str
    name: str
    quote: dict[str, Any] | None = None


@dataclass
class EvidenceItem:
    """A normalised piece of evidence, before it is stored."""

    provider: str
    kind: str  # news | filing | market_data | social
    title: str
    published_at: datetime  # naive UTC, as published by the source
    external_key: str
    url: str | None = None
    publisher: str | None = None
    summary: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    # Stored items are SourceEvents; live market data is analysed but not stored.
    persist: bool = True


class SourceConnector:
    key = ""
    label = ""
    kind = ""
    cost = "free"
    requires = ""

    def available(self) -> tuple[bool, str]:
        return False, "not implemented"

    def fetch(self, db: Session, asset: AssetRef) -> list[EvidenceItem]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        ok, note = self.available()
        return {"key": self.key, "label": self.label, "kind": self.kind, "connected": ok, "cost": self.cost,
                "requires": self.requires, "note": note}  # fmt: skip


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def _parse_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return _naive(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except ValueError:
        return None


def title_key(title: str) -> str:
    """Normalised headline for de-duplication across feeds ("Tesla beats - Reuters" == "Tesla beats")."""
    return re.sub(r"[^a-z0-9]", "", title.lower())[:80]


# ------------------------------------------------------------------ connected


class MarketDataConnector(SourceConnector):
    key, label, kind = "market_data", "Market data (delayed quotes and daily history)", "market"
    requires = "Existing market-data providers (Yahoo Finance, TradingView, UAE feeds)"

    def available(self) -> tuple[bool, str]:
        return True, "Delayed quotes; used for the latest session move and the one-month trend."

    def fetch(self, db: Session, asset: AssetRef) -> list[EvidenceItem]:
        from app.services import markets

        out: list[EvidenceItem] = []
        q = asset.quote or {}
        when = _parse_time(q.get("market_time"))
        if q.get("change_pct") is not None and q.get("price") is not None:
            pct = float(q["change_pct"]) * 100
            out.append(EvidenceItem(
                provider=self.key, kind="market_data", persist=False,
                title=f"{asset.name} {'rose' if pct >= 0 else 'fell'} {abs(pct):.2f}% in the latest session",
                published_at=when or datetime.now(UTC).replace(tzinfo=None), publisher="Market data (delayed)",
                external_key=f"mkt:{asset.symbol}:1d", url=f"/markets/{asset.symbol}",
                facts={"metric": "1d", "change_pct": round(pct, 2), "price": q["price"], "currency": q.get("currency"),
                       "time_known": when is not None},
            ))  # fmt: skip
        try:
            h = markets.history(db, asset.symbol, "1d", (datetime.now(UTC) - timedelta(days=31)).date(), None)
            closes = [b for b in h["bars"] if b.get("close") is not None]
        except NexisError:
            closes = []
        if len(closes) >= 10:
            first, last = closes[0], closes[-1]
            ret = (last["close"] / first["close"] - 1) * 100 if first["close"] else None
            if ret is not None:
                out.append(EvidenceItem(
                    provider=self.key, kind="market_data", persist=False,
                    title=f"{asset.name} is {'up' if ret >= 0 else 'down'} {abs(ret):.1f}% over the past month",
                    published_at=_parse_time(last.get("t")) or datetime.now(UTC).replace(tzinfo=None),
                    publisher="Market data (delayed)", external_key=f"mkt:{asset.symbol}:1mo", url=f"/markets/{asset.symbol}",
                    facts={"metric": "1mo", "change_pct": round(ret, 2), "from": str(first.get("t"))[:10], "to": str(last.get("t"))[:10],
                           "bars": len(closes)},
                ))  # fmt: skip
        return out


class NewsConnector(SourceConnector):
    key, label, kind = "news_search", "Financial news (Yahoo Finance, Google News)", "news"
    requires = "Public news feeds the Markets pages already use (no key)"

    def available(self) -> tuple[bool, str]:
        return True, "Headlines, publishers, times and links. Articles are linked, never copied."

    def fetch(self, db: Session, asset: AssetRef) -> list[EvidenceItem]:
        from app.markets import news as news_mod
        from app.services import markets

        value, _ = markets.cached(db, f"news:{asset.symbol}", timedelta(minutes=20),
                                  lambda: news_mod.instrument_news(asset.symbol, asset.name, limit=25))  # fmt: skip
        out = []
        for n in value.get("items") or []:
            when = _parse_time(n.get("published_at"))
            title = str(n.get("title") or "").strip()
            if not title or not n.get("url") or when is None:
                continue  # without a link and a real publication time it can't be cited
            out.append(EvidenceItem(
                provider=self.key, kind="news", title=title[:500], url=str(n["url"])[:1500],
                publisher=(n.get("publisher") or n.get("source") or None), published_at=when,
                external_key="ns:" + hashlib.sha1(str(n["url"]).encode()).hexdigest()[:32],
                facts={"feed": n.get("source")},
            ))  # fmt: skip
        return out


class FilingConnector(SourceConnector):
    key, label, kind = "sec_edgar", "SEC EDGAR filings", "filings"
    requires = "Free public SEC API with a descriptive User-Agent (NEXIS_SEC_USER_AGENT); US-listed issuers only"

    def available(self) -> tuple[bool, str]:
        if not get_settings().external_data_enabled:
            return False, "External public data sources are switched off (NEXIS_EXTERNAL_DATA_ENABLED=false)."
        return True, "Filing type, date and link for US-listed companies."

    @staticmethod
    def covers(symbol: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", symbol))

    def fetch(self, db: Session, asset: AssetRef) -> list[EvidenceItem]:
        from app.connectivity.providers.sec import SecEdgarProvider
        from app.services import markets

        if not self.covers(asset.symbol):
            return []

        def load() -> list[dict[str, Any]]:
            sec = SecEdgarProvider()
            cik = sec.ticker_map().get(asset.symbol)
            if cik is None:
                return []
            recent = (sec._get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").get("filings") or {}).get("recent") or {}
            rows = []
            for i, form in enumerate(recent.get("form") or []):
                if form not in FORMS:
                    continue
                acc = recent["accessionNumber"][i]
                rows.append({"form": form, "accession": acc, "accepted": recent.get("acceptanceDateTime", [None] * (i + 1))[i],
                             "filed": recent["filingDate"][i], "items": (recent.get("items") or [""] * (i + 1))[i] or "",
                             "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{recent['primaryDocument'][i]}"})  # fmt: skip
                if len(rows) >= 12:
                    break
            return rows

        rows, _ = markets.cached(db, f"sec-filings:{asset.symbol}", timedelta(hours=6), load)
        out = []
        for r in rows:
            when = _parse_time(r.get("accepted")) or _parse_time(r.get("filed"))
            if when is None:
                continue
            items = [EIGHT_K_ITEMS[x] for x in str(r.get("items") or "").split(",") if x.strip() in EIGHT_K_ITEMS]
            what = f"{FORMS[r['form']]}" + (f": {', '.join(items)}" if items else "")
            out.append(EvidenceItem(
                provider=self.key, kind="filing", title=f"{asset.name} filed a {r['form']} ({what})"[:500], url=r["url"],
                publisher="SEC EDGAR", published_at=when, external_key=f"sec:{r['accession']}",
                facts={"form": r["form"], "items": items, "filed": r.get("filed")},
            ))  # fmt: skip
        return out


# ------------------------------------------------------------------ registered, not connected


class RedditConnector(SourceConnector):
    key, label, kind, cost = "reddit", "Reddit", "social", "free tier after approval"
    requires = "An approved Reddit Data API application and its credentials"

    def available(self) -> tuple[bool, str]:
        return False, "Not connected: needs an approved Reddit API application. Reddit is never scraped."


class XConnector(SourceConnector):
    key, label, kind, cost = "x", "X", "social", "paid API plan"
    requires = "An X API plan with search access"

    def available(self) -> tuple[bool, str]:
        return False, "Not connected: X search requires a paid API plan, which Nexis does not use. X is never scraped."


CONNECTORS: dict[str, SourceConnector] = {
    c.key: c for c in (MarketDataConnector(), NewsConnector(), FilingConnector(), RedditConnector(), XConnector())
}


def active() -> list[SourceConnector]:
    return [c for c in CONNECTORS.values() if c.available()[0]]


def describe() -> list[dict[str, Any]]:
    return [c.describe() for c in CONNECTORS.values()]
