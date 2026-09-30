"""InstaFin: a finance-focused social feed — posts with photos, $CASHTAGS, likes, comments, follows.

Images are decoded and re-encoded server-side (which also strips EXIF metadata such as GPS
location) and capped in size. Posts reported by three different people are hidden automatically.
"""

from __future__ import annotations

import io
import re
from datetime import timedelta
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConflictError, Forbidden, NexisError, NotFoundError, ValidationFailed
from app.db.base import utcnow
from app.models import Comment, Follow, Like, Media, Post, PostReport, User
from app.services import markets, ratelimit
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
    syms = list(dict.fromkeys(s for p in posts for s in p.symbols))[:40]
    quotes: dict[str, dict[str, Any]] = {}
    if syms:
        try:
            quotes = {q["symbol"]: q for q in markets.quotes(db, syms)}
        except NexisError:
            quotes = {}
    out = []
    for p in posts:
        out.append({
            "id": p.id,
            "author": serialize_user(db, authors[p.user_id]),
            "body": p.body,
            "image_url": f"/api/social/media/{p.media_id}" if p.media_id else None,
            "symbols": [{"symbol": s, **({k: quotes[s].get(k) for k in ("name", "price", "change_pct", "currency")} if s in quotes else {})} for s in p.symbols],
            "tags": p.tags,
            "like_count": p.like_count,
            "comment_count": p.comment_count,
            "liked_by_me": p.id in liked,
            "is_mine": viewer is not None and viewer.id == p.user_id,
            "created_at": p.created_at.isoformat() + "Z",
        })  # fmt: skip
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
    q = select(Post).where(Post.hidden.is_(False))
    if mode == "following":
        if viewer is None:
            return {"items": [], "next": None, "note": "sign in to see posts from people you follow"}
        q = q.where(
            or_(Post.user_id.in_(select(Follow.followee_id).where(Follow.follower_id == viewer.id)), Post.user_id == viewer.id)
        )
    posts: list[Post]
    if symbol:
        sym = markets.clean_symbol(symbol)
        posts = [p for p in db.scalars(q.order_by(Post.id.desc()).limit(500)) if sym in p.symbols][:limit]
        return {"items": serialize_posts(db, posts, viewer), "next": None}
    if tag:
        t = tag.lower().lstrip("#")
        posts = [p for p in db.scalars(q.order_by(Post.id.desc()).limit(500)) if t in p.tags][:limit]
        return {"items": serialize_posts(db, posts, viewer), "next": None}
    if username:
        u = db.scalars(select(User).where(User.username == username.lower())).first()
        if u is None:
            raise NotFoundError("user not found")
        q = q.where(Post.user_id == u.id)
    if mode == "trending":
        recent = list(db.scalars(q.where(Post.created_at >= utcnow() - timedelta(days=7)).limit(400)))
        now = utcnow()

        def score(p: Post) -> float:
            age_h = max((now - p.created_at).total_seconds() / 3600, 0.5)
            return (p.like_count + 2 * p.comment_count + 1) / (age_h + 2) ** 1.3

        posts = sorted(recent, key=score, reverse=True)[:limit]
        return {"items": serialize_posts(db, posts, viewer), "next": None}
    if before:
        q = q.where(Post.id < before)
    posts = list(db.scalars(q.order_by(Post.id.desc()).limit(limit + 1)))
    nxt = posts[limit - 1].id if len(posts) > limit else None
    return {"items": serialize_posts(db, posts[:limit], viewer), "next": nxt}


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


def trending_tags(db: Session) -> dict[str, Any]:
    since = utcnow() - timedelta(days=7)
    sym_count: dict[str, int] = {}
    tag_count: dict[str, int] = {}
    for p in db.scalars(select(Post).where(Post.hidden.is_(False), Post.created_at >= since).limit(1000)):
        for s in p.symbols:
            sym_count[s] = sym_count.get(s, 0) + 1
        for t in p.tags:
            tag_count[t] = tag_count.get(t, 0) + 1
    top = sorted(sym_count.items(), key=lambda x: -x[1])[:10]
    quotes: dict[str, Any] = {}
    if top:
        try:
            quotes = {q["symbol"]: q for q in markets.quotes(db, [s for s, _ in top])}
        except NexisError:
            quotes = {}
    return {
        "symbols": [{"symbol": s, "posts": n, **({k: quotes[s].get(k) for k in ("name", "price", "change_pct", "currency")} if s in quotes else {})} for s, n in top],
        "tags": [{"tag": t, "posts": n} for t, n in sorted(tag_count.items(), key=lambda x: -x[1])[:15]],
        "people": [serialize_user(db, u) for u in db.scalars(select(User).where(User.is_disabled.is_(False)).order_by(User.id.desc()).limit(8))],
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
