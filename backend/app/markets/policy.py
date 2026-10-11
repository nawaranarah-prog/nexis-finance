"""What Nexis may republish from each news source (owner decision 2026-10-11: zero cost, and no use or redistribution
that the source's terms don't permit). See DATA-SOURCES.md and docs/LEGAL-REVIEW.md.

* **blocked**: terms prohibit it or no permission was found. Not fetched, not posted; earlier posts hidden.
  - Investing.com (its terms prohibit reuse explicitly)
  - Yahoo Finance news and RSS (no licence for commercial reuse)
  - Google News (feeds are for personal, non-commercial use)
  - Nexis chart posts drawn from Yahoo/TradingView prices (republishing unlicensed price data)
* **restricted**: publisher RSS whose commercial-reuse permission isn't verified. Only the headline, the publisher and
  a link to the original are shown: no excerpt, no hotlinked image.
* **permitted**: official embeds (YouTube) and official APIs.

Setting ``NEXIS_NEWS_POLICY=licensed`` lifts the restrictions — only once the owner holds the licences.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import get_settings

BLOCKED_HOSTS = ("news.google.com", "finance.yahoo.com", "investing.com", "uk.investing.com")
BLOCKED_PUBLISHERS = {"investing.com", "yahoo finance", "google news"}
BLOCKED_KINDS = {"yahoo", "google", "charts"}  # newsfeed source kinds
CHART_PAGE = "nexis.charts"
PERMITTED_HOSTS = ("youtube.com", "www.youtube.com", "youtu.be")


def strict() -> bool:
    return (get_settings().news_policy or "strict").lower() != "licensed"


def _host(url: str | None) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def blocked_url(url: str | None) -> bool:
    h = _host(url)
    return strict() and any(h == b or h.endswith("." + b) for b in BLOCKED_HOSTS)


def blocked_publisher(name: str | None) -> bool:
    return strict() and (name or "").strip().lower() in BLOCKED_PUBLISHERS


def blocked_kind(kind: str) -> bool:
    return strict() and kind in BLOCKED_KINDS


def full_preview(url: str | None) -> bool:
    """May the excerpt and image be shown? Only for permitted sources (or once licensed)."""
    if not strict():
        return True
    h = _host(url)
    return any(h == p or h.endswith("." + p) for p in PERMITTED_HOSTS)
