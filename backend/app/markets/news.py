"""Recent news for an instrument: Yahoo Finance's news feed merged with Google News search (RSS).

Google News covers regional outlets (Gulf News, Khaleej Times, The National, Arabian Business, Zawya)
that Yahoo's feed usually misses for Gulf-listed companies. Only headline, source, link and time are
kept — article bodies are not scraped.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.core.errors import ProviderError
from app.markets.yahoo import UA, YahooClient

_SUFFIX = re.compile(
    r"\b(PJSC|P\.J\.S\.C\.?|plc|Inc\.?|Corp(oration)?\.?|Ltd\.?|Limited|S\.A\.|AG|N\.V\.|Holding(s)?|Company|Co\.)\s*$", re.I
)


def clean_company_name(name: str) -> str:
    n = name
    for _ in range(3):
        n = _SUFFIX.sub("", n).strip(" ,.-")
    return n or name


def google_news(query: str, limit: int = 12, region: str = "US", timeout: float = 12.0) -> list[dict[str, Any]]:
    lang = "en-AE" if region == "AE" else "en-US"
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}+when:30d&hl={lang}&gl={region}&ceid={region}:en"
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ProviderError(f"news request failed ({exc.__class__.__name__})") from exc
    if r.status_code != 200:
        raise ProviderError(f"news provider returned HTTP {r.status_code}")
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as exc:
        raise ProviderError("unexpected news feed format") from exc
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        src = item.find("source")
        publisher = src.text.strip() if src is not None and src.text else None
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(UTC).isoformat()
        except (TypeError, ValueError):
            ts = None
        link = item.findtext("link")
        if title and link:
            out.append({"title": title, "publisher": publisher, "url": link, "published_at": ts, "source": "Google News"})
        if len(out) >= limit:
            break
    return out


def instrument_news(symbol: str, name: str | None, limit: int = 15) -> dict[str, Any]:
    """Merged, de-duplicated headlines, newest first, with a note of which feeds answered."""
    items: list[dict[str, Any]] = []
    feeds: dict[str, str] = {}
    try:
        for n in YahooClient().search(symbol, quotes=0, news=10)["news"]:
            items.append({**n, "source": "Yahoo Finance"})
        feeds["Yahoo Finance"] = "ok"
    except ProviderError as exc:
        feeds["Yahoo Finance"] = exc.message
    if name:
        q = f'"{clean_company_name(name)}"'
        try:
            items += google_news(q, limit=limit, region="AE" if symbol.upper().endswith(".AE") else "US")
            feeds["Google News"] = "ok"
        except ProviderError as exc:
            feeds["Google News"] = exc.message
    seen: set[str] = set()
    uniq = []
    for n in items:
        k = re.sub(r"[^a-z0-9]", "", n["title"].lower())[:80]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(n)
    uniq.sort(key=lambda n: n.get("published_at") or "", reverse=True)
    return {"items": uniq[:limit], "feeds": feeds, "retrieved_at": datetime.now(UTC).isoformat()}
