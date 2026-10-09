"""Public discussions from other sites, shown in Nexis Pulse without anyone's account.

Sources (each can be switched off in settings; none needs an API key):

* **Reddit** — OFF by default (``pulse_public_reddit``). Reddit's User Agreement prohibits automated collection
  without Reddit's prior written consent, and its Data API Terms require a separate written agreement for
  commercial use, so Nexis doesn't collect Reddit content unless that permission exists. While it's off, Reddit
  threads collected earlier are withdrawn from Pulse (``withdraw_reddit``). Members can still share links to Reddit
  discussions in their own posts; those open on Reddit.
* **Hacker News** — recent, well-discussed finance and AI threads, from the public HN Algolia search API.
* **StockTwits** — today's messages about widely followed tickers that their authors tagged bullish or bearish.

Rules:

* **No accounts.** Usernames, "/u/" and "@" mentions are removed from every text before it is stored. Nothing about
  the person who wrote a reply is kept.
* **Labelled by source, never as Nexis members.** Threads are ``kind = "public"`` and show "Reddit · r/stocks"
  etc. They never count as Nexis community activity and never trend unless Nexis members join in.
* **Short excerpts, always linked** to the original, so the full context is one click away.
* **Filtered.** Scams, solicitation, coordinated pumping (``pulse_safety``), slurs, bots and one-word replies are
  dropped. A thread with fewer than three usable replies is not published.

Each reply is sorted into how it relates to the thread — supports it, pushes back, asks a question, or adds context
(StockTwits: bullish or bearish as tagged by the author). An AI model does this when one is configured; otherwise
word rules do.
"""

from __future__ import annotations

import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import MarketCache, PulseDiscussion
from app.services import llm, pulse, pulse_safety

log = get_logger(__name__)

UA = "Mozilla/5.0 (compatible; NexisFinance/1.0; +https://nexis-finance-five.vercel.app/ai-disclosure)"
SUBREDDITS = ["stocks", "investing", "StockMarket", "ValueInvesting", "SecurityAnalysis", "Economics", "dividends", "CryptoCurrency"]
HN_QUERIES = ["stock market", "interest rates", "inflation", "Nvidia", "earnings", "IPO", "Federal Reserve", "AI chips", "recession", "bitcoin",
              "tariffs", "layoffs", "OpenAI valuation", "Tesla", "Apple", "Microsoft", "Google antitrust", "Amazon", "housing market",
              "jobs report", "stablecoin", "bank", "venture capital", "private equity", "oil prices", "dollar", "bonds", "S&P 500"]
STOCKTWITS_SYMBOLS = ["NVDA", "AAPL", "TSLA", "MSFT", "AMZN", "META", "AMD", "GOOGL", "SPY", "BTC.X"]
PLATFORMS = {"reddit": "Reddit", "hn": "Hacker News", "stocktwits": "StockTwits"}
STANCES = ("support", "pushback", "question", "context", "bullish", "bearish")
MAX_QUOTES = 12
MIN_QUOTES = 3
KEEP_DAYS = 21
REDDIT_PAUSE = 10.0  # Reddit throttles unauthenticated feeds hard; one request every ten seconds
STATE_KEY = "public-discussions:state"

_FINANCE = re.compile(
    r"\b(stocks?|shares?|market|invest\w*|earnings|valuation|fed|rates?|inflation|recession|economy|ipo|bonds?|yields?|bitcoin|crypto|"
    r"nvidia|apple|tesla|microsoft|amazon|google|meta|chips?|semiconductor\w*|bank\w*|oil|dividends?|s&p|nasdaq|portfolio|trading|valuation|tariffs?|"
    r"hedge fund|private equity|venture|startup funding|layoffs)\b",
    re.I,
)
_SLURS = re.compile(r"\b(retard\w*|fag\w*|n[i1]gg\w*|tr[a@]nn\w*|kys)\b", re.I)
_USER = re.compile(r"(?<![\w/])(?:/?u/[A-Za-z0-9_-]{2,}|@[A-Za-z0-9_]{2,})")
_URL = re.compile(r"https?://\S+")
# Hype and promotion that adds no argument (stricter than the rules for Nexis members' own posts).
_HYPE = re.compile(
    r"\b(pump\w*|dump\w*|to the moon|moon(ing)?|tap in|it'?s free|bio|link in bio|calls in order|load(ing)? up|lfg|mark (it|my words)|"
    r"let'?s go+|go harder|wsb|tendies|yolo|free (signals?|alerts?)|discord|join (us|now)|subscribe|watch (this|my) (new )?vid\w*|"
    r"rich dads?|boys and girls|thanks+)\b",
    re.I,
)
# Hacker News threads must be about markets or business, not just mention a company (a Kindle launch is not a debate).
_MARKET = re.compile(
    r"\b(stocks?|share price|shareholders?|markets?|invest\w*|earnings|revenue|profits?|losses|valuation|ipo|fed|federal reserve|rates?|inflation|recession|"
    r"economy|economic|gdp|jobs report|unemployment|bonds?|treasur\w+|yields?|bitcoin|crypto\w*|stablecoins?|tariffs?|layoffs|bank\w*|credit|debt|"
    r"loans?|fees|prices?|oil|dividends?|s&p|nasdaq|dow|funding|raises?|acqui\w+|mergers?|buyouts?|antitrust|bankrupt\w*|bubble|capex|spending|"
    r"\$\d+(\.\d+)?\s?[bmt]\b|billion|trillion|venture|private equity|hedge funds?|dollar|euro|currency)\b",
    re.I,
)
_BOTS = re.compile(r"^(i am a bot|this action was performed automatically|your (post|submission) (has been|was) removed|\[deleted\]|\[removed\])", re.I)


# ------------------------------------------------------------------ text


def clean(raw: str | None) -> str:
    """HTML or markdown-ish text → one plain paragraph with every username removed."""
    t = html.unescape(raw or "")
    t = re.sub(r"<!-- SC_(OFF|ON) -->", " ", t)
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"(?i)\bsubmitted by\s+/?u/\S+.*$", " ", t)  # Reddit's feed footer names the poster
    t = re.sub(r"\[(link|comments)\]", " ", t)
    t = _USER.sub("[user]", t)
    t = _URL.sub("[link]", t)
    return re.sub(r"\s+", " ", t).strip()


_PROFANITY = re.compile(r"\b(f+u+c+k\w*|sh[i1]t\w*|bullsh[i1]t\w*|assholes?|bitch\w*|damn\w*|crap\w*|wtf)\b", re.I)


def tidy(text: str) -> str:
    """Mask profanity so Pulse reads like a financial publication without changing what was argued."""
    return _PROFANITY.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)


def excerpt(text: str, n: int = 300) -> str:
    if len(text) <= n:
        return text
    cut = text[:n]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[: end + 1] if end > n * 0.5 else cut.rsplit(" ", 1)[0]).rstrip() + ("" if end > n * 0.5 else "…")


def usable(text: str) -> bool:
    if len(text) < 40 or _BOTS.search(text) or _SLURS.search(text) or _HYPE.search(text):
        return False
    words = re.sub(r"\[(user|link)\]|\$[A-Za-z.]+", " ", text)
    if len(words.strip()) < 50:  # needs an actual sentence once tickers and links are gone
        return False
    letters = [c for c in words if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.45:  # shouting
        return False
    return not any(f["severity"] == "hold" for f in pulse_safety.check(text))


def rule_stance(text: str) -> str:
    t = text.lower().strip()
    if t.endswith("?") or re.match(r"^(how|what|why|is|are|does|do|can|could|would|should|any|anyone|has|have|will)\b", t):
        return "question"
    if re.search(r"\b(disagree|not convinced|i doubt|doubtful|overvalued|overpriced|bubble|wrong|no way|careful|downside|priced in|bearish|"
                 r"short it|sell|selling|dump|red flag|concern|worried|risk|won'?t|isn'?t|doesn'?t|not (?:a|the|that|worth))\b", t):  # fmt: skip
        return "pushback"
    if re.search(r"\b(agree|exactly|same here|bought|buying|holding|long|bullish|undervalued|upside|love|great|strong|solid|winner|added more)\b", t):
        return "support"
    return "context"


def symbols_in(text: str) -> list[str]:
    from app.services.events import CORE_UNIVERSE, SECTOR_BY_SYMBOL
    from app.services.newsfeed import _NAME_RE, NAME_TO_SYMBOL
    from app.services.social import _tags

    cash, _ = _tags(text)
    names = [NAME_TO_SYMBOL[m.group(1).lower()] for m in _NAME_RE.finditer(text)]
    known = {s for s in [*CORE_UNIVERSE, *SECTOR_BY_SYMBOL] if s.isalpha() and len(s) >= 3}
    bare = [w for w in re.findall(r"\b[A-Z]{3,5}\b", text) if w in known]
    return list(dict.fromkeys([*(c for c in cash if len(c) <= 6), *names, *bare]))[:6]


def _iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
    except ValueError:
        return None


# ------------------------------------------------------------------ fetching (polite)


def _state(db: Session) -> dict[str, Any]:
    row = db.get(MarketCache, STATE_KEY)
    return dict(row.payload.get("v", {})) if row else {}


def _save_state(db: Session, **kw: Any) -> None:
    row = db.get(MarketCache, STATE_KEY)
    v = {**_state(db), **kw}
    if row is None:
        db.add(MarketCache(key=STATE_KEY, payload={"v": v}, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = {"v": v}, utcnow()
    db.commit()


def _get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.get(url, params=params, headers={"user-agent": UA, "accept": "*/*"}, timeout=15.0, follow_redirects=True)


def fetch_atom(url: str) -> list[dict[str, Any]]:
    """Entries of an Atom feed: id, title, link, published, content (HTML)."""
    r = _get(url)
    if r.status_code in (403, 429):
        raise PermissionError(f"{r.status_code} from {url.split('?')[0]}")
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.content)
    out = []
    for e in root.findall("a:entry", ns):
        link = e.find("a:link", ns)
        out.append({
            "id": (e.findtext("a:id", default="", namespaces=ns) or "").strip(),
            "title": html.unescape(e.findtext("a:title", default="", namespaces=ns) or "").strip(),
            "link": link.get("href") if link is not None else None,
            "published": _iso(e.findtext("a:published", default=None, namespaces=ns) or e.findtext("a:updated", default=None, namespaces=ns)),
            "content": e.findtext("a:content", default="", namespaces=ns) or "",
        })  # fmt: skip
    return out


# ------------------------------------------------------------------ classifying replies (AI when available)

CLASSIFY = """A public online thread about markets is below, followed by numbered replies.
For each reply, say how it relates to the thread: "support" (agrees or adds a supporting argument), "pushback"
(disagrees or argues the other side), "question", or "context" (neutral information). Then write ONE neutral
sentence describing what the replies are arguing about. Never name or describe the people.

THREAD: {title}
{body}

REPLIES
{replies}

Return JSON only: {{"stances": [{{"n": 1, "stance": "support"}}], "debate": "one sentence"}}"""


def classify(title: str, body: str, quotes: list[dict[str, Any]], db: Session) -> tuple[str | None, bool]:
    """Fill each quote's ``stance``; returns (one-line debate summary or None, whether AI did it)."""
    for q in quotes:
        q["stance"] = q.get("stance") or rule_stance(q["text"])
    from app.services import market_pulse

    if not quotes or not market_pulse._ai_available(db):
        return None, False
    market_pulse._log_ai_call(db)
    lines = "\n".join(f"[{i}] {q['text']}" for i, q in enumerate(quotes, 1))
    try:
        models = [m.strip() for m in get_settings().pulse_model.split(",") if m.strip()]
        msg = llm.chat([{"role": "system", "content": "You classify arguments neutrally. Output valid JSON only."},
                        {"role": "user", "content": CLASSIFY.format(title=title, body=body[:600], replies=lines)}],
                       max_tokens=700, temperature=0.1, models=models)  # fmt: skip
        m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
        out = json.loads(m.group(0)) if m else {}
    except (llm.LLMUnavailable, json.JSONDecodeError, ValueError) as exc:
        log.info("public discussion classification fell back to rules: %s", exc)
        return None, False
    for s in out.get("stances") or []:
        if isinstance(s, dict) and isinstance(s.get("n"), int) and 1 <= s["n"] <= len(quotes) and s.get("stance") in ("support", "pushback", "question", "context"):
            quotes[s["n"] - 1]["stance"] = s["stance"]
    debate = str(out.get("debate") or "").strip()
    bad = not debate or len(debate) > 300 or re.search(r"\b(u/|@|user\w*\s+named|the poster|OP)\b", debate)
    return (None if bad else debate), True


# ------------------------------------------------------------------ storing


def upsert(
    db: Session,
    key: str,
    *,
    platform: str,
    community: str | None,
    url: str,
    title: str,
    body: str,
    posted_at: datetime | None,
    quotes: list[dict[str, Any]],
    debate: str | None = None,
    ai: bool = False,
    symbols: list[str] | None = None,
) -> PulseDiscussion | None:
    """Create or refresh one public thread. Returns None when it isn't publishable."""
    title = tidy(clean(title))[:200]
    body = tidy(body)
    if len(title) < 8 or any(f["severity"] == "hold" for f in pulse_safety.check(title)) or _SLURS.search(title):
        return None
    now = utcnow()
    d = db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key == key)).first()
    merged: dict[str, dict[str, Any]] = {q["url"] or q["text"]: q for q in ((d.quotes or []) if d else [])}
    for q in quotes:
        merged[q["url"] or q["text"]] = q
    # Quotes stored by an earlier run are checked again, so a tightened filter applies to them too.
    kept = sorted((q for q in merged.values() if usable(q["text"])), key=lambda q: q.get("at") or "", reverse=True)[:MAX_QUOTES]
    if len(kept) < MIN_QUOTES:
        return None
    label = f"{PLATFORMS[platform]} · {community}" if community else PLATFORMS[platform]
    syms = symbols if symbols is not None else symbols_in(f"{title} {body}")
    newest = max((_iso(q.get("at")) or posted_at or now for q in kept), default=posted_at or now)
    if d is None:
        d = PulseDiscussion(public_id=pulse.new_public_id(db), kind="public", editorial_key=key, title=title, body="", status="visible",
                            created_at=min(posted_at or now, now), last_activity_at=now)  # fmt: skip
        db.add(d)
        db.flush()
    elif d.status != "visible":
        return d  # removed by a moderator: never brought back
    d.title, d.body = title, excerpt(body, 600)
    d.origin = {"platform": platform, "community": community, "url": url, "label": label,
                "posted_at": posted_at.isoformat() + "Z" if posted_at else None}  # fmt: skip
    d.quotes = kept
    d.debate = debate or d.debate
    d.ai_assisted = ai or d.ai_assisted
    d.symbols = syms
    d.primary_symbol = syms[0] if syms else None
    d.content_updated_at = now
    d.last_activity_at = max(d.last_activity_at, min(newest, now))
    pulse._set_assets(db, d, syms)
    pulse._set_topics(db, d, pulse.detect_topics(f"{title} {body}", syms))
    db.commit()
    return d


def withdraw_reddit(db: Session) -> int:
    """Hide previously collected Reddit threads while Reddit collection isn't permitted (reversible; pruned later)."""
    res = db.execute(update(PulseDiscussion).where(PulseDiscussion.kind == "public", PulseDiscussion.editorial_key.like("reddit:%"),
                                                  PulseDiscussion.status == "visible").values(status="withdrawn"))  # fmt: skip
    db.commit()
    return res.rowcount or 0


def prune(db: Session) -> int:
    """Old public threads nobody on Nexis replied to are dropped; ones with Nexis replies stay."""
    cutoff = utcnow() - timedelta(days=KEEP_DAYS)
    res = db.execute(delete(PulseDiscussion).where(PulseDiscussion.kind == "public", PulseDiscussion.comment_count == 0,
                                                  PulseDiscussion.last_activity_at < cutoff))  # fmt: skip
    db.commit()
    return res.rowcount or 0


# ------------------------------------------------------------------ sources


def _quote(text: str, url: str | None, at: datetime | None, stance: str | None = None) -> dict[str, Any]:
    return {"text": tidy(excerpt(text)), "stance": stance, "url": url, "at": at.isoformat() + "Z" if at else None}


def collect_reddit(db: Session, subreddits: list[str], posts_per_sub: int = 10, with_comments: int = 0) -> dict[str, Any]:
    """One request per subreddit: its latest replies, grouped under the threads they belong to (each reply's feed
    title carries the thread title). Reddit throttles keyless feeds hard, so nothing else is requested."""
    out: dict[str, Any] = {"threads": 0, "skipped": 0}
    for sub in subreddits:
        posts: dict[str, dict[str, Any]] = {}  # thread text is only known for threads collected before
        threads: dict[str, dict[str, Any]] = {}
        for c in fetch_atom(f"https://www.reddit.com/r/{sub}/comments/.rss?limit=100"):
            m = re.search(r"/comments/([a-z0-9]+)/[^/]*/([a-z0-9]+)/?", c["link"] or "")
            if not m or not c["id"].startswith("t1_"):
                continue
            t = threads.setdefault(m.group(1), {"title": re.sub(r"^/?u/\S+\s+on\s+", "", c["title"]).strip(), "quotes": [], "link": None})
            t["link"] = t["link"] or re.sub(r"[a-z0-9]+/?$", "", c["link"])
            text = clean(c["content"])
            if usable(text):
                t["quotes"].append(_quote(text, c["link"], c["published"]))
        for pid in dict.fromkeys([*posts, *threads]):
            t = threads.get(pid) or {"title": None, "quotes": [], "link": None}
            p = posts.get(pid)
            quotes = t["quotes"]
            have = db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key == f"reddit:{pid}")).first()
            if len(quotes) < MIN_QUOTES and have is None:
                continue
            title = p["title"] if p else t["title"]
            if not title or (not _FINANCE.search(title) and sub not in ("stocks", "investing", "StockMarket", "ValueInvesting", "SecurityAnalysis", "dividends")):
                continue
            body = clean(p["content"]) if p else (clean(have.body) if have else "")
            debate, ai = classify(title, body, quotes, db) if quotes else (None, False)
            d = upsert(db, f"reddit:{pid}", platform="reddit", community=f"r/{sub}", url=(p or {}).get("link") or t["link"] or f"https://www.reddit.com/r/{sub}/comments/{pid}/",
                       title=title, body=body, posted_at=(p or {}).get("published"), quotes=quotes, debate=debate, ai=ai)  # fmt: skip
            out["threads" if d else "skipped"] += 1
        time.sleep(REDDIT_PAUSE)
    return out


def collect_hn(db: Session, queries: list[str], days: int = 7, per_query: int = 3) -> dict[str, Any]:
    out: dict[str, Any] = {"threads": 0, "skipped": 0}
    since = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    for q in queries:
        r = _get("https://hn.algolia.com/api/v1/search", {"tags": "story", "query": q, "hitsPerPage": 8,
                                                          "numericFilters": f"created_at_i>{since},num_comments>=15"})  # fmt: skip
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if _MARKET.search(h.get("title") or "")][:per_query]
        for h in hits:
            key = f"hn:{h['objectID']}"
            have = db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key == key)).first()
            if have is not None and have.content_updated_at and utcnow() - have.content_updated_at < timedelta(hours=4):
                continue
            item = _get(f"https://hn.algolia.com/api/v1/items/{h['objectID']}").json()
            quotes = []
            for c in item.get("children") or []:
                text = clean(c.get("text"))
                if usable(text):
                    quotes.append(_quote(text, f"https://news.ycombinator.com/item?id={c['id']}", _iso(c.get("created_at"))))
                if len(quotes) >= MAX_QUOTES:
                    break
            site = re.sub(r"^www\.", "", httpx.URL(h["url"]).host) if h.get("url") else None
            body = clean(item.get("text")) or (f"A discussion of an article on {site}." if site else "")
            debate, ai = classify(h["title"], body, quotes, db)
            d = upsert(db, key, platform="hn", community=None, url=f"https://news.ycombinator.com/item?id={h['objectID']}", title=h["title"],
                       body=body, posted_at=_iso(h.get("created_at")), quotes=quotes, debate=debate, ai=ai)  # fmt: skip
            out["threads" if d else "skipped"] += 1
    return out


def collect_stocktwits(db: Session, symbols: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"threads": 0, "skipped": 0}
    today = utcnow().date().isoformat()
    for sym in symbols:
        r = _get(f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json")
        if r.status_code in (403, 429):
            raise PermissionError(f"{r.status_code} from StockTwits")
        r.raise_for_status()
        quotes = []
        for msg in r.json().get("messages") or []:
            tag = ((msg.get("entities") or {}).get("sentiment") or {}).get("basic")
            text = clean(msg.get("body"))
            tickers = [t.upper() for t in re.findall(r"\$([A-Za-z.]+)", text)]
            if tag not in ("Bullish", "Bearish") or not usable(text) or len(set(tickers)) > 3 or (tickers and tickers[0] != sym.upper()):
                continue  # must be mainly about this ticker, with a real argument
            quotes.append(_quote(text, f"https://stocktwits.com/message/{msg['id']}", _iso(msg.get("created_at")), tag.lower()))
        sides = {q["stance"] for q in quotes}
        if len(quotes) < 4 or sides != {"bullish", "bearish"}:  # only publish when both sides are actually arguing
            out["skipped"] += 1
            continue
        clean_sym = sym.replace(".X", "-USD") if sym.endswith(".X") else sym
        d = upsert(db, f"stocktwits:{sym}:{today}", platform="stocktwits", community=f"${sym.replace('.X', '')}",
                   url=f"https://stocktwits.com/symbol/{sym}", title=f"{sym.replace('.X', '')}: the bull and bear arguments traders are posting today",
                   body="", posted_at=min(_iso(q["at"]) or utcnow() for q in quotes), quotes=quotes, symbols=[clean_sym])  # fmt: skip
        out["threads" if d else "skipped"] += 1
    return out


def collect(db: Session, budget: str = "small") -> dict[str, Any]:
    """One polite round across the enabled sources, rotating through subreddits, queries and symbols."""
    s = get_settings()
    st = _state(db)
    big = budget == "large"
    result: dict[str, Any] = {}
    jobs: list[tuple[str, Any]] = []
    if s.pulse_public_reddit:
        i = st.get("reddit_i", 0)
        subs = [SUBREDDITS[(i + k) % len(SUBREDDITS)] for k in range(3 if big else 1)]
        jobs.append(("reddit", lambda: collect_reddit(db, subs)))
        st["reddit_i"] = (i + len(subs)) % len(SUBREDDITS)
    if s.pulse_public_hn:
        i = st.get("hn_i", 0)
        qs = [HN_QUERIES[(i + k) % len(HN_QUERIES)] for k in range(4 if big else 2)]
        jobs.append(("hn", lambda: collect_hn(db, qs)))
        st["hn_i"] = (i + len(qs)) % len(HN_QUERIES)
    if s.pulse_public_stocktwits:
        i = st.get("st_i", 0)
        syms = [STOCKTWITS_SYMBOLS[(i + k) % len(STOCKTWITS_SYMBOLS)] for k in range(5 if big else 2)]
        jobs.append(("stocktwits", lambda: collect_stocktwits(db, syms)))
        st["st_i"] = (i + len(syms)) % len(STOCKTWITS_SYMBOLS)
    paused = dict(st.get("paused") or {})
    for name, job in jobs:
        until = paused.get(name)
        if until and until > utcnow().isoformat():
            result[name] = {"paused_until": until}
            continue
        try:
            result[name] = job()
        except PermissionError as exc:  # the site asked us to back off: respect it for a while
            db.rollback()
            paused[name] = (utcnow() + timedelta(hours=2)).isoformat()
            result[name] = {"error": str(exc), "paused_until": paused[name]}
        except (httpx.HTTPError, ET.ParseError, ValueError, KeyError) as exc:
            db.rollback()
            result[name] = {"error": exc.__class__.__name__ + ": " + str(exc)[:160]}
    if not s.pulse_public_reddit:
        result["reddit_withdrawn"] = withdraw_reddit(db)
    result["pruned"] = prune(db)
    _save_state(db, **{k: v for k, v in st.items() if k != "paused"}, paused=paused, last_run=utcnow().isoformat(), last_result=result)
    return result


def status(db: Session) -> dict[str, Any]:
    s = get_settings()
    st = _state(db)
    return {"enabled": {"reddit": s.pulse_public_reddit, "hn": s.pulse_public_hn, "stocktwits": s.pulse_public_stocktwits},
            "last_run": st.get("last_run"), "last_result": st.get("last_result"), "paused": st.get("paused") or {}}  # fmt: skip
