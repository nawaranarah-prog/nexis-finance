"""Automated news pages for InstaFin.

Platform-run accounts (``kind = "page"``) post real articles, videos and charts:

* articles from publisher RSS feeds (Khaleej Times, Gulf News, The National, Yahoo Finance,
  MarketWatch, CNBC, Investing.com, Cointelegraph …), Yahoo Finance ticker news and Google News
  search — headline, publisher, time, a short excerpt and the publisher's lead image, always linking
  to the original article (full text is never copied);
* market videos from the public YouTube channel feeds of CNBC Television, Yahoo Finance and
  Bloomberg Television, played through YouTube's embedded player;
* charts drawn from live prices (see ``chartposts``).

Every page's bio says it is automated. Likes, saves and comments come only from real people.
Refreshing is lazy — the feed triggers it when the last refresh is older than ``STALE_AFTER`` —
and a daily cron call backs it up, so no background worker is needed on serverless hosts.
"""

from __future__ import annotations

import hashlib
import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.markets import feeds
from app.markets import news as news_mod
from app.markets.yahoo import YahooClient
from app.models import Comment, Like, MarketCache, Media, Post, Save, User

log = get_logger(__name__)
STALE_AFTER = timedelta(minutes=20)
KEEP_DAYS = 21
PER_PAGE_PER_REFRESH = 10

# Sources: ("rss", url, publisher, link_filter) · ("yahoo", ticker) · ("google", query) · ("youtube", channel_id, name) · ("charts",)
PAGES: list[dict[str, Any]] = [
    {"username": "uae.markets", "name": "UAE Markets", "color": (14, 116, 144), "badge": "AE",
     "bio": "Automated news page · Dubai & Abu Dhabi markets, companies and the UAE economy, from Khaleej Times, Gulf News, The National and more. Every post links to the original article.",
     "tags": ["uae", "dubai"],
     "sources": [("rss", "https://www.khaleejtimes.com/api/v1/collections/business.rss", "Khaleej Times", None),
                 ("rss", "https://gulfnews.com/api/v1/collections/business.rss", "Gulf News", None),
                 ("rss", "https://www.thenationalnews.com/arc/outboundfeeds/rss/?outputType=xml", "The National", r"/(business|economy|markets|property)/"),
                 ("google", "Dubai Financial Market stocks"), ("google", "Emaar OR \"Emirates NBD\" OR DEWA OR Salik")]},
    {"username": "gulf.business", "name": "Gulf Business", "color": (120, 53, 15), "badge": "GCC",
     "bio": "Automated news page · Saudi, Qatari and wider GCC business, oil and the Gulf economy. Every post links to the original article.",
     "tags": ["gcc", "saudi"],
     "sources": [("rss", "https://www.arabianbusiness.com/feed", "Arabian Business", None), ("google", "Saudi stock market Tadawul"),
                 ("google", "Aramco"), ("google", "Gulf economy")]},
    {"username": "global.markets", "name": "Global Markets", "color": (29, 78, 216), "badge": "GM",
     "bio": "Automated news page · Wall Street, global indices, rates and the big movers, from Yahoo Finance, MarketWatch, CNBC and Investing.com. Every post links to the original article.",
     "tags": ["markets", "stocks"],
     "sources": [("rss", "https://finance.yahoo.com/news/rssindex", "Yahoo Finance", None),
                 ("rss", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "MarketWatch", None),
                 ("rss", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147", "CNBC", None),
                 ("rss", "https://www.investing.com/rss/news.rss", "Investing.com", None)]},
    {"username": "tech.stocks", "name": "Tech Stocks", "color": (91, 33, 182), "badge": "TS",
     "bio": "Automated news page · Big tech, chips and AI companies. Every post links to the original article.",
     "tags": ["tech", "ai"],
     "sources": [("yahoo", "NVDA"), ("yahoo", "AAPL"), ("yahoo", "MSFT"), ("yahoo", "GOOGL"), ("yahoo", "META"), ("yahoo", "AMZN"), ("yahoo", "TSLA")]},
    {"username": "energy.desk", "name": "Energy Desk", "color": (180, 83, 9), "badge": "OIL",
     "bio": "Automated news page · Oil, gas, OPEC and energy companies. Every post links to the original article.",
     "tags": ["oil", "energy"],
     "sources": [("yahoo", "CL=F"), ("yahoo", "XOM"), ("google", "oil prices OPEC")]},
    {"username": "crypto.desk", "name": "Crypto Desk", "color": (202, 138, 4), "badge": "BTC",
     "bio": "Automated news page · Bitcoin, Ethereum and digital assets, from Cointelegraph and Yahoo Finance. Every post links to the original article.",
     "tags": ["crypto", "bitcoin"],
     "sources": [("rss", "https://cointelegraph.com/rss", "Cointelegraph", None), ("yahoo", "BTC-USD")]},
    {"username": "macro.watch", "name": "Macro Watch", "color": (21, 128, 61), "badge": "MW",
     "bio": "Automated news page · Central banks, inflation, interest rates and currencies. Every post links to the original article.",
     "tags": ["macro", "rates"],
     "sources": [("google", "Federal Reserve interest rates"), ("google", "inflation report economy"), ("google", "UAE central bank")]},
    {"username": "market.tv", "name": "Market TV", "color": (190, 18, 60), "badge": "TV",
     "bio": "Automated video page · The latest market videos from CNBC Television, Yahoo Finance and Bloomberg Television, played from YouTube.",
     "tags": ["video", "markets"],
     "sources": [("youtube", "UCrp_UI8XtuYfpiqluWLD7Lw", "CNBC Television"), ("youtube", "UCEAZeUIeJs0IjQiqTCdVSIg", "Yahoo Finance"),
                 ("youtube", "UCIALMKvObZNtJ6AmdCLP7Lg", "Bloomberg Television")]},
    {"username": "nexis.charts", "name": "Nexis Charts", "color": (15, 23, 42), "badge": "📈",
     "bio": "Automated chart page · Daily market charts drawn from live prices: the Dubai market pulse, world indices and a chart of the day.",
     "tags": ["charts"], "sources": [("charts",)]},
]  # fmt: skip

# Company names that map to a tradable symbol, so headlines get live price cards.
NAME_TO_SYMBOL = {
    "emaar development": "EMAARDEV.AE", "emaar": "EMAAR.AE", "emirates nbd": "EMIRATESNBD.AE", "dubai islamic bank": "DIB.AE",
    "dewa": "DEWA.AE", "salik": "SALIK.AE", "air arabia": "AIRARABIA.AE", "parkin": "PARKIN.AE", "talabat": "TALABAT.AE",
    "tecom": "TECOM.AE", "mashreq": "MASQ.AE", "aramex": "ARMX.AE", "dubai taxi": "DTC.AE", "empower": "EMPOWER.AE",
    "dubai financial market": "DFM.AE", "aramco": "2222.SR", "al rajhi": "1120.SR", "saudi national bank": "1180.SR",
    "acwa power": "2082.SR", "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT", "alphabet": "GOOGL", "google": "GOOGL",
    "meta": "META", "amazon": "AMZN", "tesla": "TSLA", "broadcom": "AVGO", "netflix": "NFLX", "exxon": "XOM", "chevron": "CVX",
    "bitcoin": "BTC-USD", "ethereum": "ETH-USD", "s&p 500": "^GSPC", "nasdaq": "^IXIC", "dow jones": "^DJI", "gold": "GC=F",
    "brent": "BZ=F", "oil prices": "CL=F", "jpmorgan": "JPM", "berkshire": "BRK-B", "micron": "MU", "amd": "AMD", "intel": "INTC",
}  # fmt: skip
_NAME_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(NAME_TO_SYMBOL, key=len, reverse=True)) + r")\b", re.I)


def _avatar(page: dict[str, Any]) -> bytes:
    size = 256
    im = Image.new("RGB", (size, size), page["color"])
    d = ImageDraw.Draw(im)
    for r in range(size // 2, 0, -2):  # soft highlight
        a = int(40 * (1 - r / (size / 2)))
        d.ellipse(
            [size / 2 - r - 30, size / 2 - r - 40, size / 2 + r - 30, size / 2 + r - 40],
            fill=tuple(min(255, c + a) for c in page["color"]),
        )
    badge = page["badge"] if page["badge"].isascii() else "NC"
    try:
        import matplotlib

        path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans-Bold.ttf"
        font = ImageFont.truetype(str(path), 92 if len(badge) <= 2 else 70)
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2), badge, fill="white", font=font, anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def ensure_pages(db: Session) -> dict[str, User]:
    out: dict[str, User] = {}
    for page in PAGES:
        u = db.scalars(select(User).where(User.username == page["username"])).first()
        if u is None:
            u = User(
                username=page["username"],
                display_name=page["name"],
                password_hash=None,
                kind="page",
                auth_provider="system",
                bio=page["bio"],
            )
            db.add(u)
            db.flush()
        u.bio, u.display_name = page["bio"], page["name"]
        if u.avatar_media_id is None:
            m = Media(user_id=u.id, content_type="image/jpeg", width=256, height=256, data=_avatar(page))
            db.add(m)
            db.flush()
            u.avatar_media_id = m.id
        out[page["username"]] = u
    db.commit()
    return out


def _key(title: str) -> str:
    return hashlib.sha256(re.sub(r"[^a-z0-9]", "", title.lower())[:120].encode()).hexdigest()[:40]


def _fetch(src: tuple) -> list[dict[str, Any]]:
    kind = src[0]
    if kind == "rss":
        return feeds.rss(src[1], src[2], src[3], limit=12, timeout=9)
    if kind == "youtube":
        return feeds.youtube(src[1], src[2], limit=5, timeout=9)
    if kind == "yahoo":
        items = YahooClient(timeout=8).search(src[1], quotes=0, news=8)["news"]
        return [{**n, "source": "Yahoo Finance"} for n in items]
    if kind == "google":
        q = src[1]
        region = (
            "AE" if any(w in q.lower() for w in ("dubai", "uae", "abu dhabi", "emaar", "emirates", "dewa", "salik")) else "US"
        )
        return news_mod.google_news(q, limit=8, region=region, timeout=8)
    return []


def _symbols_for(item: dict[str, Any]) -> list[str]:
    syms = [s.upper() for s in item.get("related") or [] if re.match(r"^[A-Z0-9^=.\-]{1,15}$", s.upper())]
    syms += [NAME_TO_SYMBOL[m.group(1).lower()] for m in _NAME_RE.finditer(item["title"])]
    return list(dict.fromkeys(syms))[:4]


def _body(item: dict[str, Any], tags: list[str], syms: list[str]) -> str:
    head = ("▶ " if item.get("video_id") else "") + item["title"].strip()
    parts = [head]
    if item.get("summary") and item["summary"].lower()[:60] not in head.lower():
        parts.append(item["summary"])
    extra = " ".join([f"${s}" for s in syms] + [f"#{t}" for t in tags])
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def refresh(db: Session, force: bool = False) -> dict[str, Any]:
    """Import new articles, videos and charts; returns counts. Cheap no-op when refreshed recently."""
    if not get_settings().external_data_enabled:
        return {"skipped": True, "reason": "external data is disabled"}
    state = db.get(MarketCache, "newsfeed:refreshed")
    now = utcnow()
    if not force and state is not None and now - state.fetched_at < STALE_AFTER:
        return {"skipped": True, "refreshed_at": state.fetched_at.isoformat() + "Z"}
    if state is None:  # claim the refresh first so concurrent requests don't all fetch
        state = MarketCache(key="newsfeed:refreshed", payload={"v": {}}, fetched_at=now)
        db.add(state)
    else:
        state.fetched_at = now
    db.commit()
    pages = ensure_pages(db)
    jobs = [(p["username"], src) for p in PAGES for src in p["sources"] if src[0] != "charts"]
    results: dict[str, list[dict[str, Any]]] = {p["username"]: [] for p in PAGES}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_fetch, src): (page, src) for page, src in jobs}
        for f in as_completed(futs):
            page, src = futs[f]
            try:
                results[page] += f.result()
            except ProviderError as exc:
                errors.append(f"{src[1] if len(src) > 1 else src[0]}: {exc.message}")
            except Exception:  # a malformed feed must not break the feed page
                log.exception("news fetch failed for %s", src)
                errors.append(str(src[1] if len(src) > 1 else src[0]))
    existing = set(db.scalars(select(Post.external_key).where(Post.external_key.is_not(None))))
    added = 0
    cutoff = now - timedelta(days=KEEP_DAYS)
    mentions: dict[str, int] = {}
    for page in PAGES:
        user = pages[page["username"]]
        # Prefer items with pictures, then newest, so the feed looks like a feed.
        items = sorted(results[page["username"]], key=lambda n: n.get("published_at") or "", reverse=True)
        n_new = 0
        for it in items:
            if n_new >= PER_PAGE_PER_REFRESH:
                break
            k = _key(it["title"])
            if k in existing or not it.get("url"):
                continue
            try:
                published = (
                    datetime.fromisoformat(it["published_at"]).astimezone(UTC).replace(tzinfo=None)
                    if it.get("published_at")
                    else now
                )
            except ValueError:
                published = now
            if published < cutoff or published > now + timedelta(hours=1):
                continue
            syms = _symbols_for(it)
            for s in syms:
                if not s.startswith("^") and "=" not in s:
                    mentions[s] = mentions.get(s, 0) + 1
            db.add(Post(
                user_id=user.id, body=_body(it, page["tags"], syms), symbols=syms, tags=page["tags"],
                link_url=it["url"][:1500], link_title=it["title"][:500], link_source=(it.get("publisher") or it.get("source") or "")[:160],
                link_image=(it.get("image") or None), external_key=k, created_at=published,
            ))  # fmt: skip
            existing.add(k)
            n_new += 1
            added += 1
    db.commit()
    charts = 0
    try:
        hot = max(mentions, key=lambda s: mentions[s]) if mentions else None
        from app.services import chartposts

        charts = chartposts.publish(db, pages["nexis.charts"], hot)
    except Exception:
        db.rollback()
        log.exception("chart posts failed")
    removed = _prune(db, cutoff)
    state.payload = {"v": {"added": added, "charts": charts, "errors": errors[:20], "removed": removed}}
    db.commit()
    return {"skipped": False, "added": added, "charts": charts, "errors": errors, "removed": removed}


def _prune(db: Session, cutoff: datetime) -> int:
    """Drop old page posts nobody interacted with (keeps the database small)."""
    page_ids = select(User.id).where(User.kind == "page")
    engaged = select(Like.post_id).union(select(Comment.post_id), select(Save.post_id))
    old = list(db.scalars(select(Post).where(Post.user_id.in_(page_ids), Post.created_at < cutoff, Post.id.not_in(engaged))))
    for p in old:
        if p.media_id:
            db.query(Media).filter(Media.id == p.media_id).delete()
        db.delete(p)
    db.commit()
    return len(old)
