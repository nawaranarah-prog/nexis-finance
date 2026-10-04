"""Nexis Pulse: anonymous financial discussions, and Nexis editorial discussions next to them.

Two kinds of content, never mixed:

* **Community** — written by signed-in members and shown as "Anonymous". The account behind a post is stored
  (``author_id``) so Nexis can moderate, rate-limit, delete and answer lawful requests, but it is never part of any
  public response: serializers here build the author object from constants and never read ``author_id`` into output.
* **Nexis editorial** — context Nexis maintains about current developments (``pulse_editorial``): what happened,
  what is debated, the bull and bear cases, open questions, sources and a timestamped update history. It is labelled
  "Nexis" and AI-assisted pieces say so.

Every count shown (comments, participants, reactions, follows, trending) comes from rows real members created.
Nothing in this module creates comments, reactions or engagement on anyone's behalf.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Forbidden, NexisError, NotFoundError, ValidationFailed
from app.db.base import utcnow
from app.models import (
    ContentReport,
    ModerationAction,
    PulseComment,
    PulseDiscussion,
    PulseDiscussionAsset,
    PulseDiscussionSource,
    PulseDiscussionTopic,
    PulseDiscussionUpdate,
    PulseFollow,
    PulseReaction,
    PulseSave,
    User,
)
from app.services import legal, markets, pulse_safety, ratelimit

# ------------------------------------------------------------------ vocabulary

TOPICS: dict[str, str] = {
    "us_markets": "US markets", "uae_markets": "UAE markets", "ai": "AI", "semiconductors": "Semiconductors",
    "technology": "Technology", "banks": "Banks", "fintech": "Fintech", "energy": "Oil & energy", "rates": "Interest rates",
    "inflation": "Inflation", "macro": "Macroeconomics", "earnings": "Earnings", "valuation": "Valuation", "ipos": "IPOs",
    "bonds": "Bonds", "crypto": "Crypto", "real_estate": "Real estate", "regulation": "Regulation", "deals": "M&A and deals",
    "dividends": "Dividends", "growth": "Growth", "revenue": "Revenue", "margins": "Margins", "competition": "Competition",
    "management": "Management", "products": "Products", "risk": "Risk", "debt": "Debt", "technicals": "Technical analysis",
}  # fmt: skip
# Shown in navigation; the rest exist for older discussions and finer tagging.
PRIMARY_TOPICS = ("us_markets", "uae_markets", "ai", "semiconductors", "technology", "banks", "fintech", "energy", "rates",
                  "inflation", "macro", "earnings", "valuation", "ipos", "bonds", "crypto", "real_estate")  # fmt: skip
MAX_TOPICS = 3
STANCES = ("bullish", "bearish", "neutral", "question")
COMMENT_STANCES = ("agree", "disagree", "question")
REACTIONS = ("agree", "disagree", "interesting")
REPORT_REASONS = {
    "manipulation": "Market manipulation or coordinated trading", "scam": "Scam or fraudulent promotion",
    "insider": "Claims inside information", "impersonation": "Impersonates a person or company",
    "misinformation": "Knowingly false information", "harassment": "Harassment or abuse", "spam": "Spam",
    "private_info": "Shares private information", "illegal": "Illegal content", "other": "Something else",
}  # fmt: skip
SORTS = ("latest", "trending", "discussed", "activity", "following", "for_you", "saved")
MAX_DEPTH = 8

ANONYMOUS = {"display_name": "Anonymous", "type": "community"}
NEXIS = {"display_name": "Nexis", "type": "editorial", "label": "Nexis Editorial"}
DISCLAIMER = (
    "Community posts are the opinions of anonymous members. Nexis editorial content is context, not advice. "
    "Nothing on Pulse is a recommendation to buy or sell."
)

_TOPIC_RX: list[tuple[str, re.Pattern[str]]] = [(k, re.compile(rx, re.I)) for k, rx in (
    ("us_markets", r"\b(s&p ?500|s&p|nasdaq|dow jones|wall street|us stocks|u\.s\. stocks|nyse|russell 2000)\b"),
    ("uae_markets", r"\b(uae|dubai|abu dhabi|adx|dfm|emirati|emirates)\b"),
    ("ai", r"\b(ai|a\.i\.|artificial intelligence|llms?|gpus?|data cent(er|re)s?|inference|hyperscalers?|capex)\b"),
    ("semiconductors", r"\b(semiconductors?|semis|chips?|chipmakers?|foundry|foundries|tsmc|wafers?)\b"),
    ("technology", r"\b(tech|software|cloud|saas|big tech)\b"),
    ("banks", r"\b(banks?|banking|lenders?|net interest margins?|deposits?)\b"),
    ("fintech", r"\b(fintech|payments?|stablecoins?|neobanks?|digital wallets?)\b"),
    ("energy", r"\b(oil|opec\+?|brent|crude|natural gas|lng|energy)\b"),
    ("rates", r"\b(interest rates?|rate cuts?|rate hikes?|the fed|federal reserve|central banks?|yields?|treasur(y|ies))\b"),
    ("inflation", r"\b(inflation|cpi|pce|disinflation|price pressures?)\b"),
    ("macro", r"\b(gdp|recession|economy|economic|macro|tariffs?|unemployment|jobs report|payrolls)\b"),
    ("earnings", r"\b(earnings|quarterly results|eps|guidance|beat expectations|missed expectations)\b"),
    ("valuation", r"\b(valuations?|p/?e|multiples?|overvalued|undervalued|priced in|expensive|cheap)\b"),
    ("ipos", r"\b(ipos?|initial public offering|listing debut|goes public|going public)\b"),
    ("bonds", r"\b(bonds?|sukuk|credit spreads?|fixed income|coupons?)\b"),
    ("crypto", r"\b(bitcoin|btc|ethereum|eth|crypto\w*)\b"),
    ("real_estate", r"\b(real estate|property|properties|housing|off-plan)\b"),
    ("regulation", r"\b(regulat\w+|antitrust|lawsuit|probe|sanctions?)\b"),
    ("deals", r"\b(acqui\w+|mergers?|takeover|buyout)\b"),
    ("dividends", r"\b(dividends?|payout|buybacks?)\b"),
)]  # fmt: skip
_SECTOR_TOPIC = {"ai": "ai", "technology": "technology", "banks": "banks", "fintech": "fintech", "energy": "energy", "real_estate": "real_estate"}
_SEMIS = {"NVDA", "AMD", "AVGO", "TSM", "INTC", "QCOM", "MU", "ASML", "ARM"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def symbol_topics(symbol: str) -> list[str]:
    """Topics an asset belongs to (no network calls)."""
    from app.services.events import SECTOR_BY_SYMBOL

    s = symbol.upper()
    out: list[str] = []
    if s.endswith((".AE", ".AD", ".BOND")):
        out.append("uae_markets")
    elif s.endswith("-USD"):
        out.append("crypto")
    elif s.endswith("=F"):
        out.append("energy" if s in ("BZ=F", "CL=F", "NG=F") else "macro")
    elif s.startswith("^"):
        out.append("us_markets" if s in ("^GSPC", "^IXIC", "^DJI", "^RUT") else "macro")
    elif "." not in s and "=" not in s:
        out.append("us_markets")
    if s.endswith(".BOND"):
        out.append("bonds")
    if s in _SEMIS:
        out += ["semiconductors", "ai"]
    sector = _SECTOR_TOPIC.get(SECTOR_BY_SYMBOL.get(s, ""))
    if sector:
        out.append(sector)
    return list(dict.fromkeys(out))


def detect_topics(text: str, symbols: list[str] | None = None, limit: int = MAX_TOPICS) -> list[str]:
    found = [k for k, rx in _TOPIC_RX if rx.search(text or "")]
    for s in symbols or []:
        found += symbol_topics(s)
    return list(dict.fromkeys(found))[:limit]


# ------------------------------------------------------------------ small helpers


def new_public_id(db: Session) -> str:
    while True:
        v = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        if v.isdigit():
            continue
        if db.scalar(select(PulseDiscussion.id).where(PulseDiscussion.public_id == v)) is None:
            return v


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:70].rstrip("-") or "discussion"


def url_for(d: PulseDiscussion) -> str:
    return f"/pulse/d/{d.public_id}/{slugify(d.title)}"


def _iso(dt: Any) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _text(raw: str | None, lo: int, hi: int, what: str) -> str:
    text = _CONTROL.sub("", (raw or "").replace("\r\n", "\n")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", "  ", text)
    if len(text) < lo or len(text) > hi:
        raise ValidationFailed(f"{what} must be {lo}–{hi:,} characters" if lo else f"{what} can be up to {hi:,} characters")
    return text


def _excerpt(body: str, n: int = 260) -> str:
    one = re.sub(r"\s+", " ", body or "").strip()
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def _like(term: str) -> str:
    return "%" + term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _topics_in(raw: list[str] | None) -> list[str]:
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


def _visible_to(viewer: User | None):  # type: ignore[no-untyped-def]
    """Publicly visible, or the viewer's own post waiting for review."""
    if viewer is None:
        return PulseDiscussion.status == "visible"
    return or_(PulseDiscussion.status == "visible", and_(PulseDiscussion.status == "held", PulseDiscussion.author_id == viewer.id))


def get_discussion(db: Session, public_id: str, viewer: User | None = None) -> PulseDiscussion:
    d = db.scalars(select(PulseDiscussion).where(PulseDiscussion.public_id == str(public_id)[:16])).first()
    if d is None or not (d.status == "visible" or (d.status == "held" and viewer is not None and d.author_id == viewer.id)):
        raise NotFoundError("discussion not found")
    return d


def require_participant(db: Session, user: User) -> None:
    """Posting needs the current Terms accepted and no active posting suspension."""
    legal.require_current(db, user)
    if user.posting_suspended_until and user.posting_suspended_until > utcnow():
        raise Forbidden(f"posting in Pulse is paused for this account until {user.posting_suspended_until:%d %b %Y}")


# ------------------------------------------------------------------ serialization (public; anonymous by construction)


def _viewer_state(db: Session, viewer: User | None, ds: list[PulseDiscussion]) -> dict[str, Any]:
    if viewer is None or not ds:
        return {"saved": set(), "following": set(), "reactions": {}}
    ids = [d.id for d in ds]
    saved = set(db.scalars(select(PulseSave.discussion_id).where(PulseSave.user_id == viewer.id, PulseSave.discussion_id.in_(ids))))
    following = set(db.scalars(select(PulseFollow.discussion_id).where(PulseFollow.user_id == viewer.id, PulseFollow.discussion_id.in_(ids))))
    reactions: dict[int, list[str]] = {}
    for tid, kind in db.execute(select(PulseReaction.target_id, PulseReaction.kind).where(
            PulseReaction.user_id == viewer.id, PulseReaction.target_type == "discussion", PulseReaction.target_id.in_(ids))):  # fmt: skip
        reactions.setdefault(tid, []).append(kind)
    return {"saved": saved, "following": following, "reactions": reactions}


def _topics_of(db: Session, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for did, t in db.execute(select(PulseDiscussionTopic.discussion_id, PulseDiscussionTopic.topic).where(PulseDiscussionTopic.discussion_id.in_(ids))):
        out.setdefault(did, []).append(t)
    order = list(TOPICS)
    return {k: sorted(v, key=lambda t: order.index(t) if t in order else 99) for k, v in out.items()}


def _public_author(d: PulseDiscussion) -> dict[str, Any]:
    """A public thread is labelled by where it was posted, never by who posted it."""
    o = d.origin or {}
    return {"display_name": o.get("label") or "Public discussion", "type": "public", "platform": o.get("platform")}


def _origin(d: PulseDiscussion) -> dict[str, Any] | None:
    if d.kind != "public":
        return None
    o = d.origin or {}
    quotes = d.quotes or []
    stances: dict[str, int] = {}
    for q in quotes:
        stances[q.get("stance") or "context"] = stances.get(q.get("stance") or "context", 0) + 1
    return {"platform": o.get("platform"), "label": o.get("label"), "community": o.get("community"), "url": o.get("url"),
            "posted_at": o.get("posted_at"), "quotes": len(quotes), "stances": stances}  # fmt: skip


def serialize_cards(db: Session, ds: list[PulseDiscussion], viewer: User | None) -> list[dict[str, Any]]:
    if not ds:
        return []
    st = _viewer_state(db, viewer, ds)
    topics = _topics_of(db, [d.id for d in ds])
    out = []
    for d in ds:
        editorial = d.kind == "editorial"
        mine = viewer is not None and d.author_id is not None and d.author_id == viewer.id
        out.append({
            "id": d.public_id, "url": url_for(d), "kind": d.kind, "title": d.title,
            "excerpt": _excerpt(d.what_happened or d.body) if editorial else _excerpt(d.body),
            "stance": d.stance, "symbol": d.primary_symbol, "asset_name": d.asset_name, "symbols": list(d.symbols or [])[:6],
            "topics": [{"key": t, "label": TOPICS.get(t, t)} for t in topics.get(d.id, [])],
            "author": dict(NEXIS) if editorial else _public_author(d) if d.kind == "public" else dict(ANONYMOUS),
            "ai_assisted": d.ai_assisted if d.kind != "community" else False,
            "origin": _origin(d),
            "counts": {"comments": d.comment_count, "participants": d.participant_count, "followers": d.follower_count,
                       "agree": d.agree_count, "disagree": d.disagree_count, "interesting": d.interesting_count},
            "debate": {"bull": len(d.bull_case or []), "bear": len(d.bear_case or []), "open": len(d.open_questions or [])} if editorial else None,
            "created_at": _iso(d.created_at), "updated_at": _iso(d.content_updated_at), "last_activity_at": _iso(d.last_activity_at),
            "locked": d.locked,
            "viewer": {"saved": d.id in st["saved"], "following": d.id in st["following"], "reactions": st["reactions"].get(d.id, []),
                       "is_mine": mine, "pending_review": mine and d.status == "held"},
        })  # fmt: skip
    return out


def _sources(db: Session, d: PulseDiscussion) -> list[dict[str, Any]]:
    rows = db.scalars(select(PulseDiscussionSource).where(PulseDiscussionSource.discussion_id == d.id)
                      .order_by(PulseDiscussionSource.published_at.desc().nulls_last(), PulseDiscussionSource.id))  # fmt: skip
    return [{"id": s.id, "title": s.title, "url": s.url, "publisher": s.publisher, "published_at": _iso(s.published_at),
             "retrieved_at": _iso(s.retrieved_at)} for s in rows]  # fmt: skip


def _points(raw: list[Any] | None, valid: set[int]) -> list[dict[str, Any]]:
    out = []
    for p in raw or []:
        if isinstance(p, dict) and p.get("point"):
            out.append({"point": str(p["point"]), "source_ids": [int(s) for s in p.get("sources") or [] if s in valid]})
        elif isinstance(p, str) and p:
            out.append({"point": p, "source_ids": []})
    return out


def _disclosure(d: PulseDiscussion, sourced: bool) -> str:
    if sourced and d.ai_assisted:
        return "Written by Nexis with AI assistance from the sources listed and checked against them. It describes a debate; it is not advice."
    if sourced:
        return "Assembled by Nexis from the sources listed. It describes a debate; it is not advice."
    if d.ai_assisted:
        return "Nexis editorial commentary, drafted with AI assistance. It is an editorial view, not advice."
    return "Nexis editorial commentary. It is an editorial view, not advice."


def serialize_full(db: Session, d: PulseDiscussion, viewer: User | None) -> dict[str, Any]:
    card = serialize_cards(db, [d], viewer)[0]
    srcs = _sources(db, d)
    valid = {s["id"] for s in srcs}
    updates = db.scalars(select(PulseDiscussionUpdate).where(PulseDiscussionUpdate.discussion_id == d.id)
                         .order_by(PulseDiscussionUpdate.created_at.desc(), PulseDiscussionUpdate.id.desc()).limit(40))  # fmt: skip
    editorial = d.kind == "editorial"
    card |= {
        "body": d.body,
        "editorial": {
            "what_happened": d.what_happened, "debate": d.debate,
            "bull_case": _points(d.bull_case, valid), "bear_case": _points(d.bear_case, valid),
            "neutral_case": _points(d.neutral_case, valid),
            "open_questions": [q if isinstance(q, str) else str(q.get("question", "")) for q in d.open_questions or [] if q],
            "ai_assisted": d.ai_assisted,
            "disclosure": _disclosure(d, bool(srcs)),
        } if editorial else None,
        "public": {
            "debate": d.debate, "ai_assisted": d.ai_assisted,
            "quotes": [{"text": q.get("text"), "stance": q.get("stance") or "context", "url": q.get("url"), "at": q.get("at")} for q in d.quotes or []],
            "disclosure": (f"Collected from public posts on {(d.origin or {}).get('label') or 'another site'}. Usernames are removed and each quote is a "
                           "short excerpt linked to the original. Nexis hasn't checked what's said here. Not advice."
                           + (" Replies were sorted into arguments with AI assistance." if d.ai_assisted else "")),
        } if d.kind == "public" else None,
        "sources": srcs,
        "updates": [{"id": u.id, "kind": u.kind, "headline": u.headline, "body": u.body, "source_ids": [s for s in u.source_ids or [] if s in valid],
                     "created_at": _iso(u.created_at)} for u in updates],
    }
    return card


def _comment_out(c: PulseComment, op_id: int | None, viewer: User | None, mine_reactions: dict[int, list[str]]) -> dict[str, Any]:
    mine = viewer is not None and c.author_id is not None and c.author_id == viewer.id
    gone = c.status in ("deleted", "removed")
    return {
        "id": c.id, "parent_id": c.parent_id, "depth": c.depth,
        "body": None if gone else c.body,
        "stance": None if gone else c.stance,
        "status": "visible" if c.status == "visible" else ("pending_review" if c.status == "held" else c.status),
        "author": {**ANONYMOUS, "is_op": bool(op_id and c.author_id == op_id and not gone)},
        "counts": {"agree": c.agree_count, "disagree": c.disagree_count, "interesting": c.interesting_count},
        "viewer": {"is_mine": mine and not gone, "reactions": mine_reactions.get(c.id, [])},
        "created_at": _iso(c.created_at),
    }  # fmt: skip


def comments(db: Session, public_id: str, viewer: User | None, sort: str = "old") -> dict[str, Any]:
    """The thread as a flat list in display order (parents before replies); ``depth`` gives the indentation."""
    d = get_discussion(db, public_id, viewer)
    rows = list(db.scalars(select(PulseComment).where(PulseComment.discussion_id == d.id).order_by(PulseComment.created_at, PulseComment.id).limit(2000)))

    def shown(c: PulseComment) -> bool:
        return c.status == "visible" or (c.status == "held" and viewer is not None and c.author_id == viewer.id)

    kids: dict[int | None, list[PulseComment]] = {}
    for c in rows:
        kids.setdefault(c.parent_id, []).append(c)
    keep: dict[int, bool] = {}

    def alive(c: PulseComment) -> bool:  # shown itself, or has a shown reply (then it stays as a placeholder)
        if c.id not in keep:
            keep[c.id] = shown(c) or any(alive(k) for k in kids.get(c.id, []))
        return keep[c.id]

    def score(c: PulseComment) -> float:
        return c.agree_count + c.interesting_count * 1.5 + c.disagree_count * 0.5

    top = [c for c in kids.get(None, []) if alive(c)]
    if sort == "new":
        top.sort(key=lambda c: (c.created_at, c.id), reverse=True)
    elif sort == "top":
        top.sort(key=lambda c: (score(c), c.created_at), reverse=True)
    reacted: dict[int, list[str]] = {}
    if viewer is not None and rows:
        for tid, kind in db.execute(select(PulseReaction.target_id, PulseReaction.kind).where(
                PulseReaction.user_id == viewer.id, PulseReaction.target_type == "comment", PulseReaction.target_id.in_([c.id for c in rows]))):  # fmt: skip
            reacted.setdefault(tid, []).append(kind)
    op_id = d.author_id if d.kind == "community" else None
    out: list[dict[str, Any]] = []

    def walk(c: PulseComment) -> None:
        item = _comment_out(c, op_id, viewer, reacted)
        if not shown(c):  # kept only as a placeholder so its replies keep their context
            item |= {"body": None, "stance": None, "status": "removed" if c.status == "held" else c.status,
                     "author": {**ANONYMOUS, "is_op": False}, "viewer": {"is_mine": False, "reactions": []}}  # fmt: skip
        out.append(item)
        for k in kids.get(c.id, []):
            if alive(k):
                walk(k)

    for c in top:
        walk(c)
    return {"items": out, "count": d.comment_count, "locked": d.locked}


# ------------------------------------------------------------------ listing, sorting, personalisation


def tracked_symbols(db: Session, user: User) -> dict[str, str]:
    from app.services import portfolio

    return portfolio.tracked(db, user)


def _base(viewer: User | None, kind: str | None, topic: str | None, symbol: str | None, q: str | None):  # type: ignore[no-untyped-def]
    stmt = select(PulseDiscussion).where(PulseDiscussion.status == "visible")
    if kind in ("community", "editorial", "public"):
        stmt = stmt.where(PulseDiscussion.kind == kind)
    if topic:
        _topics_in([topic])
        stmt = stmt.where(PulseDiscussion.id.in_(select(PulseDiscussionTopic.discussion_id).where(PulseDiscussionTopic.topic == topic)))
    if symbol:
        sym = markets.clean_symbol(symbol)
        stmt = stmt.where(PulseDiscussion.id.in_(select(PulseDiscussionAsset.discussion_id).where(PulseDiscussionAsset.symbol == sym)))
    if q:
        like = _like(q.strip())
        stmt = stmt.where(or_(func.lower(PulseDiscussion.title).like(like, escape="\\"), func.lower(PulseDiscussion.body).like(like, escape="\\"),
                              func.lower(func.coalesce(PulseDiscussion.asset_name, "")).like(like, escape="\\"),
                              func.lower(func.coalesce(PulseDiscussion.primary_symbol, "")).like(like, escape="\\"),
                              func.lower(func.coalesce(PulseDiscussion.what_happened, "")).like(like, escape="\\")))  # fmt: skip
    return stmt


def _trending(db: Session, pool: list[PulseDiscussion]) -> list[tuple[float, PulseDiscussion]]:
    """Rank by real recent activity: distinct people replying, replies, reactions, follows and saves.

    Distinct participants weigh most, so one account replying many times cannot carry a discussion on its own.
    A discussion with no activity in the window has no trending score at all.
    """
    if not pool:
        return []
    now = utcnow()
    since, day = now - timedelta(hours=72), now - timedelta(hours=24)
    ids = [d.id for d in pool]
    people = dict(db.execute(select(PulseComment.discussion_id, func.count(func.distinct(PulseComment.author_id)))
                             .where(PulseComment.discussion_id.in_(ids), PulseComment.created_at >= since, PulseComment.status == "visible")
                             .group_by(PulseComment.discussion_id)).all())  # fmt: skip
    replies24 = dict(db.execute(select(PulseComment.discussion_id, func.count(PulseComment.id))
                                .where(PulseComment.discussion_id.in_(ids), PulseComment.created_at >= day, PulseComment.status == "visible")
                                .group_by(PulseComment.discussion_id)).all())  # fmt: skip
    replies72 = dict(db.execute(select(PulseComment.discussion_id, func.count(PulseComment.id))
                                .where(PulseComment.discussion_id.in_(ids), PulseComment.created_at >= since, PulseComment.status == "visible")
                                .group_by(PulseComment.discussion_id)).all())  # fmt: skip
    react_d = dict(db.execute(select(PulseReaction.target_id, func.count(PulseReaction.id))
                              .where(PulseReaction.target_type == "discussion", PulseReaction.target_id.in_(ids), PulseReaction.created_at >= since)
                              .group_by(PulseReaction.target_id)).all())  # fmt: skip
    react_c = dict(db.execute(select(PulseComment.discussion_id, func.count(PulseReaction.id))
                              .join(PulseComment, and_(PulseReaction.target_type == "comment", PulseReaction.target_id == PulseComment.id))
                              .where(PulseComment.discussion_id.in_(ids), PulseReaction.created_at >= since)
                              .group_by(PulseComment.discussion_id)).all())  # fmt: skip
    follows = dict(db.execute(select(PulseFollow.discussion_id, func.count(PulseFollow.id))
                              .where(PulseFollow.discussion_id.in_(ids), PulseFollow.created_at >= since).group_by(PulseFollow.discussion_id)).all())  # fmt: skip
    saves = dict(db.execute(select(PulseSave.discussion_id, func.count(PulseSave.id))
                            .where(PulseSave.discussion_id.in_(ids), PulseSave.created_at >= since).group_by(PulseSave.discussion_id)).all())  # fmt: skip
    ranked = []
    for d in pool:
        raw = (3.0 * people.get(d.id, 0) + 1.0 * replies24.get(d.id, 0) + 0.5 * (replies72.get(d.id, 0) - replies24.get(d.id, 0))
               + 0.4 * (react_d.get(d.id, 0) + react_c.get(d.id, 0)) + 1.0 * follows.get(d.id, 0) + 0.6 * saves.get(d.id, 0))  # fmt: skip
        if raw <= 0:
            continue
        idle_h = max(0.0, (now - d.last_activity_at).total_seconds() / 3600)
        ranked.append((raw / (idle_h + 2) ** 0.35, d))
    ranked.sort(key=lambda r: -r[0])
    return ranked


def _offset(cursor: str | None) -> int:
    return int(cursor) if cursor and cursor.isdigit() and int(cursor) < 100_000 else 0


def feed(
    db: Session,
    viewer: User | None,
    sort: str = "latest",
    kind: str | None = None,
    topic: str | None = None,
    symbol: str | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    off = _offset(cursor)
    stmt = _base(viewer, kind, topic, symbol, q)
    meta: dict[str, Any] = {"sort": sort}
    if sort in ("following", "saved", "for_you") and viewer is None:
        return {"items": [], "next": None, "needs_sign_in": True, **meta}
    if sort == "following":
        stmt = stmt.where(PulseDiscussion.id.in_(select(PulseFollow.discussion_id).where(PulseFollow.user_id == viewer.id)))  # type: ignore[union-attr]
    elif sort == "saved":
        stmt = stmt.where(PulseDiscussion.id.in_(select(PulseSave.discussion_id).where(PulseSave.user_id == viewer.id)))  # type: ignore[union-attr]
    if sort == "trending":
        pool = list(db.scalars(stmt.where(PulseDiscussion.last_activity_at >= utcnow() - timedelta(days=4))
                               .order_by(PulseDiscussion.last_activity_at.desc()).limit(400)))  # fmt: skip
        ranked = [d for _, d in _trending(db, pool)]
        if not ranked and off == 0:
            # Nothing has real activity yet: say so, and show the newest discussions instead of inventing a ranking.
            rows = list(db.scalars(stmt.order_by(PulseDiscussion.created_at.desc(), PulseDiscussion.id.desc()).limit(limit + 1)))
            return {"items": serialize_cards(db, rows[:limit], viewer), "next": str(limit) if len(rows) > limit else None,
                    "fallback": "latest", **meta}  # fmt: skip
        page = ranked[off : off + limit]
        return {"items": serialize_cards(db, page, viewer), "next": str(off + limit) if len(ranked) > off + limit else None, **meta}
    if sort == "for_you":
        tracked = tracked_symbols(db, viewer)  # type: ignore[arg-type]
        followed = set(db.scalars(select(PulseFollow.discussion_id).where(PulseFollow.user_id == viewer.id)))  # type: ignore[union-attr]
        if not tracked and not followed:
            return {"items": [], "next": None, "needs_tracking": True, **meta}
        topics = {t for s in tracked for t in symbol_topics(s) if t not in ("us_markets",)}
        cands = list(db.scalars(stmt.where(or_(
            PulseDiscussion.id.in_(select(PulseDiscussionAsset.discussion_id).where(PulseDiscussionAsset.symbol.in_(list(tracked) or ["-"]))),
            PulseDiscussion.id.in_(select(PulseDiscussionTopic.discussion_id).where(PulseDiscussionTopic.topic.in_(list(topics) or ["-"]))),
            PulseDiscussion.id.in_(list(followed) or [-1]),
        )).order_by(PulseDiscussion.last_activity_at.desc()).limit(400)))  # fmt: skip
        assets = {d: set() for d in [c.id for c in cands]}
        for did, sym in db.execute(select(PulseDiscussionAsset.discussion_id, PulseDiscussionAsset.symbol).where(PulseDiscussionAsset.discussion_id.in_(list(assets) or [-1]))):
            assets[did].add(sym)
        tmap = _topics_of(db, [c.id for c in cands])
        now = utcnow()

        def rel(d: PulseDiscussion) -> float:
            direct = assets.get(d.id, set()) & set(tracked)
            held = any(tracked.get(s) == "holding" for s in direct)
            base = (3.0 if held else 2.0) if direct else (1.0 if set(tmap.get(d.id, [])) & topics else 0.0)
            base += 1.5 if d.id in followed else 0.0
            age_days = (now - d.last_activity_at).total_seconds() / 86400
            return base / (1 + age_days / 3)

        cands.sort(key=rel, reverse=True)
        page = cands[off : off + limit]
        return {"items": serialize_cards(db, page, viewer), "next": str(off + limit) if len(cands) > off + limit else None,
                "tracked": sorted(tracked)[:20], **meta}  # fmt: skip
    if sort == "discussed":
        stmt = stmt.where(PulseDiscussion.created_at >= utcnow() - timedelta(days=30), PulseDiscussion.comment_count > 0).order_by(
            PulseDiscussion.participant_count.desc(), PulseDiscussion.comment_count.desc(), PulseDiscussion.last_activity_at.desc())
    elif sort in ("activity", "following", "saved"):
        stmt = stmt.order_by(PulseDiscussion.last_activity_at.desc(), PulseDiscussion.id.desc())
    else:
        stmt = stmt.order_by(PulseDiscussion.created_at.desc(), PulseDiscussion.id.desc())
    rows = list(db.scalars(stmt.offset(off).limit(limit + 1)))
    return {"items": serialize_cards(db, rows[:limit], viewer), "next": str(off + limit) if len(rows) > limit else None, **meta}


def for_symbol(db: Session, viewer: User | None, symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    """Active discussions about one asset: most recent activity first."""
    return feed(db, viewer, "activity", symbol=symbol, limit=limit)["items"]


def related(db: Session, public_id: str, viewer: User | None, limit: int = 5) -> list[dict[str, Any]]:
    d = get_discussion(db, public_id, viewer)
    syms = list(db.scalars(select(PulseDiscussionAsset.symbol).where(PulseDiscussionAsset.discussion_id == d.id)))
    topics = list(db.scalars(select(PulseDiscussionTopic.topic).where(PulseDiscussionTopic.discussion_id == d.id)))
    conds = []
    if syms:
        conds.append(PulseDiscussion.id.in_(select(PulseDiscussionAsset.discussion_id).where(PulseDiscussionAsset.symbol.in_(syms))))
    if topics:
        conds.append(PulseDiscussion.id.in_(select(PulseDiscussionTopic.discussion_id).where(PulseDiscussionTopic.topic.in_(topics))))
    if not conds:
        return []
    rows = list(db.scalars(select(PulseDiscussion).where(PulseDiscussion.status == "visible", PulseDiscussion.id != d.id, or_(*conds))
                           .order_by(PulseDiscussion.last_activity_at.desc()).limit(40)))  # fmt: skip
    same = [r for r in rows if r.primary_symbol and r.primary_symbol in syms]
    rest = [r for r in rows if r not in same]
    return serialize_cards(db, (same + rest)[:limit], viewer)


def detail(db: Session, public_id: str, viewer: User | None) -> dict[str, Any]:
    return serialize_full(db, get_discussion(db, public_id, viewer), viewer)


def legacy(db: Session, post_id: int) -> dict[str, Any]:
    d = db.scalars(select(PulseDiscussion).where(PulseDiscussion.legacy_post_id == post_id, PulseDiscussion.status == "visible")).first()
    if d is None:
        raise NotFoundError("discussion not found")
    return {"id": d.public_id, "url": url_for(d)}


def topics_overview(db: Session, days: int = 30) -> list[dict[str, Any]]:
    since = utcnow() - timedelta(days=days)
    counts = dict(db.execute(
        select(PulseDiscussionTopic.topic, func.count(PulseDiscussionTopic.discussion_id))
        .join(PulseDiscussion, PulseDiscussion.id == PulseDiscussionTopic.discussion_id)
        .where(PulseDiscussion.status == "visible", PulseDiscussion.last_activity_at >= since)
        .group_by(PulseDiscussionTopic.topic)
    ).all())  # fmt: skip
    return [{"key": k, "label": TOPICS[k], "discussions": counts.get(k, 0), "primary": k in PRIMARY_TOPICS}
            for k in sorted(TOPICS, key=lambda k: (k not in PRIMARY_TOPICS, -counts.get(k, 0), list(TOPICS).index(k)))]  # fmt: skip


def overview(db: Session, viewer: User | None) -> dict[str, Any]:
    """The Pulse landing: what is being debated now, how the debates changed, and honest activity numbers."""
    now = utcnow()
    vis = PulseDiscussion.status == "visible"
    week = now - timedelta(days=7)
    stats = {
        "community_discussions": db.scalar(select(func.count(PulseDiscussion.id)).where(vis, PulseDiscussion.kind == "community")) or 0,
        "editorial_discussions": db.scalar(select(func.count(PulseDiscussion.id)).where(vis, PulseDiscussion.kind == "editorial")) or 0,
        "comments_7d": db.scalar(select(func.count(PulseComment.id)).join(PulseDiscussion, PulseDiscussion.id == PulseComment.discussion_id)
                                 .where(vis, PulseComment.status == "visible", PulseComment.created_at >= week)) or 0,
        "participants_7d": db.scalar(select(func.count(func.distinct(PulseComment.author_id))).where(
            PulseComment.status == "visible", PulseComment.created_at >= week)) or 0,
    }
    upd = db.execute(
        select(PulseDiscussionUpdate, PulseDiscussion).join(PulseDiscussion, PulseDiscussion.id == PulseDiscussionUpdate.discussion_id)
        .where(vis, PulseDiscussionUpdate.kind != "opened").order_by(PulseDiscussionUpdate.created_at.desc()).limit(10)
    ).all()  # fmt: skip
    debates = list(db.scalars(select(PulseDiscussion).where(vis, PulseDiscussion.kind == "editorial")
                              .order_by(func.coalesce(PulseDiscussion.content_updated_at, PulseDiscussion.created_at).desc()).limit(8)))  # fmt: skip
    return {
        "stats": stats,
        "debates": serialize_cards(db, debates, viewer),
        "timeline": [{"discussion": {"id": d.public_id, "url": url_for(d), "title": d.title, "symbol": d.primary_symbol},
                      "kind": u.kind, "headline": u.headline, "created_at": _iso(u.created_at)} for u, d in upd],
        "topics": [t for t in topics_overview(db) if t["primary"]],
        "disclaimer": DISCLAIMER,
    }


def asset_page(db: Session, symbol: str, viewer: User | None) -> dict[str, Any]:
    sym = markets.clean_symbol(symbol)
    try:
        q = (markets.quotes(db, [sym]) or [None])[0]
    except NexisError:
        q = None
    ed = db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key == f"asset:{sym}", PulseDiscussion.status == "visible")).first()
    stored_name = db.scalar(select(PulseDiscussion.asset_name).where(PulseDiscussion.primary_symbol == sym, PulseDiscussion.asset_name.is_not(None)).limit(1))
    if q is None and ed is None and stored_name is None:
        raise NotFoundError(f"no market data or Pulse discussions found for {sym}")
    linked = select(PulseDiscussionAsset.discussion_id).where(PulseDiscussionAsset.symbol == sym)
    total = db.scalar(select(func.count(PulseDiscussion.id)).where(PulseDiscussion.status == "visible", PulseDiscussion.id.in_(linked))) or 0
    return {
        "symbol": sym, "name": (q or {}).get("name") or (ed.asset_name if ed else None) or stored_name or sym,
        "quote": {k: q.get(k) for k in ("price", "change", "change_pct", "currency", "exchange", "market_time")} if q else None,
        "editorial": serialize_full(db, ed, viewer) if ed else None,
        "discussions": total,
        "topics": [{"key": t, "label": TOPICS[t]} for t in symbol_topics(sym)],
    }


def search(db: Session, viewer: User | None, q: str) -> dict[str, Any]:
    term = (q or "").strip()
    if len(term) < 2:
        return {"assets": [], "discussions": [], "topics": []}
    try:
        assets = markets.search(db, term)[:6]
    except NexisError:
        assets = []
    low = term.lower()
    return {
        "assets": [{"symbol": a["symbol"], "name": a.get("name"), "type": a.get("type"), "exchange": a.get("exchange")} for a in assets],
        "discussions": feed(db, viewer, "activity", q=term, limit=20)["items"],
        "topics": [{"key": k, "label": v} for k, v in TOPICS.items() if low in v.lower() or low == k],
    }


# ------------------------------------------------------------------ writing (members)


def _set_assets(db: Session, d: PulseDiscussion, symbols: list[str]) -> None:
    db.execute(delete(PulseDiscussionAsset).where(PulseDiscussionAsset.discussion_id == d.id))
    db.add_all(PulseDiscussionAsset(discussion_id=d.id, symbol=s[:32]) for s in dict.fromkeys(symbols) if s)


def _set_topics(db: Session, d: PulseDiscussion, topics: list[str]) -> None:
    db.execute(delete(PulseDiscussionTopic).where(PulseDiscussionTopic.discussion_id == d.id))
    db.add_all(PulseDiscussionTopic(discussion_id=d.id, topic=t) for t in dict.fromkeys(topics))


def _log(db: Session, target_type: str, target_id: int, action: str, reason: str | None = None, actor: User | None = None, **details: Any) -> None:
    db.add(ModerationAction(target_type=target_type, target_id=target_id, action=action, reason=(reason or "")[:300] or None,
                            actor_id=actor.id if actor else None, details=details, created_at=utcnow()))  # fmt: skip


def _participants(db: Session, d: PulseDiscussion) -> int:
    ids = set(db.scalars(select(func.distinct(PulseComment.author_id)).where(
        PulseComment.discussion_id == d.id, PulseComment.status == "visible", PulseComment.author_id.is_not(None))))  # fmt: skip
    if d.author_id is not None:
        ids.add(d.author_id)
    return len(ids)


def create(db: Session, user: User, title: str, body: str | None, symbol: str | None, stance: str | None, topics: list[str] | None) -> dict[str, Any]:
    require_participant(db, user)
    ratelimit.hit(db, f"pulse-discussion:{user.id}", get_settings().pulse_discussions_per_day, timedelta(hours=24))
    title = _text(title, 10, 160, "the title")
    body = _text(body, 0, 6000, "the text")
    if stance is not None and stance not in STANCES:
        raise ValidationFailed("stance must be bullish, bearish, neutral or question")
    keys = _topics_in(topics)
    sym, name = _resolve_asset(db, symbol) if symbol else (None, None)
    from app.services.social import _tags

    cashtags, _ = _tags(f"{title}\n{body}")
    symbols = list(dict.fromkeys([s for s in [sym, *cashtags] if s]))[:8]
    if not keys:
        keys = detect_topics(f"{title}\n{body}", symbols)
    flags = pulse_safety.check(f"{title}\n{body}")
    now = utcnow()
    d = PulseDiscussion(
        public_id=new_public_id(db), kind="community", author_id=user.id, title=title, body=body, stance=stance,
        primary_symbol=sym or (symbols[0] if symbols else None), asset_name=name, symbols=symbols,
        status=pulse_safety.verdict(flags), flagged=bool(flags), flags=flags, last_activity_at=now, created_at=now,
        participant_count=1,
    )  # fmt: skip
    db.add(d)
    db.flush()
    _set_assets(db, d, symbols)
    _set_topics(db, d, keys)
    if flags:
        _log(db, "discussion", d.id, "auto_hold" if d.status == "held" else "auto_flag", ", ".join(f["label"] for f in flags), flags=flags)
    db.commit()
    if d.status == "visible":
        from app.services import alerts

        alerts.notify_new_discussion(db, d)
    return serialize_full(db, d, user)


def delete_discussion(db: Session, public_id: str, user: User) -> None:
    d = get_discussion(db, public_id, user)
    if d.kind != "community" or d.author_id != user.id:
        raise Forbidden("you can only delete your own discussions")
    d.status, d.title, d.body = "deleted", "[deleted]", ""
    _log(db, "discussion", d.id, "author_delete", actor=user)
    db.commit()


def comment(db: Session, public_id: str, user: User, body: str, parent_id: int | None, stance: str | None) -> dict[str, Any]:
    require_participant(db, user)
    d = get_discussion(db, public_id, user)
    if d.status != "visible":
        raise ValidationFailed("this discussion is waiting for review; replies open once it is published")
    if d.locked:
        raise Forbidden("this discussion is locked")
    ratelimit.hit(db, f"pulse-comment:{user.id}", get_settings().pulse_comments_per_hour)
    body = _text(body, 2, 4000, "a reply")
    if stance is not None and stance not in COMMENT_STANCES:
        raise ValidationFailed("stance must be agree, disagree or question")
    depth = 0
    parent = None
    if parent_id is not None:
        parent = db.get(PulseComment, parent_id)
        if parent is None or parent.discussion_id != d.id or parent.status != "visible":
            raise NotFoundError("the comment you're replying to isn't available")
        depth = min(parent.depth + 1, MAX_DEPTH)
    flags = pulse_safety.check(body)
    now = utcnow()
    c = PulseComment(discussion_id=d.id, parent_id=parent.id if parent else None, author_id=user.id, body=body, stance=stance, depth=depth,
                     status=pulse_safety.verdict(flags), flagged=bool(flags), flags=flags, created_at=now)  # fmt: skip
    db.add(c)
    db.flush()
    if c.status == "visible":
        d.comment_count += 1
        d.last_activity_at = now
        d.participant_count = _participants(db, d)
    if flags:
        _log(db, "comment", c.id, "auto_hold" if c.status == "held" else "auto_flag", ", ".join(f["label"] for f in flags), flags=flags)
    db.commit()
    if c.status == "visible":
        from app.services import alerts

        alerts.notify_comment(db, d, c, parent)
    return _comment_out(c, d.author_id if d.kind == "community" else None, user, {})


def delete_comment(db: Session, cid: int, user: User) -> None:
    c = db.get(PulseComment, cid)
    if c is None or c.author_id != user.id or c.status in ("deleted", "removed"):
        raise NotFoundError("comment not found")  # someone else's comment looks exactly like a missing one
    was_visible = c.status == "visible"
    c.status, c.body, c.stance = "deleted", "", None
    db.flush()  # sessions don't autoflush; the recount below must see this
    d = db.get(PulseDiscussion, c.discussion_id)
    if d is not None and was_visible:
        d.comment_count = max(0, d.comment_count - 1)
        d.participant_count = _participants(db, d)
    _log(db, "comment", c.id, "author_delete", actor=user)
    db.commit()


def _target(db: Session, target_type: str, target_id: str | int, viewer: User) -> tuple[PulseDiscussion | PulseComment, int, int | None]:
    """The object, its internal id, and its author's id. Only content the viewer can see is a valid target."""
    if target_type == "discussion":
        d = get_discussion(db, str(target_id), viewer)
        return d, d.id, d.author_id
    if target_type == "comment":
        try:
            cid = int(target_id)
        except (TypeError, ValueError) as exc:
            raise NotFoundError("comment not found") from exc
        c = db.get(PulseComment, cid)
        if c is None or c.status != "visible":
            raise NotFoundError("comment not found")
        get_discussion(db, db.scalar(select(PulseDiscussion.public_id).where(PulseDiscussion.id == c.discussion_id)) or "", viewer)
        return c, c.id, c.author_id
    raise ValidationFailed("target must be a discussion or a comment")


def react(db: Session, user: User, target_type: str, target_id: str | int, kind: str) -> dict[str, Any]:
    """Toggle a reaction. Agree and disagree exclude each other; nobody reacts to their own post."""
    if kind not in REACTIONS:
        raise ValidationFailed("reaction must be agree, disagree or interesting")
    require_participant(db, user)
    obj, tid, author = _target(db, target_type, target_id, user)
    if author is not None and author == user.id:
        raise ValidationFailed("you can't react to your own post")
    ratelimit.hit(db, f"pulse-react:{user.id}", get_settings().pulse_reactions_per_hour)
    existing = {r.kind: r for r in db.scalars(select(PulseReaction).where(
        PulseReaction.user_id == user.id, PulseReaction.target_type == target_type, PulseReaction.target_id == tid))}  # fmt: skip
    deltas: dict[str, int] = {}
    if kind in existing:
        db.delete(existing[kind])
        deltas[kind] = -1
    else:
        opposite = {"agree": "disagree", "disagree": "agree"}.get(kind)
        if opposite and opposite in existing:
            db.delete(existing[opposite])
            deltas[opposite] = -1
        db.add(PulseReaction(user_id=user.id, target_type=target_type, target_id=tid, kind=kind))
        deltas[kind] = 1
    for k, v in deltas.items():
        setattr(obj, f"{k}_count", max(0, getattr(obj, f"{k}_count") + v))
    try:
        db.commit()
    except IntegrityError:  # a double click raced itself
        db.rollback()
    db.refresh(obj)
    mine = list(db.scalars(select(PulseReaction.kind).where(PulseReaction.user_id == user.id, PulseReaction.target_type == target_type,
                                                            PulseReaction.target_id == tid)))  # fmt: skip
    return {"counts": {"agree": obj.agree_count, "disagree": obj.disagree_count, "interesting": obj.interesting_count}, "reactions": mine}


def _toggle(db: Session, model: Any, counter: str, user: User, public_id: str, on: bool | None) -> bool:
    d = get_discussion(db, public_id, user)
    row = db.scalars(select(model).where(model.user_id == user.id, model.discussion_id == d.id)).first()
    want = (row is None) if on is None else on
    if want and row is None:
        db.add(model(user_id=user.id, discussion_id=d.id))
        setattr(d, counter, getattr(d, counter) + 1)
    elif not want and row is not None:
        db.delete(row)
        setattr(d, counter, max(0, getattr(d, counter) - 1))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return want


def follow(db: Session, user: User, public_id: str, on: bool | None = None) -> dict[str, Any]:
    return {"following": _toggle(db, PulseFollow, "follower_count", user, public_id, on)}


def save(db: Session, user: User, public_id: str, on: bool | None = None) -> dict[str, Any]:
    return {"saved": _toggle(db, PulseSave, "save_count", user, public_id, on)}


def report(db: Session, user: User, target_type: str, target_id: str | int, reason: str, detail: str | None) -> dict[str, Any]:
    if reason not in REPORT_REASONS:
        raise ValidationFailed("pick a reason for the report")
    ratelimit.hit(db, f"pulse-report:{user.id}", get_settings().pulse_reports_per_day, timedelta(hours=24))
    obj, tid, author = _target(db, target_type, target_id, user)
    if author is not None and author == user.id:
        raise ValidationFailed("you can't report your own post — delete it instead")
    note = _text(detail, 0, 500, "the details") or None
    try:
        with db.begin_nested():
            db.add(ContentReport(reporter_id=user.id, target_type=target_type, target_id=tid, reason=reason, detail=note))
    except IntegrityError:
        return {"reported": True, "already": True}
    open_reports = db.scalar(select(func.count(ContentReport.id)).where(
        ContentReport.target_type == target_type, ContentReport.target_id == tid, ContentReport.status == "open")) or 0  # fmt: skip
    if open_reports >= 3 and not obj.flagged:  # several people reported it: put it at the front of the review queue
        obj.flagged = True
        _log(db, target_type, tid, "auto_flag", f"{open_reports} reports")
    db.commit()
    return {"reported": True, "already": False}


# ------------------------------------------------------------------ a member's own activity (private)


def my_activity(db: Session, user: User) -> dict[str, Any]:
    mine = list(db.scalars(select(PulseDiscussion).where(PulseDiscussion.author_id == user.id, PulseDiscussion.status.in_(["visible", "held"]))
                           .order_by(PulseDiscussion.created_at.desc()).limit(50)))  # fmt: skip
    rows = db.execute(
        select(PulseComment, PulseDiscussion).join(PulseDiscussion, PulseDiscussion.id == PulseComment.discussion_id)
        .where(PulseComment.author_id == user.id, PulseComment.status.in_(["visible", "held"]), PulseDiscussion.status == "visible")
        .order_by(PulseComment.created_at.desc()).limit(50)
    ).all()  # fmt: skip
    return {
        "discussions": serialize_cards(db, mine, user),
        "comments": [{"id": c.id, "body": _excerpt(c.body, 220), "created_at": _iso(c.created_at), "pending_review": c.status == "held",
                      "discussion": {"id": d.public_id, "url": url_for(d), "title": d.title}} for c, d in rows],
        "note": "Only you can see this list. Your posts appear publicly as Anonymous.",
    }


def erase_member(db: Session, user: User) -> None:
    """Account deletion: erase the member's Pulse text and detach it from the account (threads keep their shape)."""
    db.execute(update(PulseComment).where(PulseComment.author_id == user.id).values(status="deleted", body="", stance=None, author_id=None))
    for d in db.scalars(select(PulseDiscussion).where(PulseDiscussion.author_id == user.id)):
        d.status, d.title, d.body, d.author_id = "deleted", "[deleted]", "", None
    db.flush()
    for d in db.scalars(select(PulseDiscussion).where(PulseDiscussion.status == "visible", PulseDiscussion.id.in_(
            select(PulseComment.discussion_id).where(PulseComment.status == "deleted")))):  # fmt: skip
        d.comment_count = db.scalar(select(func.count(PulseComment.id)).where(PulseComment.discussion_id == d.id, PulseComment.status == "visible")) or 0
        d.participant_count = _participants(db, d)
    for r in db.scalars(select(PulseReaction).where(PulseReaction.user_id == user.id)):
        obj = db.get(PulseDiscussion if r.target_type == "discussion" else PulseComment, r.target_id)
        if obj is not None:
            setattr(obj, f"{r.kind}_count", max(0, getattr(obj, f"{r.kind}_count") - 1))
        db.delete(r)
