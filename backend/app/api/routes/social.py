"""Accounts and the Finstagram social feed."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AuthenticationRequired, NexisError, ValidationFailed
from app.db.session import get_db
from app.models import User
from app.services import auth, newsfeed, oauth, phone, social

router = APIRouter(tags=["social"])
MAX_UPLOAD = 8 * 1024 * 1024


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterIn(_Base):
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    username: str | None = Field(default=None, max_length=30)
    password: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=60)
    # Explicit acceptance of the Terms of Use and Privacy Policy (the versions the form showed).
    accept_terms: bool = False
    terms_version: str | None = Field(default=None, max_length=20)
    privacy_version: str | None = Field(default=None, max_length=20)


class LoginIn(_Base):
    identifier: str | None = Field(default=None, max_length=254, description="email or username")
    username: str | None = Field(default=None, max_length=254, description="alias of identifier")
    password: str = Field(min_length=1, max_length=200)


class FeedbackIn(_Base):
    signal: Literal["more", "less"]


class TopicIn(_Base):
    kind: Literal["symbol", "tag"]
    value: str = Field(min_length=1, max_length=60)


class ProfileUpdate(_Base):
    display_name: str | None = Field(default=None, max_length=60)
    bio: str | None = Field(default=None, max_length=300)
    links: dict[str, str] | None = None
    username: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    language: Literal["en", "ar"] | None = None


class PasswordChange(_Base):
    current_password: str | None = Field(default=None, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class CommentIn(_Base):
    body: str = Field(min_length=1, max_length=1000)
    parent_id: int | None = None


class ReportIn(_Base):
    reason: str | None = Field(default=None, max_length=200)


async def _read_image(f: UploadFile | None) -> bytes | None:
    if f is None or not f.filename:
        return None
    raw = await f.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise ValidationFailed("images must be smaller than 8 MB")
    return raw or None


# ---------------------------------------------------------------- auth


@router.post("/auth/register", status_code=201)
def register(req: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not req.accept_terms or not req.terms_version or not req.privacy_version:
        raise ValidationFailed("you need to accept the Terms of Use and Privacy Policy to create an account")
    u = auth.register(db, request, response, req.username, req.password, req.display_name, req.email, req.phone,
                      accepted=(req.terms_version, req.privacy_version))  # fmt: skip
    return auth.serialize_user(db, u, u, full=True)


@router.post("/auth/login")
def login(req: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    ident = req.identifier or req.username
    if not ident:
        raise ValidationFailed("enter your email or username")
    u = auth.login(db, request, response, ident, req.password)
    return auth.serialize_user(db, u, u, full=True)


class PhoneStart(_Base):
    phone: str = Field(min_length=6, max_length=30)


class PhoneVerify(_Base):
    phone: str = Field(min_length=6, max_length=30)
    code: str = Field(min_length=4, max_length=10)
    display_name: str | None = Field(default=None, max_length=60)


@router.get("/auth/providers")
def providers() -> dict[str, bool]:
    return {"email": True, "phone": True, "phone_otp": phone.otp_enabled(), **oauth.configured()}


@router.post("/auth/phone/start")
def phone_start(req: PhoneStart, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    return auth.phone_start(db, request, req.phone)


@router.post("/auth/phone/verify")
def phone_verify(req: PhoneVerify, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    u = auth.phone_verify(db, request, response, req.phone, req.code, req.display_name)
    return auth.serialize_user(db, u, u, full=True)


@router.get("/auth/oauth/{provider}/start")
def oauth_start(
    provider: str,
    request: Request,
    next: str = "/",
    mode: Literal["signin", "link"] = "signin",
    viewer: User | None = Depends(auth.optional_user),
) -> Response:
    resp = RedirectResponse("/", status_code=302)
    try:
        url = oauth.start(provider, request, resp, next, mode, viewer.id if viewer else None)
    except NexisError as exc:
        back = "/settings" if mode == "link" else "/login"
        return RedirectResponse(f"{back}?error={quote(exc.message)}", status_code=302)
    resp.headers["location"] = url
    return resp


def _oauth_finish(
    provider: str, request: Request, db: Session, code: str | None, state: str | None, user: str | None
) -> Response:
    linking = False
    try:
        ident = oauth.finish(provider, request, code, state, user)
        linking = ident["mode"] == "link"
        resp = RedirectResponse(ident["next"], status_code=302)
        if linking:
            viewer = auth.optional_user(request, db)
            if viewer is None or viewer.id != ident["user_id"]:
                raise AuthenticationRequired("sign in to Nexis again, then link the account")
            auth.link_account(db, viewer, provider, ident["sub"], ident["username"], ident["avatar"])
            resp.headers["location"] = f"{ident['next']}?linked={provider}"
        else:
            auth.sign_in_external(
                db, resp, provider, ident["sub"], ident["email"], ident["name"], ident["username"], ident["avatar"]
            )
    except NexisError as exc:
        resp = RedirectResponse(f"{'/settings' if linking else '/login'}?error={quote(exc.message)}", status_code=302)
    resp.delete_cookie(oauth.STATE_COOKIE, path="/api/auth/oauth")
    return resp


@router.get("/auth/oauth/{provider}/callback")
def oauth_callback_get(
    provider: str, request: Request, code: str | None = None, state: str | None = None, db: Session = Depends(get_db)
) -> Response:
    return _oauth_finish(provider, request, db, code, state, None)


@router.delete("/auth/me/linked/{provider}", status_code=204)
def unlink(provider: Literal["reddit", "x"], user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> Response:
    auth.unlink_account(db, user, provider)
    return Response(status_code=204)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    auth.logout(db, request, response)
    response.status_code = 204
    return response


@router.get("/auth/me")
def me(user: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"user": auth.serialize_user(db, user, user, full=True) if user else None}


@router.patch("/auth/me")
def update_me(req: ProfileUpdate, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    u = auth.update_profile(db, user, req.display_name, req.bio, req.links)
    u = auth.update_account(db, u, req.username, req.email, req.phone, req.language)
    return auth.serialize_user(db, u, u, full=True)


@router.post("/auth/me/password", status_code=204)
def change_password(
    req: PasswordChange, request: Request, user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> Response:
    auth.change_password(db, request, user, req.current_password, req.new_password)
    return Response(status_code=204)


@router.post("/auth/me/logout-everywhere")
def logout_everywhere(request: Request, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, int]:
    return {"signed_out_sessions": auth.logout_everywhere(db, request, user)}


class DeleteAccount(_Base):
    password: str = Field(min_length=1, max_length=200)


@router.post("/auth/me/delete", status_code=204)
def delete_me(
    req: DeleteAccount,
    request: Request,
    response: Response,
    user: User = Depends(auth.require_user),
    db: Session = Depends(get_db),
) -> Response:
    auth.delete_account(db, request, response, user, req.password)
    response.status_code = 204
    return response


@router.post("/auth/me/avatar")
async def avatar(
    image: UploadFile = File(...), user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    raw = await _read_image(image)
    if raw is None:
        raise ValidationFailed("choose an image")
    u = social.set_avatar(db, user, raw)
    return auth.serialize_user(db, u, u, full=True)


# ---------------------------------------------------------------- feed


@router.get("/social/feed")
def feed(
    mode: Literal["latest", "following", "trending", "saved"] = "latest",
    symbol: str | None = None,
    tag: str | None = None,
    user: str | None = None,
    before: int | None = None,
    viewer: User | None = Depends(auth.optional_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return social.feed(db, viewer, mode, symbol, tag, user, before)


@router.get("/social/search")
def search(
    q: str = Query(min_length=1, max_length=80), viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return social.search(db, q, viewer)


@router.get("/social/topics/{kind}/{value}")
def topic(
    kind: str, value: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return social.topic_page(db, kind, value, viewer)


@router.post("/social/topics/follow")
def follow_topic(req: TopicIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.toggle_topic(db, user, req.kind, req.value)


@router.get("/social/following")
def following(user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.my_following(db, user)


@router.post("/social/posts/{pid}/save")
def save(pid: int, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.toggle_save(db, pid, user)


@router.post("/social/posts/{pid}/feedback")
def feedback(pid: int, req: FeedbackIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.feedback(db, pid, user, req.signal)


@router.get("/social/news/refresh")
def refresh_news(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Imports new headlines when the last import is older than 20 minutes (also called by the daily cron)."""
    secret = get_settings().cron_secret
    forced = bool(secret) and request.headers.get("authorization") == f"Bearer {secret}"
    out = newsfeed.refresh(db, force=forced)
    if forced:  # the daily cron also advances Pulse and prepares digests
        from app.services import alerts, pulse_engine

        out["pulse"] = pulse_engine.tick(db, max_assets=4, force=True)
        out["digests"] = alerts.run_digests(db)
    return out


@router.get("/social/trending")
def trending(viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.trending_tags(db, viewer)


@router.post("/social/posts", status_code=201)
async def create_post(
    request: Request,
    body: str = Form(default="", max_length=2200),
    image: UploadFile | None = File(default=None),
    user: User = Depends(auth.require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    raw = await _read_image(image)
    p = social.create_post(db, request, user, body, raw)
    return social.get_post(db, p.id, user)


@router.get("/social/posts/{pid}")
def get_post(pid: int, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.get_post(db, pid, viewer)


@router.delete("/social/posts/{pid}", status_code=204)
def delete_post(pid: int, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> Response:
    social.delete_post(db, pid, user)
    return Response(status_code=204)


@router.post("/social/posts/{pid}/like")
def like(pid: int, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.toggle_like(db, pid, user)


@router.get("/social/posts/{pid}/comments")
def list_comments(
    pid: int, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return social.comments(db, pid, viewer)


@router.post("/social/posts/{pid}/comments", status_code=201)
def add_comment(
    pid: int, req: CommentIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return social.add_comment(db, pid, user, req.body, req.parent_id)


@router.delete("/social/comments/{cid}", status_code=204)
def delete_comment(cid: int, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> Response:
    social.delete_comment(db, cid, user)
    return Response(status_code=204)


@router.post("/social/posts/{pid}/report")
def report(pid: int, req: ReportIn, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.report(db, pid, user, req.reason)


@router.get("/social/users/{username}")
def profile(username: str, viewer: User | None = Depends(auth.optional_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.profile(db, username, viewer)


@router.post("/social/users/{username}/follow")
def follow(username: str, user: User = Depends(auth.require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.toggle_follow(db, username, user)


@router.get("/social/media/{mid}")
def media(mid: int, db: Session = Depends(get_db)) -> Response:
    m = social.media(db, mid)
    return Response(m.data, media_type=m.content_type, headers={"Cache-Control": "public, max-age=31536000, immutable"})
