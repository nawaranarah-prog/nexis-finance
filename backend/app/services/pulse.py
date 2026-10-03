"""Nexis Pulse: discussions about assets, and what they add up to.

A discussion is a community post that is about one asset and has a title (``Post.asset`` / ``Post.title``),
so likes, comments, saves, reports and the Community feed all work on it unchanged. Everything this module
reports — scores, percentages, volume changes, topics, arguments — is calculated from stored discussions;
when there isn't enough data it says so instead of showing a number.

The community Pulse score and sentiment use member discussions only (``pulse_sources.COMMUNITY``). Nexis Research
editorial discussions are listed alongside them with their own label and summarised as a separate research view.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Forbidden, NexisError, NotFoundError, ProviderError, ValidationFailed
from app.db.base import utcnow
from app.models import Comment, Like, Persona, Post, PostTopic, Save, SourceEvent, User
from app.services import events, llm, markets, pulse_score, pulse_sources, ratelimit
from app.services.auth import serialize_user

TOPICS: dict[str, str] = {
    "earnings": "Earnings", "valuation": "Valuation", "revenue": "Revenue", "margins": "Margins", "growth": "Growth",
    "dividends": "Dividends", "ai": "AI", "products": "Products", "competition": "Competition", "management": "Management",
    "regulation": "Regulation", "macro": "Macroeconomics", "rates": "Interest rates", "risk": "Risk", "debt": "Debt",
    "deals": "M&A and deals", "technicals": "Technical analysis", "real_estate": "Real estate", "energy": "Oil and energy",
}  # fmt: skip
MAX_TOPICS = 3
SENTIMENTS = pulse_score.SENTIMENTS
SCORE_WINDOW = timedelta(days=90)
DISCLAIMER = (
    "The Pulse score reflects the sentiment people expressed in recent Nexis discussions. It is not a price "
    "prediction, not a probability that the asset will rise, and not financial advice."
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ------------------------------------------------------------------ helpers


def _is_discussion():  # type: ignore[no-untyped-def]
    return and_(
        Post.asset.is_not(None), Post.title.is_not(None), Post.hidden.is_(False), Post.source.in_(list(pulse_sources.SOURCES))
    )


def _discussions() -> Select:
    return select(Post).where(_is_discussion())


def _from(sources: tuple[str, ...] | None):  # type: ignore[no-untyped-def]
    """Restrict to some sources; ``None`` keeps every source."""
    return Post.source.in_(list(sources)) if sources is not None else Post.id.is_not(None)


COMMUNITY = pulse_sources.COMMUNITY
EDITORIAL = pulse_sources.EDITORIAL
GENERATED = pulse_sources.GENERATED


# The sentiment used for statistics: what the author chose, otherwise what the AI detected.
def _effective():  # type: ignore[no-untyped-def]
    return func.coalesce(Post.sentiment, Post.ai_sentiment)


def _text(raw: str | None, lo: int, hi: int, what: str) -> str:
    text = _CONTROL.sub("", (raw or "").replace("\r\n", "\n")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) < lo or len(text) > hi:
        raise ValidationFailed(f"{what} must be {lo}–{hi:,} characters")
    return text


def _sentiment(raw: str | None) -> str | None:
    if raw in (None, ""):
        return None
    if raw not in SENTIMENTS:
        raise ValidationFailed("sentiment must be bullish, neutral or bearish")
    return raw


def _topics(raw: list[str] | None) -> list[str]:
    keys = list(dict.fromkeys(raw or []))
    bad = [k for k in keys if k not in TOPICS]
    if bad:
        raise ValidationFailed(f"unknown topic: {bad[0]}")
    if len(keys) > MAX_TOPICS:
        raise ValidationFailed(f"pick up to {MAX_TOPICS} topics")
    return keys


def _resolve_asset(db: Session, symbol: str) -> tuple[str, str]:
    sym = markets.clean_symbol(symbol)
    try:
        q = markets.quotes(db, [sym])
    except NexisError as exc:
        raise ValidationFailed("the asset couldn't be checked right now — try again in a moment") from exc
    if not q:
        raise ValidationFailed("we couldn't find that asset — pick it from the search")
    return sym, str(q[0].get("name") or sym)[:160]


def _get(db: Session, pid: int) -> Post:
    p = db.get(Post, pid)
    if p is None or p.hidden or p.asset is None or p.title is None:
        raise NotFoundError("discussion not found")
    return p


def _excerpt(body: str, n: int = 240) -> str:
    one = re.sub(r"\s+", " ", body).strip()
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def serialize(
    db: Session, posts: list[Post], viewer: User | None, full: bool = False, preview: bool = False
) -> list[dict[str, Any]]:
    if not posts:
        return []
    ids = [p.id for p in posts]
    authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_({p.user_id for p in posts})))}
    liked = set(db.scalars(select(Like.post_id).where(Like.post_id.in_(ids), Like.user_id == viewer.id))) if viewer else set()
    saved = set(db.scalars(select(Save.post_id).where(Save.post_id.in_(ids), Save.user_id == viewer.id))) if viewer else set()
    evs = {e.id: e for e in db.scalars(select(SourceEvent).where(SourceEvent.id.in_({p.event_id for p in posts if p.event_id})))}
    last: dict[int, dict[str, Any]] = {}
    if preview:
        latest = (
            select(Comment.post_id, func.max(Comment.id).label("cid"))
            .where(Comment.post_id.in_(ids))
            .group_by(Comment.post_id)
            .subquery()
        )
        rows = db.execute(
            select(Comment, User).join(latest, Comment.id == latest.c.cid).join(User, User.id == Comment.user_id)
        ).all()
        for c, u in rows:
            last[c.post_id] = {"author": serialize_user(db, u) | {"kind": u.kind}, "body": _excerpt(c.body, 160),
                               "created_at": c.created_at.isoformat() + "Z", "generated": c.generated}  # fmt: skip
    topics: dict[int, list[str]] = {}
    for pid, topic in db.execute(select(PostTopic.post_id, PostTopic.topic).where(PostTopic.post_id.in_(ids))):
        topics.setdefault(pid, []).append(topic)
    order = list(TOPICS)
    out = []
    for p in posts:
        out.append({
            "id": p.id, "asset": p.asset, "asset_name": p.asset_name, "title": p.title,
            "body": p.body if full else _excerpt(p.body), "truncated": not full and len(p.body) > 240,
            "sentiment": p.sentiment, "ai_sentiment": p.ai_sentiment,
            "topics": [{"key": t, "label": TOPICS.get(t, t)} for t in sorted(topics.get(p.id, []), key=lambda t: order.index(t) if t in order else 99)],
            "source": {"key": p.source, "label": pulse_sources.label(p.source),
                       "editorial": p.source in EDITORIAL, "generated": p.source in GENERATED},
            "author": serialize_user(db, authors[p.user_id]) | {"kind": authors[p.user_id].kind},
            "event": events.serialize(evs[p.event_id]) if p.event_id in evs else None,
            "last_reply": last.get(p.id), "saved_by_me": p.id in saved,
            "like_count": p.like_count, "comment_count": p.comment_count, "liked_by_me": p.id in liked,
            "is_mine": viewer is not None and viewer.id == p.user_id,
            "created_at": p.created_at.isoformat() + "Z", "edited_at": p.edited_at.isoformat() + "Z" if p.edited_at else None,
        })  # fmt: skip
    return out


# ------------------------------------------------------------------ create / edit / read


def create(
    db: Session, user: User, symbol: str, title: str, body: str, sentiment: str | None, topics: list[str] | None
) -> dict[str, Any]:
    ratelimit.hit(db, f"post:{user.id}", get_settings().posts_per_hour)
    title = _text(title, 8, 140, "the title")
    body = _text(body, 20, 5000, "the discussion")
    sentiment = _sentiment(sentiment)
    keys = _topics(topics)
    sym, name = _resolve_asset(db, symbol)
    from app.services.social import _tags

    cashtags, tags = _tags(f"{title}\n{body}")
    p = Post(
        user_id=user.id, body=body, symbols=list(dict.fromkeys([sym, *cashtags]))[:10], tags=tags,
        source="nexis", asset=sym, asset_name=name, title=title, sentiment=sentiment,
    )  # fmt: skip
    db.add(p)
    db.flush()
    db.add_all(PostTopic(post_id=p.id, topic=k) for k in keys)
    db.commit()
    return serialize(db, [p], user, full=True)[0]


_UNSET: Any = object()


def update(
    db: Session, pid: int, user: User, title: Any = _UNSET, body: Any = _UNSET, sentiment: Any = _UNSET, topics: Any = _UNSET
) -> dict[str, Any]:
    p = _get(db, pid)
    if p.user_id != user.id:
        raise Forbidden("you can only edit your own discussions")
    changed_text = False
    if title is not _UNSET:
        p.title, changed_text = _text(title, 8, 140, "the title"), True
    if body is not _UNSET:
        p.body, changed_text = _text(body, 20, 5000, "the discussion"), True
    if sentiment is not _UNSET:
        p.sentiment = _sentiment(sentiment)
    if topics is not _UNSET:
        keys = _topics(topics)
        db.query(PostTopic).filter(PostTopic.post_id == p.id).delete(synchronize_session=False)
        db.add_all(PostTopic(post_id=p.id, topic=k) for k in keys)
    if changed_text:
        p.ai_sentiment = None  # the AI read the old text
        from app.services.social import _tags

        cashtags, p.tags = _tags(f"{p.title}\n{p.body}")
        p.symbols = list(dict.fromkeys([p.asset, *cashtags]))[:10]
    p.edited_at = utcnow()
    db.commit()
    return serialize(db, [p], user, full=True)[0]


def delete(db: Session, pid: int, user: User) -> None:
    p = _get(db, pid)
    if p.user_id != user.id:
        raise Forbidden("you can only delete your own discussions")
    db.delete(p)
    db.commit()


def get(db: Session, pid: int, viewer: User | None) -> dict[str, Any]:
    return serialize(db, [_get(db, pid)], viewer, full=True)[0]


def listing(
    db: Session,
    viewer: User | None,
    symbol: str | None = None,
    sort: str = "new",
    sentiment: str | None = None,
    topic: str | None = None,
    author: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
    source: str | None = None,
) -> dict[str, Any]:
    """One page of discussions. ``new`` pages by id; ``top`` (most engagement, last 90 days) pages by offset."""
    limit = max(1, min(limit, 50))
    q = _discussions()
    if symbol:
        q = q.where(Post.asset == markets.clean_symbol(symbol))
    if sentiment:
        q = q.where(_effective() == _sentiment(sentiment))
    if topic:
        _topics([topic])
        q = q.where(Post.id.in_(select(PostTopic.post_id).where(PostTopic.topic == topic)))
    if author:
        q = q.join(User, User.id == Post.user_id).where(User.username == author.lower())
    if source == "community":
        q = q.where(_from(COMMUNITY))
    elif source == "research":
        q = q.where(_from(EDITORIAL))
    elif source == "generated":
        q = q.where(_from(GENERATED))
    if sort == "top":
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        q = q.where(Post.created_at >= utcnow() - SCORE_WINDOW).order_by(
            (Post.like_count * 2 + Post.comment_count * 3).desc(), Post.id.desc()
        )
        rows = list(db.scalars(q.offset(offset).limit(limit + 1)))
        nxt = str(offset + limit) if len(rows) > limit else None
    else:
        # Newest by publication time (Nexis Research items are back-dated, so ids don't follow time).
        # The cursor is "<epoch-microseconds>.<id>" of the last row on the previous page.
        if cursor and re.fullmatch(r"\d+\.\d+", cursor):
            us, last_id = (int(x) for x in cursor.split("."))
            at = datetime(1970, 1, 1) + timedelta(microseconds=us)
            q = q.where(or_(Post.created_at < at, and_(Post.created_at == at, Post.id < last_id)))
        rows = list(db.scalars(q.order_by(Post.created_at.desc(), Post.id.desc()).limit(limit + 1)))
        last = rows[limit - 1] if len(rows) > limit else None
        nxt = f"{(last.created_at - datetime(1970, 1, 1)) // timedelta(microseconds=1)}.{last.id}" if last else None
    return {"items": serialize(db, rows[:limit], viewer), "next": nxt}


# ------------------------------------------------------------------ statistics


def _blank() -> dict[str, int]:
    return {s: 0 for s in SENTIMENTS} | {"unclassified": 0}


def _counts(
    db: Session, symbol: str | None, since: Any = None, until: Any = None, sources: tuple[str, ...] | None = COMMUNITY
) -> dict[str, int]:
    q = select(_effective(), func.count(Post.id)).where(_is_discussion(), _from(sources))
    if symbol:
        q = q.where(Post.asset == symbol)
    if since is not None:
        q = q.where(Post.created_at >= since)
    if until is not None:
        q = q.where(Post.created_at < until)
    out = _blank()
    for eff, n in db.execute(q.group_by(_effective())):
        out[eff if eff in SENTIMENTS else "unclassified"] += n
    return out


def _share(counts: dict[str, int]) -> dict[str, float] | None:
    n = sum(counts[s] for s in SENTIMENTS)
    return {s: round(100 * counts[s] / n, 1) for s in SENTIMENTS} if n else None


def _volume(db: Session, symbol: str | None) -> dict[str, Any]:
    now = utcnow()
    base = select(func.count(Post.id)).where(_is_discussion())
    if symbol:
        base = base.where(Post.asset == symbol)
    week = db.scalar(base.where(Post.created_at >= now - timedelta(days=7))) or 0
    prev = db.scalar(base.where(Post.created_at >= now - timedelta(days=14), Post.created_at < now - timedelta(days=7))) or 0
    change = round(100 * (week - prev) / prev, 1) if prev else None
    return {"last_7_days": week, "previous_7_days": prev, "change_pct": change}


def _topic_counts(db: Session, symbol: str | None, days: int = 30, limit: int = 8) -> list[dict[str, Any]]:
    q = (
        select(PostTopic.topic, func.count(PostTopic.post_id))
        .join(Post, Post.id == PostTopic.post_id)
        .where(_is_discussion(), Post.created_at >= utcnow() - timedelta(days=days))
    )
    if symbol:
        q = q.where(Post.asset == symbol)
    rows = db.execute(q.group_by(PostTopic.topic).order_by(func.count(PostTopic.post_id).desc()).limit(limit)).all()
    return [{"key": t, "label": TOPICS.get(t, t), "count": n} for t, n in rows]


def _history(db: Session, symbol: str, weeks: int = 12) -> list[dict[str, Any]]:
    now = utcnow()
    start = now - timedelta(weeks=weeks)
    q = select(Post.created_at, _effective(), Post.source).where(_is_discussion(), Post.asset == symbol, Post.created_at >= start)
    buckets = [_blank() for _ in range(weeks)]
    research = [0] * weeks
    for created, eff, src in db.execute(q):
        i = min(weeks - 1, int((created - start).total_seconds() // (7 * 86400)))
        if src in COMMUNITY:
            buckets[i][eff if eff in SENTIMENTS else "unclassified"] += 1
        else:
            research[i] += 1
    # ``counts`` and ``score`` are community only; ``research`` counts Nexis Research discussions that week.
    return [
        {"week_start": (start + timedelta(weeks=i)).date().isoformat(), "counts": b, "research": research[i],
         "total": sum(b.values()) + research[i], "score": pulse_score.score(b)["value"]}
        for i, b in enumerate(buckets)
    ]  # fmt: skip


def _arguments(db: Session, symbol: str, side: str, viewer: User | None, limit: int = 3) -> list[dict[str, Any]]:
    q = (
        _discussions()
        .where(Post.asset == symbol, _effective() == side, Post.created_at >= utcnow() - SCORE_WINDOW)
        .order_by((Post.like_count * 2 + Post.comment_count * 3).desc(), Post.id.desc())
        .limit(limit)
    )
    return serialize(db, list(db.scalars(q)), viewer)


def asset(db: Session, symbol: str, viewer: User | None) -> dict[str, Any]:
    sym = markets.clean_symbol(symbol)
    recent = _counts(db, sym, utcnow() - SCORE_WINDOW)
    research_recent = _counts(db, sym, utcnow() - SCORE_WINDOW, sources=EDITORIAL)
    generated_recent = _counts(db, sym, utcnow() - SCORE_WINDOW, sources=GENERATED)
    generated_total = db.scalar(select(func.count(Post.id)).where(_is_discussion(), _from(GENERATED), Post.asset == sym)) or 0
    ai_read = (
        db.scalar(
            select(func.count(Post.id)).where(
                _is_discussion(),
                _from(COMMUNITY),
                Post.asset == sym,
                Post.created_at >= utcnow() - SCORE_WINDOW,
                Post.sentiment.is_(None),
                Post.ai_sentiment.is_not(None),
            )
        )
        or 0
    )
    total = db.scalar(select(func.count(Post.id)).where(_is_discussion(), Post.asset == sym)) or 0
    research_total = db.scalar(select(func.count(Post.id)).where(_is_discussion(), _from(EDITORIAL), Post.asset == sym)) or 0
    participants = (
        db.scalar(select(func.count(func.distinct(Post.user_id))).where(_is_discussion(), _from(COMMUNITY), Post.asset == sym))
        or 0
    )
    try:
        q = (markets.quotes(db, [sym]) or [None])[0]
    except NexisError:
        q = None
    stored_name = db.scalar(select(Post.asset_name).where(Post.asset == sym).order_by(Post.id.desc()).limit(1))
    history = _history(db, sym)
    return {
        "symbol": sym,
        "name": (q or {}).get("name") or stored_name or sym,
        "quote": {k: q.get(k) for k in ("price", "change", "change_pct", "currency", "type", "exchange", "market_time")}
        if q
        else None,
        "total_discussions": total,
        "community_discussions": total - research_total - generated_total,
        "research_discussions": research_total,
        "participants": participants,
        "score": pulse_score.score(recent) | {"window_days": SCORE_WINDOW.days, "ai_classified": ai_read},
        "sentiment": {"counts": recent, "share": _share(recent)},
        # Nexis Research's own stance: editorial, shown separately and never mixed into community sentiment.
        "research": {
            "discussions": research_total,
            "sentiment": {"counts": research_recent, "share": _share(research_recent)},
            "view": pulse_score.score(research_recent),
        },
        "volume": _volume(db, sym),
        "topics": _topic_counts(db, sym),
        "history": history if sum(1 for h in history if h["total"]) >= 2 else None,
        "generated": {
            "discussions": generated_total,
            "sentiment": {"counts": generated_recent, "share": _share(generated_recent)},
        },
        "voices": _voices(db, sym),
        "arguments": {"bullish": _arguments(db, sym, "bullish", viewer), "bearish": _arguments(db, sym, "bearish", viewer)},
        "sources": pulse_sources.describe(),
        "disclaimer": DISCLAIMER,
    }


def _per_asset(db: Session, since: Any, until: Any = None, sources: tuple[str, ...] | None = None) -> dict[str, dict[str, int]]:
    q = select(Post.asset, _effective(), func.count(Post.id)).where(_is_discussion(), _from(sources), Post.created_at >= since)
    if until is not None:
        q = q.where(Post.created_at < until)
    out: dict[str, dict[str, int]] = {}
    for a, eff, n in db.execute(q.group_by(Post.asset, _effective())):
        out.setdefault(a, _blank())[eff if eff in SENTIMENTS else "unclassified"] += n
    return out


def discover(db: Session, viewer: User | None) -> dict[str, Any]:
    now = utcnow()
    totals = {
        "discussions": db.scalar(select(func.count(Post.id)).where(_is_discussion())) or 0,
        "assets": db.scalar(select(func.count(func.distinct(Post.asset))).where(_is_discussion())) or 0,
        "community_discussions": db.scalar(select(func.count(Post.id)).where(_is_discussion(), _from(COMMUNITY))) or 0,
        "research_discussions": db.scalar(select(func.count(Post.id)).where(_is_discussion(), _from(EDITORIAL))) or 0,
        "participants": db.scalar(select(func.count(func.distinct(Post.user_id))).where(_is_discussion(), _from(COMMUNITY))) or 0,
    }
    empty = {
        "trending_assets": [],
        "most_discussed": [],
        "sentiment_changes": [],
        "trending_discussions": [],
        "recent": [],
        "topics": [],
    }
    if not totals["discussions"]:
        return {"totals": totals, **empty, "disclaimer": DISCLAIMER}

    names = dict(db.execute(select(Post.asset, func.max(Post.asset_name)).where(_is_discussion()).group_by(Post.asset)).all())
    basis = _per_asset(db, now - SCORE_WINDOW, sources=COMMUNITY)
    research_basis = _per_asset(db, now - SCORE_WINDOW, sources=EDITORIAL)

    def row(a: str, n: int) -> dict[str, Any]:
        return {"symbol": a, "name": names.get(a) or a, "discussions": n, "score": pulse_score.score(basis.get(a, {})),
                "research_view": pulse_score.score(research_basis.get(a, {}))}  # fmt: skip

    week = _per_asset(db, now - timedelta(days=7))
    month = _per_asset(db, now - timedelta(days=30))
    # Sentiment changes are about the community, so they use member discussions only.
    recent14 = _per_asset(db, now - timedelta(days=14), sources=COMMUNITY)
    prior14 = _per_asset(db, now - timedelta(days=28), now - timedelta(days=14), sources=COMMUNITY)
    changes = []
    for a, c in recent14.items():
        before, after = pulse_score.score(prior14.get(a, {})), pulse_score.score(c)
        if before["available"] and after["available"] and after["value"] != before["value"]:
            changes.append({"symbol": a, "name": names.get(a) or a, "from": before["value"], "to": after["value"],
                            "change": after["value"] - before["value"], "label": after["label"]})  # fmt: skip
    changes.sort(key=lambda r: -abs(r["change"]))

    # Trending discussions: engagement in the last 14 days, discounted by age.
    pool = list(db.scalars(_discussions().where(Post.created_at >= now - timedelta(days=14)).order_by(Post.id.desc()).limit(300)))

    def heat(p: Post) -> float:
        hours = max(1.0, (now - p.created_at).total_seconds() / 3600)
        return (1 + p.like_count * 2 + p.comment_count * 3) / (hours + 2) ** 0.8

    hot = sorted((p for p in pool if p.like_count or p.comment_count), key=heat, reverse=True)[:6]
    return {
        "totals": totals,
        "trending_assets": [row(a, sum(c.values())) for a, c in sorted(week.items(), key=lambda kv: -sum(kv[1].values()))[:8]],
        "most_discussed": [row(a, sum(c.values())) for a, c in sorted(month.items(), key=lambda kv: -sum(kv[1].values()))[:8]],
        "sentiment_changes": changes[:6],
        "trending_discussions": serialize(db, hot, viewer),
        "recent": listing(db, viewer, limit=8)["items"],
        "topics": _topic_counts(db, None, days=7),
        "disclaimer": DISCLAIMER,
    }


def user_activity(db: Session, username: str, viewer: User | None) -> dict[str, Any]:
    u = db.scalars(select(User).where(User.username == username.lower(), User.is_disabled.is_(False))).first()
    if u is None:
        raise NotFoundError("user not found")
    assets = db.execute(
        select(Post.asset, func.max(Post.asset_name), func.count(Post.id))
        .where(_is_discussion(), Post.user_id == u.id)
        .group_by(Post.asset)
        .order_by(func.count(Post.id).desc())
        .limit(12)
    ).all()
    stance = db.execute(
        select(Post.sentiment, func.count(Post.id)).where(_is_discussion(), Post.user_id == u.id).group_by(Post.sentiment)
    ).all()
    return {
        "assets": [{"symbol": a, "name": n or a, "discussions": c} for a, n, c in assets],
        "sentiment": {(s or "none"): c for s, c in stance},
        "discussions": listing(db, viewer, author=u.username, limit=10),
    }


# ------------------------------------------------------------------ AI summary

SUMMARY_PROMPT = """You summarise the discussions on Nexis about {name} ({symbol}).
Each numbered discussion says who wrote it: a Nexis member, or Nexis Research (the Nexis editorial team).
Use ONLY these discussions; never add facts, prices or news that are not in them. They are opinions and analysis:
describe what is argued, not what is true. When a point comes only from Nexis Research, say so rather than presenting
it as what members think. Cite the discussions behind each point by number.
If there are few discussions, say so plainly.

Also classify the sentiment each discussion expresses toward the asset, from its text alone, as bullish, neutral or bearish.

Return JSON only:
{{"summary": "2-3 sentences on the overall tone and the main reasons",
  "themes": [{{"title": "short", "detail": "one sentence", "refs": [1]}}],
  "bull": [{{"point": "a reason members give for being positive", "refs": [1]}}],
  "bear": [{{"point": "a concern or reason members give for being negative", "refs": [2]}}],
  "classifications": [{{"n": 1, "sentiment": "bullish"}}]}}
At most 4 themes and 4 points per list. Leave a list empty rather than inventing.

Discussions:
{items}"""


def summary(db: Session, symbol: str) -> dict[str, Any]:
    """AI summary of recent discussions, plus the AI's own sentiment reading of each one (stored separately)."""
    sym = markets.clean_symbol(symbol)
    q = (
        _discussions()
        .where(Post.asset == sym, Post.created_at >= utcnow() - SCORE_WINDOW)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(40)
    )
    posts = list(db.scalars(q))
    if len(posts) < 2:
        return {"available": False, "reason": "At least two discussions are needed before there is anything to summarise."}
    stamp = hashlib.sha1(
        json.dumps([(p.id, p.edited_at.isoformat() if p.edited_at else "") for p in posts]).encode()
    ).hexdigest()[:16]
    name = posts[0].asset_name or sym

    def fetch() -> dict[str, Any]:
        lines = []
        for n, p in enumerate(posts, 1):
            chose = f" · author marked it {p.sentiment}" if p.sentiment else ""
            who = "Nexis Research" if p.source in EDITORIAL else "Nexis member"
            lines.append(f"[{n}] ({who}) {p.title}{chose}\n{_excerpt(p.body, 900)}")
        try:
            msg = llm.chat(
                [{"role": "system", "content": "You write careful, neutral summaries of investor discussions. Output valid JSON only."},
                 {"role": "user", "content": SUMMARY_PROMPT.format(name=name, symbol=sym, items="\n\n".join(lines))}],
                max_tokens=1400, temperature=0.2,
            )  # fmt: skip
        except llm.LLMUnavailable as exc:
            raise ProviderError(f"the AI model is unavailable ({exc.reason})") from exc
        m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            out = None
        if not isinstance(out, dict):
            raise ProviderError("the AI model returned an unreadable summary")

        def refs(r: Any) -> list[int]:
            return [posts[int(x) - 1].id for x in (r or []) if isinstance(x, int | float) and 1 <= int(x) <= len(posts)][:6]

        def points(key: str, field: str) -> list[dict[str, Any]]:
            items = [p for p in (out.get(key) or [])[:4] if isinstance(p, dict) and p.get(field)]
            return [{"text": str(p[field])[:400], "refs": refs(p.get("refs"))} for p in items]

        detected = {}
        for c in out.get("classifications") or []:
            ok = isinstance(c, dict) and isinstance(c.get("n"), int | float) and c.get("sentiment") in SENTIMENTS
            if ok and 1 <= int(c["n"]) <= len(posts):
                detected[str(posts[int(c["n"]) - 1].id)] = c["sentiment"]
        return {
            "summary": str(out.get("summary", ""))[:900],
            "themes": [{"title": str(t.get("title", ""))[:80], "text": str(t.get("detail", ""))[:300], "refs": refs(t.get("refs"))}
                       for t in (out.get("themes") or [])[:4] if isinstance(t, dict) and t.get("title")],
            "bull": points("bull", "point"),
            "bear": points("bear", "point"),
            "detected": detected,
            "model": msg.get("_model"),
        }  # fmt: skip

    try:
        value, meta = markets.cached(db, f"pulse-summary:{sym}:{stamp}", timedelta(hours=6), fetch)
    except ProviderError as exc:
        return {"available": False, "reason": exc.message}
    # Store the AI's reading next to (never over) the author's own choice.
    changed = False
    for p in posts:
        ai = value["detected"].get(str(p.id))
        if ai and p.ai_sentiment != ai:
            p.ai_sentiment, changed = ai, True
    if changed:
        db.commit()
    return {
        "available": True,
        **{k: v for k, v in value.items() if k != "detected"},
        "discussions_used": len(posts),
        "titles": {str(p.id): p.title for p in posts},
        "generated_at": meta["fetched_at"],
    }


# ------------------------------------------------------------------ feed, search, people


def _voices(db: Session, symbol: str, limit: int = 8) -> list[dict[str, Any]]:
    """Who is discussing an asset: authors of discussions and comments in the last 30 days."""
    since = utcnow() - timedelta(days=30)
    ids = select(Post.id).where(_is_discussion(), Post.asset == symbol)
    counts: dict[int, int] = {}
    for uid, n in db.execute(
        select(Post.user_id, func.count(Post.id)).where(Post.id.in_(ids), Post.created_at >= since).group_by(Post.user_id)
    ):
        counts[uid] = counts.get(uid, 0) + n
    for uid, n in db.execute(
        select(Comment.user_id, func.count(Comment.id))
        .where(Comment.post_id.in_(ids), Comment.created_at >= since)
        .group_by(Comment.user_id)
    ):
        counts[uid] = counts.get(uid, 0) + n
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([u for u, _ in top])))}
    labels = {
        p.user_id: p.traits.get("label") for p in db.scalars(select(Persona).where(Persona.user_id.in_([u for u, _ in top])))
    }
    return [
        serialize_user(db, users[u]) | {"kind": users[u].kind, "label": labels.get(u), "contributions": n}
        for u, n in top
        if u in users
    ]


def feed(
    db: Session,
    viewer: User | None,
    mode: str = "latest",
    topic: str | None = None,
    source: str | None = None,
    cursor: str | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    """The Pulse feed across every source, with a preview of the latest reply."""
    limit = max(1, min(limit, 40))
    q = _discussions()
    if topic:
        _topics([topic])
        q = q.where(Post.id.in_(select(PostTopic.post_id).where(PostTopic.topic == topic)))
    if source in ("community", "research", "generated"):
        q = q.where(_from({"community": COMMUNITY, "research": EDITORIAL, "generated": GENERATED}[source]))
    if mode == "following":
        if viewer is None:
            return {"items": [], "next": None, "needs_sign_in": True}
        from app.models import Follow, TopicFollow, UserHolding

        syms = set(db.scalars(select(TopicFollow.value).where(TopicFollow.user_id == viewer.id, TopicFollow.kind == "symbol")))
        syms |= set(db.scalars(select(UserHolding.symbol).where(UserHolding.user_id == viewer.id)))
        people = select(Follow.followee_id).where(Follow.follower_id == viewer.id)
        q = q.where(or_(Post.asset.in_(syms or {"-"}), Post.user_id.in_(people)))
    elif mode == "saved":
        if viewer is None:
            return {"items": [], "next": None, "needs_sign_in": True}
        q = q.where(Post.id.in_(select(Save.post_id).where(Save.user_id == viewer.id)))
    if mode == "trending":
        now = utcnow()
        pool = list(db.scalars(q.where(Post.created_at >= now - timedelta(days=5)).order_by(Post.created_at.desc()).limit(400)))

        def heat(p: Post) -> float:
            hours = max(1.0, (now - p.created_at).total_seconds() / 3600)
            return (1 + p.like_count * 3 + p.comment_count) / (hours + 3) ** 0.7

        pool.sort(key=heat, reverse=True)
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        page = pool[offset : offset + limit]
        nxt = str(offset + limit) if len(pool) > offset + limit else None
        return {"items": serialize(db, page, viewer, preview=True), "next": nxt}
    if cursor and re.fullmatch(r"\d+\.\d+", cursor):
        us, last_id = (int(x) for x in cursor.split("."))
        at = datetime(1970, 1, 1) + timedelta(microseconds=us)
        q = q.where(or_(Post.created_at < at, and_(Post.created_at == at, Post.id < last_id)))
    rows = list(db.scalars(q.order_by(Post.created_at.desc(), Post.id.desc()).limit(limit + 1)))
    last = rows[limit - 1] if len(rows) > limit else None
    nxt = f"{(last.created_at - datetime(1970, 1, 1)) // timedelta(microseconds=1)}.{last.id}" if last else None
    return {"items": serialize(db, rows[:limit], viewer, preview=True), "next": nxt}


def search(db: Session, viewer: User | None, q: str) -> dict[str, Any]:
    term = q.strip()
    if len(term) < 2:
        return {"assets": [], "discussions": [], "topics": [], "people": []}
    like = f"%{term.lower()}%"
    try:
        assets = markets.search(db, term)[:6]
    except NexisError:
        assets = []
    discussed = list(db.execute(
        select(Post.asset, func.max(Post.asset_name), func.count(Post.id)).where(_is_discussion(), or_(func.lower(Post.asset).like(like), func.lower(Post.asset_name).like(like)))
        .group_by(Post.asset).order_by(func.count(Post.id).desc()).limit(6)
    ).all())  # fmt: skip
    disc = list(db.scalars(_discussions().where(or_(func.lower(Post.title).like(like), func.lower(Post.body).like(like), func.lower(Post.asset).like(like)))
                           .order_by(Post.created_at.desc()).limit(12)))  # fmt: skip
    topics = [{"key": k, "label": v} for k, v in TOPICS.items() if term.lower() in v.lower() or term.lower() == k]
    people = list(db.scalars(select(User).where(User.kind.in_(["person", "persona", "editorial"]), User.is_disabled.is_(False),
                                                or_(func.lower(User.username).like(like), func.lower(User.display_name).like(like), func.lower(User.bio).like(like)))
                             .limit(8)))  # fmt: skip
    return {
        "assets": [
            {"symbol": a["symbol"], "name": a.get("name"), "type": a.get("type"), "exchange": a.get("exchange")} for a in assets
        ],
        "discussed_assets": [{"symbol": a, "name": n or a, "discussions": c} for a, n, c in discussed],
        "discussions": serialize(db, disc, viewer),
        "topics": topics,
        "people": [serialize_user(db, u) | {"kind": u.kind, "bio": u.bio} for u in people],
    }


def persona_profile(db: Session, username: str, viewer: User | None) -> dict[str, Any]:
    u = db.scalars(select(User).where(User.username == username.lower(), User.kind == "persona")).first()
    if u is None:
        raise NotFoundError("persona not found")
    p = db.scalars(select(Persona).where(Persona.user_id == u.id)).first()
    from app.services import personas as personas_svc

    comments = list(db.execute(select(Comment, Post).join(Post, Post.id == Comment.post_id).where(Comment.user_id == u.id, Post.hidden.is_(False))
                               .order_by(Comment.created_at.desc()).limit(10)).all())  # fmt: skip
    return {
        "user": serialize_user(db, u) | {"kind": u.kind, "bio": u.bio},
        "profile": personas_svc.public(p) if p else None,
        "generated": True,
        "discussions": listing(db, viewer, author=u.username, limit=10)["items"],
        "recent_comments": [
            {
                "id": c.id,
                "body": _excerpt(c.body, 220),
                "created_at": c.created_at.isoformat() + "Z",
                "discussion": {"id": post.id, "title": post.title, "asset": post.asset},
            }
            for c, post in comments
        ],
        "assets": sorted((p.memory or {}).keys())[:12] if p else [],
    }


def related(db: Session, pid: int, viewer: User | None, limit: int = 4) -> list[dict[str, Any]]:
    p = _get(db, pid)
    rows = list(
        db.scalars(_discussions().where(Post.asset == p.asset, Post.id != p.id).order_by(Post.created_at.desc()).limit(limit))
    )
    return serialize(db, rows, viewer)
