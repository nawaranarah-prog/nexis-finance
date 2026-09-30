"""User accounts: scrypt password hashes, opaque session tokens in an HTTP-only cookie.

Only the SHA-256 of a session token is stored, so a database leak does not expose live sessions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Any

from fastapi import Depends, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AuthenticationRequired, ConflictError, ValidationFailed
from app.db.base import utcnow
from app.db.session import get_db
from app.models import Follow, Post, User, UserSession
from app.services import phone, ratelimit

COOKIE = "nexis_session"
SESSION_DAYS = 30
USERNAME = re.compile(r"^[a-z0-9_.]{3,30}$")
RESERVED = {"admin", "nexis", "support", "api", "root", "system", "moderator", "official", "help", "security"}
LINK_KEYS = ("instagram", "x", "linkedin", "tiktok", "youtube", "website")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(dk))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def serialize_user(db: Session, u: User, viewer: User | None = None, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "avatar_url": f"/api/social/media/{u.avatar_media_id}" if u.avatar_media_id else None,
    }
    if full:
        out |= {
            "bio": u.bio,
            "links": u.links or {},
            "joined": u.created_at.isoformat() + "Z",
            "posts": db.scalar(select(func.count(Post.id)).where(Post.user_id == u.id, Post.hidden.is_(False))) or 0,
            "following": db.scalar(select(func.count(Follow.id)).where(Follow.follower_id == u.id)) or 0,
            "is_me": viewer is not None and viewer.id == u.id,
            "kind": u.kind,
            "email": u.email if viewer is not None and viewer.id == u.id else None,
            "phone": u.phone if viewer is not None and viewer.id == u.id else None,
            "auth_provider": u.auth_provider if viewer is not None and viewer.id == u.id else None,
            "followed_by_me": viewer is not None
            and db.scalar(select(Follow.id).where(Follow.follower_id == viewer.id, Follow.followee_id == u.id)) is not None,
        }
    return out


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=get_settings().env != "development",
        samesite="lax",
        path="/",
    )


def _start_session(db: Session, user: User, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(user_id=user.id, token_hash=_token_hash(token), expires_at=utcnow() + timedelta(days=SESSION_DAYS)))
    db.commit()
    _set_cookie(response, token)


EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")


def _unique_username(db: Session, seed: str) -> str:
    base = re.sub(r"[^a-z0-9_.]", "", seed.lower().replace(" ", "."))[:24].strip(".") or "investor"
    if len(base) < 3:
        base = f"{base}inv"
    if base in RESERVED:
        base = f"{base}.1"
    cand, n = base, 1
    while db.scalar(select(User.id).where(func.lower(User.username) == cand)) is not None:
        n += 1
        cand = f"{base[:26]}{n}"
    return cand


def _check_password(password: str, *avoid: str) -> None:
    if len(password) < 8 or len(password) > 200:
        raise ValidationFailed("passwords must be at least 8 characters")
    if password.lower() in {"password", "12345678", "123456789", "qwertyui", "password1", *[a.lower() for a in avoid if a]}:
        raise ValidationFailed("choose a less guessable password")


def register(
    db: Session,
    request: Request,
    response: Response,
    username: str | None,
    password: str,
    display_name: str | None,
    email: str | None = None,
    phone_number: str | None = None,
) -> User:
    """Create an account with an email or a mobile number (and optionally a username), then sign in."""
    ratelimit.hit(db, f"register:{ratelimit.client_ip(request)}", 5)
    mail = (email or "").strip().lower() or None
    mobile = phone.normalize(phone_number) if phone_number else None
    if mobile is not None and db.scalar(select(User.id).where(User.phone == mobile)) is not None:
        raise ConflictError("an account with that mobile number already exists — sign in instead")
    if mail is not None:
        if not EMAIL.match(mail):
            raise ValidationFailed("enter a valid email address")
        if db.scalar(select(User.id).where(func.lower(User.email) == mail)) is not None:
            raise ConflictError("an account with that email already exists — sign in instead")
    if username:
        uname = username.strip().lower()
        if not USERNAME.match(uname):
            raise ValidationFailed("usernames are 3–30 characters: lowercase letters, digits, underscores and dots")
        if uname in RESERVED:
            raise ValidationFailed("that username is reserved")
        if db.scalar(select(User.id).where(func.lower(User.username) == uname)) is not None:
            raise ConflictError("that username is taken")
    elif mail is not None:
        uname = _unique_username(db, mail.split("@")[0])
    elif mobile is not None:
        uname = _unique_username(db, (display_name or "investor").strip() or "investor")
    else:
        raise ValidationFailed("enter an email address or a mobile number")
    _check_password(password, uname, mail or "", mobile or "")
    name = (display_name or "").strip()[:60] or uname
    u = User(
        username=uname,
        display_name=name,
        password_hash=hash_password(password),
        email=mail,
        phone=mobile,
        auth_provider="password",
    )
    db.add(u)
    db.commit()
    _start_session(db, u, response)
    return u


def login(db: Session, request: Request, response: Response, identifier: str, password: str) -> User:
    """Sign in with an email address, a mobile number or a username."""
    ratelimit.hit(db, f"login:{ratelimit.client_ip(request)}", 20)
    ident = identifier.strip().lower()
    if "@" not in ident and phone.looks_like_phone(ident):
        u = db.scalars(select(User).where(User.phone == phone.normalize(ident))).first()
    else:
        col = User.email if "@" in ident else User.username
        u = db.scalars(select(User).where(func.lower(col) == ident)).first()
    if u is not None and u.password_hash is None and u.auth_provider == "phone":
        raise AuthenticationRequired("this account signs in with an SMS code — use “Continue with phone”")
    if u is not None and u.password_hash is None and u.auth_provider == "google":
        raise AuthenticationRequired(
            f"this account uses {u.auth_provider.title()} sign-in — use the {u.auth_provider.title()} button"
        )
    if u is None or u.is_disabled or u.kind != "person" or not u.password_hash or not verify_password(password, u.password_hash):
        raise AuthenticationRequired("wrong email, phone or password")
    _start_session(db, u, response)
    return u


def sign_in_external(db: Session, response: Response, provider: str, sub: str, email: str | None, name: str | None) -> User:
    """Find or create the account for a verified Google/Apple identity and start a session."""
    key = f"{provider}:{sub}"
    u = db.scalars(select(User).where(User.provider_sub == key)).first()
    mail = (email or "").strip().lower() or None
    if u is None and mail:
        u = db.scalars(select(User).where(func.lower(User.email) == mail)).first()
        if u is not None:
            u.provider_sub = key  # link the existing email account to this provider
    if u is None:
        seed = (name or (mail.split("@")[0] if mail else provider)).strip()
        u = User(
            username=_unique_username(db, seed),
            display_name=(name or seed)[:60],
            password_hash=None,
            email=mail,
            auth_provider=provider,
            provider_sub=key,
        )
        db.add(u)
    if u.is_disabled:
        raise AuthenticationRequired("this account is disabled")
    db.commit()
    _start_session(db, u, response)
    return u


def logout(db: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        s = db.scalars(select(UserSession).where(UserSession.token_hash == _token_hash(token))).first()
        if s is not None:
            db.delete(s)
            db.commit()
    response.delete_cookie(COOKIE, path="/")


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    s = db.scalars(select(UserSession).where(UserSession.token_hash == _token_hash(token))).first()
    if s is None or s.expires_at < utcnow():
        return None
    u = db.get(User, s.user_id)
    return None if u is None or u.is_disabled else u


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise AuthenticationRequired("sign in to do that")
    return user


def update_profile(db: Session, u: User, display_name: str | None, bio: str | None, links: dict[str, str] | None) -> User:
    if display_name is not None:
        if not display_name.strip():
            raise ValidationFailed("display name cannot be empty")
        u.display_name = display_name.strip()[:60]
    if bio is not None:
        u.bio = bio.strip()[:300] or None
    if links is not None:
        clean = {}
        for k, v in links.items():
            if k not in LINK_KEYS:
                raise ValidationFailed(f"unknown profile link '{k}'")
            v = (v or "").strip().lstrip("@")[:120]
            if not v:
                continue
            if k == "website" and not re.match(r"^https://[\w.-]+\.[a-z]{2,}(/\S*)?$", v, re.I):
                raise ValidationFailed("website must be an https:// address")
            if k != "website" and not re.match(r"^[\w.\-]{1,60}$", v):
                raise ValidationFailed(f"{k}: enter just the handle")
            clean[k] = v
        u.links = clean
    db.commit()
    return u


def delete_account(db: Session, request: Request, response: Response, user: User, password: str) -> None:
    """Permanently delete the account and everything it owns (posts, photos, comments, likes, follows, sessions)."""
    ratelimit.hit(db, f"delete-account:{ratelimit.client_ip(request)}", 10)
    if user.password_hash is None:
        if password != "DELETE":
            raise AuthenticationRequired("type DELETE to confirm")
    elif not verify_password(password, user.password_hash):
        raise AuthenticationRequired("wrong password")
    from app.models import Comment, Like, Media, PostReport

    post_ids = list(db.scalars(select(Post.id).where(Post.user_id == user.id)))
    for model, col in ((Like, Like.user_id), (Comment, Comment.user_id), (PostReport, PostReport.user_id)):
        db.execute(delete(model).where(col == user.id))
    if post_ids:
        for model, col in ((Like, Like.post_id), (Comment, Comment.post_id), (PostReport, PostReport.post_id)):
            db.execute(delete(model).where(col.in_(post_ids)))
        db.execute(delete(Post).where(Post.id.in_(post_ids)))
    db.execute(delete(Follow).where((Follow.follower_id == user.id) | (Follow.followee_id == user.id)))
    db.execute(delete(Media).where(Media.user_id == user.id))
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.delete(user)
    db.commit()
    _recount(db)
    response.delete_cookie(COOKIE, path="/")


def _recount(db: Session) -> None:
    """Keep denormalised like/comment counters right after bulk deletes."""
    from app.models import Comment, Like

    likes = dict(db.execute(select(Like.post_id, func.count(Like.id)).group_by(Like.post_id)).all())
    comments = dict(db.execute(select(Comment.post_id, func.count(Comment.id)).group_by(Comment.post_id)).all())
    for post in db.scalars(select(Post).where((Post.like_count > 0) | (Post.comment_count > 0))):
        post.like_count, post.comment_count = likes.get(post.id, 0), comments.get(post.id, 0)
    db.commit()


def phone_start(db: Session, request: Request, number: str) -> dict[str, Any]:
    """Send an SMS code (requires Twilio Verify)."""
    if not phone.otp_enabled():
        raise ValidationFailed("SMS codes are not set up on this server — sign up with your mobile number and a password")
    mobile = phone.normalize(number)
    ratelimit.hit(db, f"sms:{ratelimit.client_ip(request)}", 5)
    ratelimit.hit(db, f"sms-number:{mobile}", 3)
    phone.send_code(mobile)
    return {"sent": True, "phone": mobile}


def phone_verify(db: Session, request: Request, response: Response, number: str, code: str, display_name: str | None) -> User:
    """Check the SMS code, then sign in (creating the account on first use)."""
    ratelimit.hit(db, f"sms-check:{ratelimit.client_ip(request)}", 15)
    mobile = phone.normalize(number)
    phone.check_code(mobile, code)
    u = db.scalars(select(User).where(User.phone == mobile)).first()
    if u is None:
        seed = (display_name or "investor").strip() or "investor"
        u = User(
            username=_unique_username(db, seed), display_name=seed[:60], password_hash=None, phone=mobile, auth_provider="phone"
        )
        db.add(u)
        db.commit()
    if u.is_disabled:
        raise AuthenticationRequired("this account is disabled")
    _start_session(db, u, response)
    return u
