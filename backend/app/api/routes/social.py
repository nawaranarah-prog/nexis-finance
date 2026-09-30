"""Accounts and the InstaFin social feed."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.session import get_db
from app.models import User
from app.services import auth, social

router = APIRouter(tags=["social"])
MAX_UPLOAD = 8 * 1024 * 1024


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(_Base):
    username: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=60)


class ProfileUpdate(_Base):
    display_name: str | None = Field(default=None, max_length=60)
    bio: str | None = Field(default=None, max_length=300)
    links: dict[str, str] | None = None


class CommentIn(_Base):
    body: str = Field(min_length=1, max_length=1000)


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
def register(req: Credentials, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    u = auth.register(db, request, response, req.username, req.password, req.display_name)
    return auth.serialize_user(db, u, u, full=True)


@router.post("/auth/login")
def login(req: Credentials, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    u = auth.login(db, request, response, req.username, req.password)
    return auth.serialize_user(db, u, u, full=True)


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
    return auth.serialize_user(db, u, u, full=True)


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
    mode: Literal["latest", "following", "trending"] = "latest",
    symbol: str | None = None,
    tag: str | None = None,
    user: str | None = None,
    before: int | None = None,
    viewer: User | None = Depends(auth.optional_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return social.feed(db, viewer, mode, symbol, tag, user, before)


@router.get("/social/trending")
def trending(db: Session = Depends(get_db)) -> dict[str, Any]:
    return social.trending_tags(db)


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
    return social.add_comment(db, pid, user, req.body)


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
