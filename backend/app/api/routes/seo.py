"""Sitemap for search engines: the main pages, every UAE share and bond, and recent Finstagram posts."""

from __future__ import annotations

import logging
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NexisError
from app.db.session import get_db
from app.models.community import Post
from app.services import uae

router = APIRouter(tags=["seo"])
log = logging.getLogger(__name__)

SITE = "https://nexis-finance-five.vercel.app"
PAGES = [
    ("/", "hourly", "1.0"),
    ("/finstagram", "hourly", "0.9"),
    ("/advisor", "weekly", "0.9"),
    ("/markets", "hourly", "0.9"),
    ("/markets?list=uae", "hourly", "0.8"),
    ("/markets?list=uae_bonds", "daily", "0.8"),
    ("/compare", "weekly", "0.7"),
    ("/valuation", "weekly", "0.7"),
    ("/login", "monthly", "0.3"),
]


@router.get("/seo/sitemap.xml", include_in_schema=False)
def sitemap(db: Session = Depends(get_db)) -> Response:
    urls: list[tuple[str, str, str]] = list(PAGES)
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
