"""Reddit discussions in Nexis Pulse, the authorised no-cost way: links shared by members, shown with Reddit's embed.

Why this shape (reviewed against Reddit's terms, October 2026):

* Reddit's **Developer Terms** §4.1 (rev. 24 Mar 2026) forbid accessing Reddit data "by or on behalf of a business or as
  part of a service or product that is monetized" without Reddit's written approval, and the **Data API Terms** §3.1
  (rev. 20 Jul 2026) require a separate agreement for commercial purposes. Nexis sells subscriptions, so collecting
  Reddit content through the Data API — even to show it free — needs that agreement first.
* The **User Agreement** (rev. 26 May 2026) prohibits scraping without prior written consent and only permits crawling
  within robots.txt, which disallows everything (``Disallow: /``). That rules out the old RSS collector.
* The **Embeds Terms** (rev. 18 Apr 2023) let any app use Reddit Embeds "for making User Content visible on your App",
  provided the embed isn't modified, doesn't imply partnership, isn't used in ads, and isn't used to sell access to
  Reddit data. Reddit serves the content itself, so posts removed on Reddit disappear from the embed too.

So Nexis never fetches, stores, copies or analyses Reddit content here. A member pastes a link to a Reddit post; Nexis
keeps only the link (validated and normalised, never requested by the server) and the reader's browser loads Reddit's
official embed on request. It's free for every plan. Automated discovery of Reddit threads stays off
(``public_discussions``, ``pulse_public_reddit``) until Reddit grants commercial Data API access.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

PUBLISHER = "Reddit"
_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "m.reddit.com"}
_PATH = re.compile(r"^/r/([A-Za-z0-9_]{2,21})/comments/([a-z0-9]{3,12})(?:/([^/?#]{0,120}))?(?:/([a-z0-9]{3,12}))?/?$")
_IN_TEXT = re.compile(
    r"https?://(?:www\.|old\.|new\.|np\.|m\.)?reddit\.com/r/[A-Za-z0-9_]{2,21}/comments/[a-z0-9]{3,12}[^\s)\]>\"']*", re.I
)


def parse(url: str | None) -> dict[str, Any] | None:
    """A Reddit post (or comment) URL → its canonical form, or None. Short links (redd.it, /s/) aren't accepted:
    resolving them would mean requesting Reddit from our servers."""
    if not url or len(url) > 600:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in _HOSTS or parts.username or parts.password:
        return None
    m = _PATH.match(parts.path)
    if not m:
        return None
    sub, post, slug, comment = m.group(1), m.group(2), m.group(3) or "", m.group(4)
    base = f"https://www.reddit.com/r/{sub}/comments/{post}/{slug + '/' if slug else ''}"
    canonical = base + (f"{comment}/" if comment and slug else "")
    return {"url": canonical, "subreddit": sub, "post_id": post, "comment_id": comment if slug else None,
            "label": f"r/{sub}", "title": f"Discussion on Reddit · r/{sub}"}  # fmt: skip


def first_in(text: str | None) -> dict[str, Any] | None:
    for m in _IN_TEXT.finditer(text or ""):
        hit = parse(m.group(0).rstrip(".,;:!?"))
        if hit:
            return hit
    return None


def from_sources(sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The shared Reddit link of a discussion, from its stored sources (only the URL is stored)."""
    for s in sources:
        if s.get("publisher") == PUBLISHER:
            hit = parse(s.get("url"))
            if hit:
                return hit
    return None
