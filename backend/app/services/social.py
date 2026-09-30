"""Finstagram: a finance-focused social feed — posts with photos, $CASHTAGS, likes, comments, follows.

Images are decoded and re-encoded server-side (which also strips EXIF metadata such as GPS
location) and capped in size. Posts reported by three different people are hidden automatically.
"""

from __future__ import annotations

import io
import math
import re
from datetime import timedelta
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, Forbidden, NexisError, NotFoundError, ValidationFailed
from app.db.base import utcnow
from app.models import Comment, FeedPreference, Follow, Like, Media, Post, PostReport, Save, TopicFollow, User
from app.services import markets, newsfeed, ratelimit
from app.services.auth import serialize_user

CASHTAG = re.compile(r"(?<![\w$])\$([A-Za-z][A-Za-z0-9]{0,11}(?:[.\-=][A-Za-z0-9]{1,4})?)")
HASHTAG = re.compile(r"(?<![\w#])#([A-Za-z][\w]{1,39})")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_SIDE = 1440
HIDE_AFTER_REPORTS = 3


def store_image(db: Session, user: User, raw: bytes) -> Media:
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValidationFailed("images must be smaller than 8 MB")
    try:
        im = Image.open(io.BytesIO(raw))
        im.verify()
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationFailed("that file is not a supported image (JPEG, PNG, WebP or GIF)") from exc
    if im.width * im.height > 60_000_000:
        raise ValidationFailed("image dimensions are too large")
    im = im.convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85, optimize=True, progressive=True)  # re-encoding drops EXIF/GPS metadata
    m = Media(user_id=user.id, content_type="image/jpeg", width=im.width, height=im.height, data=out.getvalue())
    db.add(m)
    db.flush()
    return m


def _tags(body: str) -> tuple[list[str], list[str]]:
    syms = list(dict.fromkeys(s.upper() for s in CASHTAG.findall(body)))[:10]
    tags = list(dict.fromkeys(t.lower() for t in HASHTAG.findall(body)))[:15]
    return syms, tags


def create_post(db: Session, request: Any, user: User, body: str, image: bytes | None) -> Post:
    ratelimit.hit(db, f"post:{user.id}", get_settings().posts_per_hour)
    body = (body or "").strip()
    if not body and not image:
        raise ValidationFailed("write something or add a photo")
    if len(body) > 2200:
        raise ValidationFailed("posts are limited to 2,200 characters")
    media = store_image(db, user, image) if image else None
    syms, tags = _tags(body)
    p = Post(user_id=user.id, body=body, media_id=media.id if media else None, symbols=syms, tags=tags)
    db.add(p)
    db.commit()
    return p


def _get_post(db: Session, pid: int) -> Post:
    p = db.get(Post, pid)
    if p is None or p.hidden:
        raise NotFoundError("post not found")
    return p


def serialize_posts(db: Session, posts: list[Post], viewer: User | None) -> list[dict[str, Any]]:
    if not posts:
        return []
    ids = [p.id for p in posts]
    authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_({p.user_id for p in posts})))}
    liked = set(db.scalars(select(Like.post_id).where(Like.post_id.in_(ids), Like.user_id == viewer.id))) if viewer else set()
    saved = set(db.scalars(select(Save.post_id).where(Save.post_id.in_(ids), Save.user_id == viewer.id))) if viewer else set()
    following = set(db.scalars(select(Follow.followee_id).where(Follow.follower_id == viewer.id))) if viewer else set()
    syms = list(dict.fromkeys(s for p in posts for s in p.symbols))[:40]
    quotes: dict[str, dict[str, Any]] = {}
    if syms:
        try:
            quotes = {q["symbol"]: q for q in markets.quotes(db, syms)}
        except NexisError:
            quotes = {}
    out = []
    for p in posts:
        a = authors[p.user_id]
        out.append(
            {
                "id": p.id,
                "author": {**serialize_user(db, a), "kind": a.kind, "followed_by_me": a.id in following},
                "body": p.body,
                "image_url": f"/api/social/media/{p.media_id}" if p.media_id else None,
                "link": {"url": p.link_url, "title": p.link_title, "source": p.link_source, "image": p.link_image}
                if p.link_url
                else None,
                "symbols": [
                    {
                        "symbol": s,
                        **({k: quotes[s].get(k) for k in ("name", "price", "change_pct", "currency")} if s in quotes else {}),
                    }
                    for s in p.symbols
                ],
                "tags": p.tags,
                "like_count": p.like_count,
                "comment_count": p.comment_count,
                "liked_by_me": p.id in liked,
                "saved_by_me": p.id in saved,
                "is_mine": viewer is not None and viewer.id == p.user_id,
                "created_at": p.created_at.isoformat() + "Z",
            }
        )
    return out


def _post_keys(p: Post) -> list[str]:
    return [f"author:{p.user_id}"] + [f"sym:{s}" for s in p.symbols] + [f"tag:{t}" for t in p.tags]


def _prefs(db: Session, viewer: User | None) -> dict[str, float]:
    if viewer is None:
        return {}
    rows = db.execute(select(FeedPreference.key, FeedPreference.weight).where(FeedPreference.user_id == viewer.id)).all()
    return {k: w for k, w in rows}


def _followed(db: Session, viewer: User | None) -> tuple[set[int], set[str], set[str]]:
    if viewer is None:
        return set(), set(), set()
    users = set(db.scalars(select(Follow.followee_id).where(Follow.follower_id == viewer.id)))
    topics = db.execute(select(TopicFollow.kind, TopicFollow.value).where(TopicFollow.user_id == viewer.id)).all()
    return users, {v for k, v in topics if k == "symbol"}, {v for k, v in topics if k == "tag"}


def _no_runs(ranked: list[Post]) -> list[Post]:
    """Keep variety: no more than two posts in a row from the same account."""
    out: list[Post] = []
    rest = list(ranked)
    while rest:
        pick = next((p for p in rest if not (len(out) >= 2 and out[-1].user_id == out[-2].user_id == p.user_id)), rest[0])
        rest.remove(pick)
        out.append(pick)
    return out


def feed(
    db: Session,
    viewer: User | None,
    mode: str = "latest",
    symbol: str | None = None,
    tag: str | None = None,
    username: str | None = None,
    before: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if mode in ("latest", "trending", "following") and not (symbol or tag or username) and not before:
        try:
            newsfeed.refresh(db)
        except Exception:  # importing news must never take the feed down
            db.rollback()
    q = select(Post).where(Post.hidden.is_(False))
    prefs = _prefs(db, viewer)
    hidden_for_me = {int(k[5:]) for k, w in prefs.items() if k.startswith("post:") and w <= -5}
    if hidden_for_me:
        q = q.where(Post.id.not_in(hidden_for_me))
    if mode == "saved":
        if viewer is None:
            return {"items": [], "next": None, "note": "sign in to see your saved posts"}
        rows = list(
            db.scalars(
                select(Post)
                .join(Save, Save.post_id == Post.id)
                .where(Save.user_id == viewer.id, Post.hidden.is_(False))
                .order_by(Save.id.desc())
                .limit(200)
            )
        )
        return {"items": serialize_posts(db, rows, viewer), "next": None}
    if symbol or tag:
        key = markets.clean_symbol(symbol) if symbol else (tag or "").lower().lstrip("#")
        cands = db.scalars(q.order_by(Post.created_at.desc()).limit(800))
        posts = [p for p in cands if (key in p.symbols if symbol else key in p.tags)][: limit * 2]
        return {"items": serialize_posts(db, posts, viewer), "next": None}
    if username:
        u = db.scalars(select(User).where(User.username == username.lower())).first()
        if u is None:
            raise NotFoundError("user not found")
        q = q.where(Post.user_id == u.id)
        if before:
            ref = db.get(Post, before)
            if ref is not None:
                q = q.where(Post.created_at < ref.created_at)
        rows = list(db.scalars(q.order_by(Post.created_at.desc()).limit(limit + 1)))
        return {"items": serialize_posts(db, rows[:limit], viewer), "next": rows[limit - 1].id if len(rows) > limit else None}

    users, syms, tags = _followed(db, viewer)
    now = utcnow()
    cands = list(db.scalars(q.where(Post.created_at >= now - timedelta(days=14)).order_by(Post.created_at.desc()).limit(600)))
    if mode == "following":
        if viewer is None:
            return {
                "items": [],
                "next": None,
                "note": "sign in and follow news pages, people, stocks or hashtags to build this feed",
            }
        cands = [p for p in cands if p.user_id in users or p.user_id == viewer.id or set(p.symbols) & syms or set(p.tags) & tags]
        if not cands:
            return {
                "items": [],
                "next": None,
                "note": "you are not following anything yet — follow news pages, stocks ($EMAAR.AE) or hashtags to fill this feed",
            }

    def score(p: Post) -> float:
        age_h = max((now - p.created_at).total_seconds() / 3600, 0.25)
        engagement = p.like_count + 2 * p.comment_count
        if mode == "trending":
            return (engagement + 1) / (age_h + 2) ** 1.3
        base = 0.5 ** (age_h / 18)  # half-life of 18 hours
        base *= 1 + 0.35 * math.log1p(engagement)
        pref = sum(prefs.get(k, 0.0) for k in _post_keys(p))
        base *= math.exp(max(-3.0, min(3.0, 0.6 * pref)))
        if p.user_id in users or set(p.symbols) & syms or set(p.tags) & tags:
            base *= 1.8
        return base

    if mode == "following":
        ranked = sorted(cands, key=lambda p: p.created_at, reverse=True)
    else:
        ranked = sorted(cands, key=score, reverse=True)
        if mode == "latest":
            ranked = _no_runs(ranked)
    offset = int(before or 0)
    page = ranked[offset : offset + limit]
    nxt = offset + limit if len(ranked) > offset + limit else None
    return {"items": serialize_posts(db, page, viewer), "next": nxt}


def get_post(db: Session, pid: int, viewer: User | None) -> dict[str, Any]:
    return serialize_posts(db, [_get_post(db, pid)], viewer)[0]


def delete_post(db: Session, pid: int, user: User) -> None:
    p = db.get(Post, pid)
    if p is None:
        raise NotFoundError("post not found")
    if p.user_id != user.id:
        raise Forbidden("you can only delete your own posts")
    media_id = p.media_id
    db.delete(p)
    if media_id:
        db.execute(delete(Media).where(Media.id == media_id))
    db.commit()


def toggle_like(db: Session, pid: int, user: User) -> dict[str, Any]:
    p = _get_post(db, pid)
    existing = db.scalars(select(Like).where(Like.post_id == pid, Like.user_id == user.id)).first()
    if existing:
        db.delete(existing)
        liked = False
    else:
        db.add(Like(post_id=pid, user_id=user.id))
        liked = True
    db.flush()
    p.like_count = db.scalar(select(func.count(Like.id)).where(Like.post_id == pid)) or 0
    db.commit()
    return {"liked": liked, "like_count": p.like_count}


def comments(db: Session, pid: int, viewer: User | None) -> list[dict[str, Any]]:
    _get_post(db, pid)
    rows = list(db.scalars(select(Comment).where(Comment.post_id == pid).order_by(Comment.id).limit(300)))
    authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_({c.user_id for c in rows})))} if rows else {}
    return [
        {"id": c.id, "author": serialize_user(db, authors[c.user_id]), "body": c.body, "created_at": c.created_at.isoformat() + "Z",
         "is_mine": viewer is not None and viewer.id == c.user_id}
        for c in rows
    ]  # fmt: skip


def add_comment(db: Session, pid: int, user: User, body: str) -> dict[str, Any]:
    ratelimit.hit(db, f"comment:{user.id}", 60)
    p = _get_post(db, pid)
    body = (body or "").strip()
    if not body or len(body) > 1000:
        raise ValidationFailed("comments must be 1–1,000 characters")
    c = Comment(post_id=pid, user_id=user.id, body=body)
    db.add(c)
    db.flush()
    p.comment_count = db.scalar(select(func.count(Comment.id)).where(Comment.post_id == pid)) or 0
    db.commit()
    return {
        "id": c.id,
        "author": serialize_user(db, user),
        "body": c.body,
        "created_at": c.created_at.isoformat() + "Z",
        "is_mine": True,
    }


def delete_comment(db: Session, cid: int, user: User) -> None:
    c = db.get(Comment, cid)
    if c is None:
        raise NotFoundError("comment not found")
    p = db.get(Post, c.post_id)
    if c.user_id != user.id and (p is None or p.user_id != user.id):
        raise Forbidden("you can only delete your own comments or comments on your posts")
    db.delete(c)
    db.flush()
    if p is not None:
        p.comment_count = db.scalar(select(func.count(Comment.id)).where(Comment.post_id == p.id)) or 0
    db.commit()


def report(db: Session, pid: int, user: User, reason: str | None) -> dict[str, Any]:
    p = _get_post(db, pid)
    if p.user_id == user.id:
        raise ValidationFailed("you cannot report your own post")
    if db.scalar(select(PostReport.id).where(PostReport.post_id == pid, PostReport.user_id == user.id)) is not None:
        raise ConflictError("you already reported this post")
    db.add(PostReport(post_id=pid, user_id=user.id, reason=(reason or "")[:200] or None))
    db.flush()
    n = db.scalar(select(func.count(PostReport.id)).where(PostReport.post_id == pid)) or 0
    if n >= HIDE_AFTER_REPORTS:
        p.hidden = True
    db.commit()
    return {"reported": True, "hidden": p.hidden}


def toggle_follow(db: Session, username: str, user: User) -> dict[str, Any]:
    target = db.scalars(select(User).where(User.username == username.lower())).first()
    if target is None:
        raise NotFoundError("user not found")
    if target.id == user.id:
        raise ValidationFailed("you cannot follow yourself")
    f = db.scalars(select(Follow).where(Follow.follower_id == user.id, Follow.followee_id == target.id)).first()
    if f:
        db.delete(f)
    else:
        db.add(Follow(follower_id=user.id, followee_id=target.id))
    db.commit()
    return serialize_user(db, target, user, full=True)  # includes followed_by_me and follower counts


def profile(db: Session, username: str, viewer: User | None) -> dict[str, Any]:
    u = db.scalars(select(User).where(User.username == username.lower())).first()
    if u is None or u.is_disabled:
        raise NotFoundError("user not found")
    return serialize_user(db, u, viewer, full=True)


def trending_tags(db: Session, viewer: User | None = None) -> dict[str, Any]:
    since = utcnow() - timedelta(days=7)
    sym_count: dict[str, int] = {}
    tag_count: dict[str, int] = {}
    for p in db.scalars(select(Post).where(Post.hidden.is_(False), Post.created_at >= since).limit(1000)):
        for s in p.symbols:
            sym_count[s] = sym_count.get(s, 0) + 1
        for t in p.tags:
            tag_count[t] = tag_count.get(t, 0) + 1
    top = sorted(sym_count.items(), key=lambda x: -x[1])[:10]
    following = set(db.scalars(select(Follow.followee_id).where(Follow.follower_id == viewer.id))) if viewer else set()
    quotes: dict[str, Any] = {}
    if top:
        try:
            quotes = {q["symbol"]: q for q in markets.quotes(db, [s for s, _ in top])}
        except NexisError:
            quotes = {}
    return {
        "symbols": [{"symbol": s, "posts": n, **({k: quotes[s].get(k) for k in ("name", "price", "change_pct", "currency")} if s in quotes else {})} for s, n in top],
        "tags": [{"tag": t, "posts": n} for t, n in sorted(tag_count.items(), key=lambda x: -x[1])[:15]],
        "people": [
            serialize_user(db, u)
            for u in db.scalars(select(User).where(User.is_disabled.is_(False), User.kind == "person").order_by(User.id.desc()).limit(6))
        ],
        "pages": [
            {**serialize_user(db, u), "kind": "page", "bio": u.bio, "followed_by_me": u.id in following}
            for u in db.scalars(select(User).where(User.kind == "page").order_by(User.id))
        ],
    }  # fmt: skip


def media(db: Session, mid: int) -> Media:
    m = db.get(Media, mid)
    if m is None:
        raise NotFoundError("image not found")
    return m


def set_avatar(db: Session, user: User, raw: bytes) -> User:
    m = store_image(db, user, raw)
    old = user.avatar_media_id
    user.avatar_media_id = m.id
    if old:
        db.execute(delete(Media).where(Media.id == old))
    db.commit()
    return user


def toggle_save(db: Session, pid: int, user: User) -> dict[str, Any]:
    _get_post(db, pid)
    existing = db.scalars(select(Save).where(Save.post_id == pid, Save.user_id == user.id)).first()
    if existing:
        db.delete(existing)
    else:
        db.add(Save(post_id=pid, user_id=user.id))
    db.commit()
    return {"saved": existing is None}


def feedback(db: Session, pid: int, user: User, signal: str) -> dict[str, Any]:
    """Suggest more / less like this: nudges the author, tickers and hashtags; "less" also hides the post."""
    if signal not in ("more", "less"):
        raise ValidationFailed("signal must be 'more' or 'less'")
    p = _get_post(db, pid)
    delta = 1.0 if signal == "more" else -1.0
    keys = _post_keys(p) + ([f"post:{p.id}"] if signal == "less" else [])
    existing = {
        f.key: f
        for f in db.scalars(select(FeedPreference).where(FeedPreference.user_id == user.id, FeedPreference.key.in_(keys)))
    }
    for k in keys:
        d = -10.0 if k.startswith("post:") else delta
        if k in existing:
            existing[k].weight = max(-10.0, min(10.0, existing[k].weight + d))
        else:
            db.add(FeedPreference(user_id=user.id, key=k, weight=d))
    db.commit()
    return {"signal": signal, "hidden": signal == "less"}


def toggle_topic(db: Session, user: User, kind: str, value: str) -> dict[str, Any]:
    if kind not in ("symbol", "tag"):
        raise ValidationFailed("kind must be 'symbol' or 'tag'")
    v = markets.clean_symbol(value) if kind == "symbol" else value.lower().lstrip("#")[:40]
    if kind == "tag" and not re.match(r"^[a-z][\w]{1,39}$", v):
        raise ValidationFailed("invalid hashtag")
    f = db.scalars(
        select(TopicFollow).where(TopicFollow.user_id == user.id, TopicFollow.kind == kind, TopicFollow.value == v)
    ).first()
    if f:
        db.delete(f)
    else:
        db.add(TopicFollow(user_id=user.id, kind=kind, value=v))
    db.commit()
    return {"kind": kind, "value": v, "following": f is None}


def my_following(db: Session, user: User) -> dict[str, Any]:
    users = list(db.scalars(select(User).join(Follow, Follow.followee_id == User.id).where(Follow.follower_id == user.id)))
    topics = db.execute(
        select(TopicFollow.kind, TopicFollow.value).where(TopicFollow.user_id == user.id).order_by(TopicFollow.id)
    ).all()
    return {
        "accounts": [{**serialize_user(db, u), "kind": u.kind} for u in users],
        "symbols": [v for k, v in topics if k == "symbol"],
        "tags": [v for k, v in topics if k == "tag"],
    }


def search(db: Session, q: str, viewer: User | None) -> dict[str, Any]:
    q = q.strip()
    if not q:
        return {"instruments": [], "accounts": [], "tags": []}
    term = q.lstrip("@#$").lower()
    accounts = list(
        db.scalars(
            select(User)
            .where(
                User.is_disabled.is_(False),
                or_(func.lower(User.username).contains(term), func.lower(User.display_name).contains(term)),
            )
            .order_by(User.kind.desc(), User.id)
            .limit(8)
        )
    )
    tag_count: dict[str, int] = {}
    for tags in db.scalars(select(Post.tags).where(Post.hidden.is_(False)).order_by(Post.id.desc()).limit(1500)):
        for t in tags or []:
            if term in t:
                tag_count[t] = tag_count.get(t, 0) + 1
    try:
        instruments = markets.search(db, q.lstrip("$#@"))[:8] if not q.startswith(("@", "#")) else []
    except NexisError:
        instruments = []
    following = set(db.scalars(select(Follow.followee_id).where(Follow.follower_id == viewer.id))) if viewer else set()
    return {
        "instruments": instruments,
        "accounts": [
            {**serialize_user(db, u), "kind": u.kind, "bio": u.bio, "followed_by_me": u.id in following} for u in accounts
        ],
        "tags": [{"tag": t, "posts": n} for t, n in sorted(tag_count.items(), key=lambda x: -x[1])[:8]],
    }


def topic_page(db: Session, kind: str, value: str, viewer: User | None) -> dict[str, Any]:
    if kind not in ("symbol", "tag"):
        raise ValidationFailed("kind must be 'symbol' or 'tag'")
    v = markets.clean_symbol(value) if kind == "symbol" else value.lower().lstrip("#")
    following = (
        viewer is not None
        and db.scalar(
            select(TopicFollow.id).where(TopicFollow.user_id == viewer.id, TopicFollow.kind == kind, TopicFollow.value == v)
        )
        is not None
    )
    out: dict[str, Any] = {"kind": kind, "value": v, "following": following}
    if kind == "symbol":
        try:
            d = markets.details(db, v)
            out["instrument"] = {
                k: d[k] for k in ("symbol", "name", "type", "exchange", "currency", "quote", "analysts", "valuation", "dividends")
            }
            out["instrument"] |= {"summary": (d["profile"].get("summary") or "")[:400], "sector": d["profile"].get("sector")}
        except NexisError as exc:
            out["instrument"] = {"symbol": v, "error": exc.message}
    return out
