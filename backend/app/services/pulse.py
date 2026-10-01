"""Nexis Pulse: what investors are saying about an asset on Bluesky, StockTwits, Nexis itself, Reddit and X.

Everything shown comes from the sources' own APIs and links back to the original post and author:

* Reddit — the official API (app-only OAuth) with an app Reddit has approved under its Responsible Builder
  Policy (``NEXIS_REDDIT_CLIENT_ID``/``SECRET``). Without approval Reddit is not read at all, unless
  ``NEXIS_REDDIT_PUBLIC_FEED`` is explicitly enabled.
* X — recent search when ``NEXIS_X_BEARER_TOKEN`` is set (an X API plan with search access is required).
* Bluesky — the open public search API (app.bsky.feed.searchPosts).
* Nexis members — Finstagram posts by people (not news pages) that tag the asset.
* StockTwits — the public symbol stream (US-listed tickers and crypto). Bullish/Bearish labels on StockTwits
  are tags the posters chose themselves.

The AI synthesis only summarises the collected posts, cites them by number, and is labelled as a summary
of opinions. Nothing here is a measure of total discussion volume or a forecast.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError, ProviderError
from app.services import llm, markets


def _q(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


log = logging.getLogger(__name__)

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
API_UA = "web:nexis-finance:1.0 (by /u/nexisfinance)"
TTL = timedelta(minutes=10)
DISCLAIMER = (
    "Posts are personal opinions from public social media and Nexis members, collected automatically and not verified by Nexis. "
    "Popularity and sentiment are not evidence that a price will rise or fall."
)
_NAME_NOISE = re.compile(
    r"\b(p\.?j\.?s\.?c\.?|pjsc|plc|inc\.?|corp\.?|corporation|company|co\.?|ltd\.?|limited|holdings?|group|s\.?a\.?|n\.?v\.?|ag|the|class [a-z])\b|[(),.]",
    re.I,
)
_GENERIC = {"first", "national", "bank", "united", "general", "international", "american", "emirates", "dubai", "abu", "gulf",
            "arab", "global", "commercial", "islamic", "investment", "energy", "real", "property", "properties", "capital", "new"}  # fmt: skip


# ------------------------------------------------------------------ subject


def subject(db: Session, symbol: str) -> dict[str, Any]:
    """Ticker, display name and the words people actually use for this asset."""
    sym = markets.clean_symbol(symbol)
    try:
        q = (markets.quotes(db, [sym]) or [{}])[0]
    except NexisError:
        q = {}
    name = q.get("name") or sym
    base = re.sub(r"[.\-=^].*$", "", sym.lstrip("^")) or sym
    words = _NAME_NOISE.sub(" ", name).split()
    full = " ".join(words[:4])
    # "Emaar Properties" is usually just "Emaar"; "First Abu Dhabi Bank" is never just "First".
    lead = words[0] if words and len(words[0]) >= 4 and words[0].lower() not in _GENERIC else None
    if q.get("type") == "future" or sym.endswith("=F"):
        terms = [words[0]] if words else [base]  # "Gold", "Crude"; the futures root (GC, CL) is too ambiguous
    elif q.get("type") == "currency" or sym.endswith("=X"):
        terms = [name]
    elif sym.startswith("^"):
        terms = [full]
    else:
        terms = [base, full, *([lead] if lead else [])]
    terms = [t for t in dict.fromkeys(terms) if t and len(t) >= 2]
    return {"symbol": sym, "name": name, "base": base, "short": full or base, "terms": terms, "quote": q}


_MARKET_WORDS = re.compile(
    r"\b(stocks?|shares?|shareholders?|dividends?|earnings|revenue|profits?|quarterly|q[1-4]|guidance|share price|stock price|"
    r"price target|valuation|invest(?:or|ors|ing|ment|ments|ed)?|portfolio|buy|buying|sell|selling|bullish|bearish|ipo|adx|dfm|"
    r"nasdaq|nyse|stock market|ticker|analysts?|rally|call options|put options|trader|traders|trading|upgrade|downgrade|"
    r"market cap|bonds?|sukuk|yields?|undervalued|overvalued|p/e|eps|short sellers?|shorting)\b|\d+(?:\.\d+)?%",
    re.I,
)


def _has(term: str, low: str) -> bool:
    return re.search(rf"(?<![\w$]){re.escape(term.lower())}(?!\w)", low) is not None


def _mentions(text: str, subj: dict[str, Any]) -> bool:
    """True when the post is about this asset. A bare ticker such as "FAB" also needs a $cashtag or market context."""
    low = text.lower()
    base = subj["base"]
    for t in subj["terms"]:
        if t == base:
            # the ticker has to be written as a ticker: $FAB, or FAB in capitals next to market talk
            if f"${base.lower()}" in low or (
                re.search(rf"(?<![\w$]){re.escape(base)}(?!\w)", text) and _MARKET_WORDS.search(low)
            ):
                return True
        elif _has(t, low):
            return True
    return False


def _clean(text: str, limit: int = 600) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ Reddit


def _reddit_token(db: Session) -> str:
    s = get_settings()

    def fetch() -> dict[str, Any]:
        try:
            r = httpx.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"},
                auth=(s.reddit_client_id or "", s.reddit_client_secret or ""),
                headers={"User-Agent": API_UA},
                timeout=15,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Reddit unreachable ({exc.__class__.__name__})") from exc
        if r.status_code != 200 or "access_token" not in r.json():
            raise ProviderError(f"Reddit rejected the app credentials ({r.status_code})")
        return {"token": r.json()["access_token"]}

    value, _ = markets.cached(db, "pulse:reddit-token", timedelta(minutes=50), fetch)
    return str(value["token"])


def _reddit_query(subj: dict[str, Any]) -> str:
    return " OR ".join(f'"{t}"' if " " in t else t for t in subj["terms"])


def reddit(db: Session, subj: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    s = get_settings()
    if s.reddit_client_id and s.reddit_client_secret:
        try:
            r = httpx.get(
                "https://oauth.reddit.com/search",
                params={"q": _reddit_query(subj), "sort": "new", "t": "month", "limit": 60, "type": "link", "raw_json": 1},
                headers={"Authorization": f"Bearer {_reddit_token(db)}", "User-Agent": API_UA},
                timeout=20,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Reddit unreachable ({exc.__class__.__name__})") from exc
        if r.status_code != 200:
            raise ProviderError(f"Reddit API error ({r.status_code})")
        out = []
        for c in r.json().get("data", {}).get("children", []):
            d = c.get("data", {})
            text = f"{d.get('title', '')} {d.get('selftext', '')}"
            if d.get("over_18") or d.get("author") in (None, "[deleted]", "AutoModerator") or not _mentions(text, subj):
                continue
            out.append({
                "source": "reddit", "id": f"r-{d['id']}", "url": f"https://www.reddit.com{d.get('permalink', '')}",
                "author": d["author"], "author_url": f"https://www.reddit.com/user/{d['author']}", "avatar": None,
                "title": _clean(d.get("title", ""), 300), "text": _clean(d.get("selftext", "")),
                "community": f"r/{d.get('subreddit')}", "created_at": _iso(float(d.get("created_utc", time.time()))),
                "score": d.get("score"), "replies": d.get("num_comments"), "tag": None,
            })  # fmt: skip
        return out, "Reddit API"
    if not s.reddit_public_feed:
        raise ProviderError("waiting for Reddit to approve API access — Reddit requires approval before its posts can be shown")
    # Explicitly allowed: Reddit's public search feed (one retry when it asks us to slow down).
    for attempt in range(2):
        try:
            r = httpx.get(
                "https://www.reddit.com/search.rss",
                params={"q": _reddit_query(subj), "sort": "new", "t": "month", "limit": 50},
                headers={"User-Agent": BROWSER_UA},
                timeout=20,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Reddit unreachable ({exc.__class__.__name__})") from exc
        if r.status_code != 429 or attempt:
            break
        try:  # Reddit says how long to wait; honour it up to a few seconds
            wait = min(6.0, max(1.0, float(r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset") or 2.5)))
        except ValueError:
            wait = 2.5
        time.sleep(wait)
    if r.status_code != 200:
        raise ProviderError(
            "Reddit refused the public feed (" + str(r.status_code) + ") — connect the official Reddit API to read posts reliably"
        )
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as exc:
        raise ProviderError("Reddit returned an unreadable feed") from exc
    for e in root.findall("a:entry", ns):
        author = (e.findtext("a:author/a:name", "", ns) or "").removeprefix("/u/")
        link = e.find("a:link", ns)
        url = link.get("href") if link is not None else ""
        title = _clean(e.findtext("a:title", "", ns), 300)
        body = _clean(e.findtext("a:content", "", ns))
        body = re.sub(r"\s*submitted by /u/\S+.*$", "", body).strip()
        cat = e.find("a:category", ns)
        if not author or not url or not _mentions(f"{title} {body}", subj):
            continue
        updated = e.findtext("a:updated", "", ns) or e.findtext("a:published", "", ns)
        out.append({
            "source": "reddit", "id": f"r-{e.findtext('a:id', url, ns)}", "url": url, "author": author,
            "author_url": f"https://www.reddit.com/user/{author}", "avatar": None, "title": title, "text": body,
            "community": f"r/{cat.get('term')}" if cat is not None and not str(cat.get("term", "")).startswith(("u/", "u_")) else None,
            "created_at": updated.replace("+00:00", "Z") if updated else None, "score": None, "replies": None, "tag": None,
        })  # fmt: skip
    return out, "Reddit public search feed"


# ------------------------------------------------------------------ X


def x_posts(subj: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    token = get_settings().x_bearer_token
    if not token:
        raise ProviderError("not connected — reading X needs an X API plan with search access")
    words = [f'"{t}"' if " " in t else t for t in subj["terms"] if t != subj["base"]]
    cash = [f"${subj['base']}"] if subj["base"] in subj["terms"] else []
    query = f"({' OR '.join([*words, *cash])}) -is:retweet lang:en"
    try:
        r = httpx.get(
            "https://api.x.com/2/tweets/search/recent",
            params={
                "query": query[:500],
                "max_results": 50,
                "tweet.fields": "created_at,public_metrics,author_id",
                "expansions": "author_id",
                "user.fields": "username,name,profile_image_url",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"X unreachable ({exc.__class__.__name__})") from exc
    if r.status_code != 200:
        raise ProviderError(f"X API error ({r.status_code}) — check the plan includes recent search")
    body = r.json()
    users = {u["id"]: u for u in body.get("includes", {}).get("users", [])}
    out = []
    for t in body.get("data", []):
        u = users.get(t.get("author_id"), {})
        if not u.get("username"):
            continue
        m = t.get("public_metrics", {})
        out.append({
            "source": "x", "id": f"x-{t['id']}", "url": f"https://x.com/{u['username']}/status/{t['id']}",
            "author": u["username"], "author_url": f"https://x.com/{u['username']}", "avatar": u.get("profile_image_url"),
            "title": None, "text": _clean(t.get("text", "")), "community": None, "created_at": t.get("created_at"),
            "score": m.get("like_count"), "replies": m.get("reply_count"), "tag": None,
        })  # fmt: skip
    return out, "X API"


# ------------------------------------------------------------------ StockTwits


def stocktwits_symbol(subj: dict[str, Any]) -> str | None:
    sym = subj["symbol"]
    if re.fullmatch(r"[A-Z]{1,5}", sym):
        return sym
    if m := re.fullmatch(r"([A-Z]{2,6})-USD", sym):
        return f"{m.group(1)}.X"
    return None  # UAE listings, futures, FX and indices aren't covered by StockTwits streams


def stocktwits(subj: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    st_sym = stocktwits_symbol(subj)
    if st_sym is None:
        raise ProviderError("StockTwits doesn't cover this listing")
    try:
        r = httpx.get(f"https://api.stocktwits.com/api/2/streams/symbol/{st_sym}.json",
                      headers={"User-Agent": BROWSER_UA, "Accept": "application/json"}, timeout=20)  # fmt: skip
    except httpx.HTTPError as exc:
        raise ProviderError(f"StockTwits unreachable ({exc.__class__.__name__})") from exc
    if r.status_code == 404:
        raise ProviderError("StockTwits has no stream for this symbol")
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        raise ProviderError(f"StockTwits unavailable ({r.status_code})")
    out = []
    for m in r.json().get("messages", []):
        u = m.get("user") or {}
        if not u.get("username"):
            continue
        tag = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
        out.append({
            "source": "stocktwits", "id": f"s-{m['id']}", "url": f"https://stocktwits.com/{u['username']}/message/{m['id']}",
            "author": u["username"], "author_url": f"https://stocktwits.com/{u['username']}", "avatar": u.get("avatar_url_ssl") or u.get("avatar_url"),
            "title": None, "text": _clean(m.get("body", "")), "community": None, "created_at": m.get("created_at"),
            "score": (m.get("likes") or {}).get("total"), "replies": None, "tag": tag if tag in ("Bullish", "Bearish") else None,
        })  # fmt: skip
    return out, "StockTwits public stream"


# ------------------------------------------------------------------ collection


# ------------------------------------------------------------------ Bluesky


def bluesky(subj: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Latest public Bluesky posts for each search term (the AppView search is open to everyone)."""
    queries = [
        f'"{t}"' if " " in t else (f"${t}" if t == subj["base"] and subj["base"] != subj["short"] else t) for t in subj["terms"]
    ]
    seen: dict[str, dict[str, Any]] = {}
    errors = []
    for query in dict.fromkeys(queries):
        try:
            r = httpx.get(
                "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                params={"q": query, "limit": 50, "sort": "latest"},
                headers={"User-Agent": "NexisFinance/1.0 (+https://nexis-finance-five.vercel.app)", "Accept": "application/json"},
                timeout=20,
            )
        except httpx.HTTPError as exc:
            errors.append(exc.__class__.__name__)
            continue
        if r.status_code != 200:
            errors.append(str(r.status_code))
            continue
        for post in r.json().get("posts", []):
            a = post.get("author") or {}
            rec = post.get("record") or {}
            text = rec.get("text") or ""
            handle = a.get("handle")
            uri = post.get("uri", "")
            # Pulse is about investor opinion: product chatter and property ads without market talk are left out
            if not handle or not uri or uri in seen or not _mentions(text, subj) or not _MARKET_WORDS.search(text):
                continue
            seen[uri] = {
                "source": "bluesky", "id": f"b-{uri.rsplit('/', 1)[-1]}", "url": f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}",
                "author": handle, "author_url": f"https://bsky.app/profile/{handle}", "avatar": a.get("avatar"),
                "title": None, "text": _clean(text), "community": None, "created_at": rec.get("createdAt") or post.get("indexedAt"),
                "score": post.get("likeCount"), "replies": post.get("replyCount"), "tag": None,
            }  # fmt: skip
    if not seen and errors:
        raise ProviderError(f"Bluesky unavailable ({', '.join(errors[:2])})")
    return list(seen.values()), "Bluesky public API"


# ------------------------------------------------------------------ Nexis members


def nexis_members(db: Session, subj: dict[str, Any]) -> list[dict[str, Any]]:
    """Finstagram posts by people (not the automated news pages) that tag this asset, from the last 30 days."""
    from sqlalchemy import select

    from app.db.base import utcnow
    from app.models import Post, User
    from app.services.auth import linked_accounts

    since = utcnow() - timedelta(days=30)
    rows = db.execute(
        select(Post, User).join(User, User.id == Post.user_id)
        .where(Post.hidden.is_(False), Post.created_at >= since, User.kind == "person", User.is_disabled.is_(False))
        .order_by(Post.created_at.desc()).limit(400)
    ).all()  # fmt: skip
    out = []
    for post, user in rows:
        if subj["symbol"] not in (post.symbols or []):
            continue
        out.append({
            "source": "nexis", "id": f"n-{post.id}", "url": f"/finstagram/p/{post.id}", "author": user.username,
            "author_url": f"/finstagram/u/{user.username}", "avatar": None, "title": None, "text": _clean(post.body),
            "community": "Finstagram", "created_at": post.created_at.isoformat() + "Z", "score": post.like_count,
            "replies": post.comment_count, "tag": None, "linked": linked_accounts(db, user.id),
        })  # fmt: skip
    return out[:40]


def search_links(subj: dict[str, Any]) -> dict[str, str]:
    """Where to read the live discussion on sites Nexis doesn't read itself."""
    terms = [f'"{t}"' if " " in t else t for t in subj["terms"]]
    cash = f"${subj['base']}" if subj["base"] in subj["terms"] else None
    return {
        "reddit": "https://www.reddit.com/search/?sort=new&q=" + _q(" OR ".join(terms)),
        "x": "https://x.com/search?f=live&q="
        + _q(" OR ".join([*(t for t in terms if t != subj["base"]), *([cash] if cash else [])])),
    }


def sources_status() -> dict[str, dict[str, Any]]:
    s = get_settings()
    return {
        "bluesky": {"label": "Bluesky", "official": True},
        "nexis": {"label": "Nexis members", "official": True},
        "reddit": {
            "label": "Reddit",
            "official": bool(s.reddit_client_id and s.reddit_client_secret),
            "pending": not (s.reddit_client_id and s.reddit_client_secret) and not s.reddit_public_feed,
        },
        "x": {"label": "X", "official": bool(s.x_bearer_token)},
        "stocktwits": {"label": "StockTwits", "official": True},
    }


def _reddit_cached(db: Session, subj: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Reddit results keep their own cache, so a refusal falls back to the last good result."""

    def fetch() -> dict[str, Any]:
        items, via = reddit(db, subj)
        return {"items": items, "via": via}

    value, meta = markets.cached(db, f"pulse:reddit:{subj['symbol']}", timedelta(minutes=15), fetch)
    via = value["via"] + (" · earlier result, Reddit is rate-limiting right now" if meta.get("stale") else "")
    return value["items"], via


def collect(db: Session, symbol: str) -> dict[str, Any]:
    subj = subject(db, symbol)

    def fetch() -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        report: dict[str, dict[str, Any]] = {}
        for key, fn in (
            ("bluesky", lambda: bluesky(subj)),
            ("stocktwits", lambda: stocktwits(subj)),
            ("reddit", lambda: _reddit_cached(db, subj)),
            ("x", lambda: x_posts(subj)),
        ):
            try:
                got, via = fn()
                items += got
                report[key] = {"ok": True, "count": len(got), "via": via}
            except ProviderError as exc:
                report[key] = {"ok": False, "count": 0, "error": exc.message}
        items.sort(key=lambda i: i.get("created_at") or "", reverse=True)
        tags = [i["tag"] for i in items if i["tag"]]
        return {
            "items": items[:120],
            "sources": report,
            "tags": {"bullish": tags.count("Bullish"), "bearish": tags.count("Bearish")},
        }

    value, meta = markets.cached(db, f"pulse:{subj['symbol']}", TTL, fetch)
    # Nexis members are read fresh on every request, so a new post shows up straight away.
    members = nexis_members(db, subj)
    items = sorted(members + value["items"], key=lambda i: i.get("created_at") or "", reverse=True)[:140]
    links = search_links(subj)
    sources = {"bluesky": value["sources"].get("bluesky", {"ok": False, "count": 0, "error": "not collected yet"}),
               "nexis": {"ok": True, "count": len(members), "via": "Finstagram posts by members"},
               **{k: v for k, v in value["sources"].items() if k != "bluesky"}}  # fmt: skip
    for k in ("reddit", "x"):
        if k in sources:
            sources[k] = {**sources[k], "search_url": links[k]}
    q = subj["quote"]
    return {
        "symbol": subj["symbol"],
        "name": subj["name"],
        "search_terms": subj["terms"],
        "quote": {k: q.get(k) for k in ("price", "change", "change_pct", "currency", "type", "exchange")} if q else None,
        **value,
        "items": items,
        "sources": sources,
        "fetched_at": meta["fetched_at"],
        "disclaimer": DISCLAIMER,
    }


# ------------------------------------------------------------------ AI synthesis

SYNTH_PROMPT = """You summarise what retail investors are saying about {name} ({symbol}) on social media.
You are given numbered posts. Use ONLY these posts. Never add facts, prices or news that are not in them.
These are opinions, not verified information: phrase every point as what posters say or argue.
Cite the posts that support each point with their numbers. If there are few posts or they are off-topic, say so plainly.

Return JSON only:
{{"summary": "2-3 sentences on the overall conversation",
  "themes": [{{"title": "short", "detail": "one or two sentences", "refs": [1, 4]}}],
  "bull": [{{"point": "an argument for the asset", "refs": [2]}}],
  "bear": [{{"point": "an argument against or a risk raised", "refs": [3]}}],
  "questions": [{{"point": "a question or concern people raise", "refs": [5]}}],
  "tone": "mostly positive | mixed | mostly negative | unclear",
  "coverage": "one sentence on how much there was to go on"}}
Use at most 4 themes and at most 4 items per list. Leave a list empty rather than inventing.

Posts:
{posts}"""


def synthesis(db: Session, symbol: str) -> dict[str, Any]:
    data = collect(db, symbol)
    items = data["items"][:50]
    if len(items) < 3:
        return {"available": False, "reason": "Not enough posts were collected to summarise.", "posts_used": len(items)}
    key = f"pulse-ai:{data['symbol']}:{abs(hash(tuple(i['id'] for i in items))) % 10**10}"

    def fetch() -> dict[str, Any]:
        lines = []
        for n, i in enumerate(items, 1):
            head = f"[{n}] {i['source']} · @{i['author']}{' · ' + i['community'] if i.get('community') else ''} · {str(i.get('created_at', ''))[:10]}"
            if i.get("tag"):
                head += f" · self-tagged {i['tag']}"
            lines.append(f"{head}\n{(i.get('title') or '')} {i.get('text') or ''}"[:520])
        prompt = SYNTH_PROMPT.format(name=data["name"], symbol=data["symbol"], posts="\n\n".join(lines))
        try:
            msg = llm.chat([{"role": "system", "content": "You write careful, neutral summaries. Output valid JSON only."},
                            {"role": "user", "content": prompt}], max_tokens=1400, temperature=0.2)  # fmt: skip
        except llm.LLMUnavailable as exc:
            raise ProviderError(f"AI model unavailable: {exc.reason}") from exc
        m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            out = None
        if not isinstance(out, dict):
            raise ProviderError("the AI model returned an unreadable summary")
        n_max = len(items)

        def refs(r: Any) -> list[int]:
            return [int(x) for x in (r or []) if isinstance(x, int | float) and 1 <= int(x) <= n_max][:6]

        def points(key: str, field: str) -> list[dict[str, Any]]:
            return [
                {"text": str(p.get(field, ""))[:400], "refs": refs(p.get("refs"))}
                for p in (out.get(key) or [])[:4]
                if isinstance(p, dict) and p.get(field)
            ]

        return {
            "summary": str(out.get("summary", ""))[:900],
            "themes": [
                {"title": str(t.get("title", ""))[:80], "text": str(t.get("detail", ""))[:400], "refs": refs(t.get("refs"))}
                for t in (out.get("themes") or [])[:4]
                if isinstance(t, dict) and t.get("title")
            ],
            "bull": points("bull", "point"),
            "bear": points("bear", "point"),
            "questions": points("questions", "point"),
            "tone": str(out.get("tone", "unclear"))[:30],
            "coverage": str(out.get("coverage", ""))[:300],
            "model": msg.get("_model"),
        }

    try:
        value, meta = markets.cached(db, key[:300], timedelta(minutes=30), fetch)
    except ProviderError as exc:
        return {"available": False, "reason": exc.message, "posts_used": len(items)}
    # refs point into the same ordered list the page shows
    return {
        "available": True,
        **value,
        "posts_used": len(items),
        "ref_ids": [i["id"] for i in items],
        "generated_at": meta["fetched_at"],
    }


WARM = ("EMAAR.AE", "FAB.AD", "ALDAR.AD", "EMIRATESNBD.AE", "NVDA", "TSLA", "AAPL", "BTC-USD")


def warm(db: Session, symbols: tuple[str, ...] = WARM, pause: float = 4.0) -> dict[str, Any]:
    """Collect popular assets one by one (run by the daily cron), so Reddit refusals later fall back to stored posts."""
    done: dict[str, Any] = {}
    for n, sym in enumerate(symbols):
        if n:
            time.sleep(pause)
        try:
            d = collect(db, sym)
            done[sym] = {k: v["count"] for k, v in d["sources"].items()}
        except NexisError as exc:
            done[sym] = {"error": exc.message}
    return done
