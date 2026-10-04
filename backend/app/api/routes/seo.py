"""Search engines: the sitemap, and server-rendered page heads for public Pulse discussions.

The web app is a single-page app, so link previews and crawlers that don't run JavaScript would otherwise see the
same generic title on every page. ``/seo/render/pulse/d/...`` returns the app's own ``index.html`` with the
discussion's title, description, canonical URL, Open Graph tags and ``DiscussionForumPosting`` structured data in
the head; the app then loads as usual. Private pages (My Nexis, notifications, settings) are never listed here.
"""

from __future__ import annotations

import json
import logging
import re
import time
from html import escape as html_escape
from urllib.parse import quote
from xml.sax.saxutils import escape

import httpx
from fastapi import APIRouter, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError, NotFoundError
from app.db.session import get_db
from app.models import PulseDiscussion
from app.models.community import Post
from app.services import pulse, uae

router = APIRouter(tags=["seo"])
log = logging.getLogger(__name__)

SITE = get_settings().public_site_url.rstrip("/")
PAGES = [
    ("/", "hourly", "1.0"),
    ("/pulse", "hourly", "0.9"),
    ("/finstagram", "hourly", "0.9"),
    ("/advisor", "weekly", "0.9"),
    ("/markets", "hourly", "0.9"),
    ("/markets?list=uae", "hourly", "0.8"),
    ("/markets?list=uae_bonds", "daily", "0.8"),
    ("/compare", "weekly", "0.7"),
    ("/valuation", "weekly", "0.7"),
    ("/terms", "monthly", "0.2"),
    ("/privacy", "monthly", "0.2"),
    ("/disclaimer", "monthly", "0.2"),
    ("/community-guidelines", "monthly", "0.3"),
    ("/ai-disclosure", "monthly", "0.2"),
    ("/login", "monthly", "0.3"),
]


@router.get("/seo/sitemap.xml", include_in_schema=False)
def sitemap(db: Session = Depends(get_db)) -> Response:
    urls: list[tuple[str, str, str]] = list(PAGES)
    urls += [(f"/pulse/topic/{t}", "daily", "0.6") for t in pulse.PRIMARY_TOPICS]
    for d in db.scalars(select(PulseDiscussion).where(PulseDiscussion.status == "visible").order_by(PulseDiscussion.last_activity_at.desc()).limit(1000)):
        urls.append((pulse.url_for(d), "hourly" if d.kind == "editorial" else "daily", "0.7" if d.kind == "editorial" else "0.6"))
    for sym in dict.fromkeys(db.scalars(select(PulseDiscussion.primary_symbol).where(PulseDiscussion.status == "visible",
                                                                                      PulseDiscussion.primary_symbol.is_not(None)).limit(500))):  # fmt: skip
        urls.append((f"/pulse/asset/{quote(sym, safe='')}", "daily", "0.6"))
    try:
        for row in uae.universe(db):
            sym = quote(row["symbol"], safe="")
            urls.append((f"/markets/{sym}", "daily", "0.7"))
            urls.append((f"/valuation/{sym}", "weekly", "0.5"))
    except NexisError as exc:
        log.warning("sitemap: UAE shares unavailable: %s", exc)
    try:
        urls += [(f"/markets/{quote(b['symbol'], safe='')}", "daily", "0.6") for b in uae.bonds(db)]
    except NexisError as exc:
        log.warning("sitemap: UAE bonds unavailable: %s", exc)
    posts = db.scalars(select(Post.id).where(Post.hidden.is_(False)).order_by(Post.created_at.desc()).limit(500))
    urls += [(f"/finstagram/p/{pid}", "weekly", "0.4") for pid in posts]

    body = "".join(
        f"<url><loc>{escape(SITE + path)}</loc><changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
        for path, freq, prio in dict.fromkeys(urls)
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


# ------------------------------------------------------------------ server-rendered heads for Pulse discussions

_SHELL: dict[str, tuple[float, str]] = {}
_SHELL_TTL = 30.0


def _shell() -> str:
    """The deployed app's index.html (cached briefly so a new frontend deploy is picked up within seconds)."""
    hit = _SHELL.get("v")
    if hit and time.monotonic() - hit[0] < _SHELL_TTL:
        return hit[1]
    r = httpx.get(f"{SITE}/index.html", timeout=4.0, headers={"user-agent": "NexisFinance-SEO/1.0"})
    r.raise_for_status()
    if "<head" not in r.text or 'id="root"' not in r.text:
        raise ValueError("unexpected index.html")
    _SHELL["v"] = (time.monotonic(), r.text)
    return r.text


def head_for(d: PulseDiscussion, comment_count: int) -> tuple[str, str, str]:
    """(title, description, extra head HTML) for one discussion."""
    editorial = d.kind == "editorial"
    url = SITE + pulse.url_for(d)
    asset = f" ({d.primary_symbol})" if d.primary_symbol else ""
    title = f"{d.title}{asset} · Nexis Pulse"
    lead = (d.what_happened or d.body) if editorial else d.body
    desc = re.sub(r"\s+", " ", lead or "").strip()
    desc = (desc[:157] + "…") if len(desc) > 158 else desc
    if not desc:
        desc = "Nexis editorial context and the community debate." if editorial else "An anonymous discussion on Nexis Pulse."
    ld = {
        "@context": "https://schema.org", "@type": "DiscussionForumPosting", "headline": d.title[:110], "url": url,
        "text": desc, "datePublished": d.created_at.isoformat() + "Z",
        "dateModified": (d.content_updated_at or d.last_activity_at or d.created_at).isoformat() + "Z",
        "author": {"@type": "Organization", "name": "Nexis", "url": SITE} if editorial else {"@type": "Person", "name": "Anonymous"},
        "interactionStatistic": {"@type": "InteractionCounter", "interactionType": "https://schema.org/CommentAction", "userInteractionCount": comment_count},
        "isPartOf": {"@type": "WebSite", "name": "Nexis Finance", "url": SITE + "/"},
    }  # fmt: skip
    if d.primary_symbol:
        ld["about"] = {"@type": "Thing", "name": d.asset_name or d.primary_symbol}
    e = html_escape
    ld_json = json.dumps(ld).replace("</", "<\\/")  # text can never close the script tag
    extra = (
        '<meta property="og:type" content="article" />'
        f'<meta property="article:published_time" content="{e(ld["datePublished"])}" />'
        f'<meta property="article:modified_time" content="{e(ld["dateModified"])}" />'
        f'<script type="application/ld+json">{ld_json}</script>'
    )
    return title, desc, extra


def _inject(shell: str, title: str, desc: str, url: str, extra: str, robots: str = "index, follow, max-image-preview:large") -> str:
    e = html_escape
    swaps = [
        (r"<title>.*?</title>", f"<title>{e(title)}</title>"),
        (r'<meta name="description" content="[^"]*"\s*/?>', f'<meta name="description" content="{e(desc)}" />'),
        (r'<meta name="robots" content="[^"]*"\s*/?>', f'<meta name="robots" content="{e(robots)}" />'),
        (r'<link rel="canonical" href="[^"]*"\s*/?>', f'<link rel="canonical" href="{e(url)}" />'),
        (r'<meta property="og:type" content="[^"]*"\s*/?>', ""),
        (r'<meta property="og:title" content="[^"]*"\s*/?>', f'<meta property="og:title" content="{e(title)}" />'),
        (r'<meta property="og:description" content="[^"]*"\s*/?>', f'<meta property="og:description" content="{e(desc)}" />'),
        (r'<meta property="og:url" content="[^"]*"\s*/?>', f'<meta property="og:url" content="{e(url)}" />'),
        (r'<meta name="twitter:title" content="[^"]*"\s*/?>', f'<meta name="twitter:title" content="{e(title)}" />'),
        (r'<meta name="twitter:description" content="[^"]*"\s*/?>', f'<meta name="twitter:description" content="{e(desc)}" />'),
    ]
    out = shell
    for rx, rep in swaps:
        out = re.sub(rx, lambda _m, r=rep: r, out, count=1, flags=re.S)
    return out.replace("</head>", f"{extra}</head>", 1)


@router.get("/seo/render/pulse/d/{pid}", include_in_schema=False)
@router.get("/seo/render/pulse/d/{pid}/{slug}", include_in_schema=False)
def render_discussion(pid: str, slug: str | None = None, db: Session = Depends(get_db)) -> Response:
    fallback = RedirectResponse(f"/pulse/d/{quote(pid, safe='')}{'/' + quote(slug, safe='') if slug else ''}?spa=1", status_code=302)
    try:
        shell = _shell()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("seo render: app shell unavailable: %s", exc)
        return fallback  # the app itself serves the page without the enhanced head
    try:
        d = pulse.get_discussion(db, pid)
    except NotFoundError:
        html = _inject(shell, "Discussion not found · Nexis Pulse", "This discussion is not available.", f"{SITE}/pulse", "", robots="noindex")
        return HTMLResponse(html, status_code=404)
    title, desc, extra = head_for(d, d.comment_count)
    html = _inject(shell, title, desc, SITE + pulse.url_for(d), extra)
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=60, s-maxage=60"})
