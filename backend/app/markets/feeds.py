"""Publisher RSS/Atom feeds and YouTube channel feeds.

Each item keeps the headline, link, publisher, time, a short plain-text excerpt (at most ~280
characters, as feed readers show) and the lead image the publisher put in the feed. Full article
text is never copied — posts link to the original.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.errors import ProviderError
from app.markets.yahoo import UA

NS = {
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}
_TAG = re.compile(r"<[^>]+>")
_IMG = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def _get(url: str, timeout: float) -> bytes:
    try:
        r = httpx.get(
            url,
            headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"feed request failed ({exc.__class__.__name__})") from exc
    if r.status_code != 200:
        raise ProviderError(f"feed returned HTTP {r.status_code}")
    return r.content


def excerpt(raw: str | None, limit: int = 280) -> str | None:
    if not raw:
        return None
    text = html.unescape(_TAG.sub(" ", raw))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 30:
        return None
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:.-")
        text = cut + "…"
    return text


def _when(item: ET.Element) -> str | None:
    for tag in ("pubDate", "{http://purl.org/dc/elements/1.1/}date", "atom:updated", "atom:published"):
        v = item.findtext(tag, namespaces=NS)
        if not v:
            continue
        try:
            dt = parsedate_to_datetime(v) if "," in v or v[:3].isalpha() else datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.astimezone(UTC).isoformat()
        except (TypeError, ValueError):
            continue
    return None


def _image(item: ET.Element) -> str | None:
    best, best_w = None, -1
    for el in (
        item.findall("media:content", NS) + item.findall("media:thumbnail", NS) + item.findall("media:group/media:content", NS)
    ):
        url = el.get("url") or ""
        medium, typ = el.get("medium") or "", el.get("type") or ""
        if not url.startswith("https://") or (medium and medium != "image") or (typ and not typ.startswith("image")):
            continue
        w = int(el.get("width") or 0)
        if best is None or (w > best_w and w <= 2000):
            best, best_w = url, w
    if best:
        return best
    enc = item.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image") and (enc.get("url") or "").startswith("https://"):
        return enc.get("url")
    for tag in ("content:encoded", "description"):
        m = _IMG.search(item.findtext(tag, namespaces=NS) or "")
        if m and m.group(1).startswith("https://"):
            return html.unescape(m.group(1))
    return None


def rss(url: str, publisher: str, link_filter: str | None = None, limit: int = 12, timeout: float = 10.0) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(_get(url, timeout))
    except ET.ParseError as exc:
        raise ProviderError("unexpected feed format") from exc
    out = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        if link_filter and not re.search(link_filter, link):
            continue
        out.append({
            "title": title,
            "url": link,
            "publisher": publisher,
            "source": publisher,
            "published_at": _when(item),
            "summary": excerpt(item.findtext("description") or item.findtext("content:encoded", namespaces=NS)),
            "image": _image(item),
        })  # fmt: skip
        if len(out) >= limit:
            break
    return out


def youtube(channel_id: str, channel: str, limit: int = 6, timeout: float = 10.0) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", timeout))
    except ET.ParseError as exc:
        raise ProviderError("unexpected video feed format") from exc
    out = []
    for e in root.findall("atom:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        title = e.findtext("atom:title", namespaces=NS) or ""
        if not vid or not title:
            continue
        if "#shorts" in title.lower():
            continue
        published = e.findtext("atom:published", namespaces=NS)
        desc = e.findtext("media:group/media:description", namespaces=NS)
        out.append({
            "title": html.unescape(title.strip()),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "publisher": channel,
            "source": channel,
            "published_at": datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(UTC).isoformat() if published else None,
            "summary": excerpt((desc or "").split("\n\n")[0], 220),
            "image": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "video_id": vid,
        })  # fmt: skip
        if len(out) >= limit:
            break
    return out
