"""Email verification and password reset.

Tokens are stateless and signed (HMAC-SHA256 with the app's signing key): ``purpose.user_id.expiry.fingerprint.mac``.
* A **reset** token's fingerprint is derived from the current password hash, so it stops working once used (the
  password changed) — single use without a table. Valid 1 hour.
* A **verification** token's fingerprint is derived from the email address, so changing the address invalidates it.
  Valid 3 days.

Requests never reveal whether an address has an account: "forgot password" answers the same either way (except when
email isn't configured at all, which is said plainly because it's true for everyone).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import time
from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.connectivity.security import signing_key
from app.core.config import get_settings
from app.core.errors import AuthenticationRequired, ValidationFailed
from app.db.base import utcnow
from app.models import User, UserSession
from app.services import auth, email, ratelimit

RESET_TTL = 3600
VERIFY_TTL = 3 * 86400


def _fingerprint(purpose: str, u: User) -> str:
    basis = (u.password_hash or "") if purpose == "reset" else (u.email or "")
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _mac(raw: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(signing_key(), raw.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def make_token(purpose: str, u: User, ttl: int, now: float | None = None) -> str:
    raw = f"{purpose}.{u.id}.{int((now or time.time()) + ttl)}.{_fingerprint(purpose, u)}"
    return f"{raw}.{_mac(raw)}"


def read_token(db: Session, purpose: str, token: str, now: float | None = None) -> User:
    bad = ValidationFailed("This link is invalid or has expired. Request a new one.")
    parts = (token or "").split(".")
    if len(parts) != 5 or parts[0] != purpose:
        raise bad
    raw, mac = ".".join(parts[:4]), parts[4]
    if not hmac.compare_digest(_mac(raw), mac):
        raise bad
    try:
        uid, exp = int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise bad from exc
    if exp < (now or time.time()):
        raise bad
    u = db.get(User, uid)
    if u is None or u.is_disabled or not hmac.compare_digest(_fingerprint(purpose, u), parts[3]):
        raise bad
    return u


def _link(path: str, token: str) -> str:
    return f"{get_settings().public_site_url.rstrip('/')}{path}?token={token}"


def forgot_password(db: Session, request: Request, address: str) -> None:
    if not email.available():
        raise email.EmailUnavailable("Password reset by email isn't available yet. Contact support to regain access.")
    mail = (address or "").strip().lower()
    ratelimit.hit(db, f"forgot:{ratelimit.client_ip(request)}", 5)
    ratelimit.hit(db, f"forgot-mail:{hashlib.sha256(mail.encode()).hexdigest()[:24]}", 3)
    u = db.scalars(select(User).where(func.lower(User.email) == mail)).first() if mail else None
    if u is None or u.is_disabled or u.kind != "person":
        return  # same answer either way
    email.send(u.email or mail, "Reset your Nexis password",
               "Someone (hopefully you) asked to reset the password for your Nexis Finance account.\n\n"
               f"Choose a new password here (the link works for one hour, once):\n{_link('/reset-password', make_token('reset', u, RESET_TTL))}\n\n"
               "If it wasn't you, ignore this email — your password hasn't changed.")  # fmt: skip


def reset_password(db: Session, request: Request, token: str, new_password: str) -> None:
    ratelimit.hit(db, f"reset:{ratelimit.client_ip(request)}", 10)
    u = read_token(db, "reset", token)
    auth._check_password(new_password, u.username, u.email or "", u.phone or "")
    u.password_hash = auth.hash_password(new_password)
    if u.email and u.email_verified_at is None:
        u.email_verified_at = utcnow()  # they proved they read the inbox
    db.execute(delete(UserSession).where(UserSession.user_id == u.id))  # every device signs in again
    db.commit()


def send_verification(db: Session, u: User) -> None:
    if not u.email:
        raise ValidationFailed("Add an email address to your account first.")
    if u.email_verified_at is not None:
        raise ValidationFailed("Your email address is already verified.")
    ratelimit.hit(db, f"verify-send:{u.id}", 5, timedelta(hours=24))
    email.send(u.email, "Confirm your email for Nexis",
               f"Confirm this address for your Nexis Finance account (the link works for 3 days):\n"
               f"{_link('/verify-email', make_token('verify', u, VERIFY_TTL))}\n\nIf you didn't create an account, ignore this email.")  # fmt: skip


def verify_email(db: Session, request: Request, token: str) -> User:
    ratelimit.hit(db, f"verify:{ratelimit.client_ip(request)}", 20)
    u = read_token(db, "verify", token)
    if u.email_verified_at is None:
        u.email_verified_at = utcnow()
        db.commit()
    return u


def guard_login_attempts(db: Session, identifier: str) -> None:
    """Too many wrong passwords for one account → wait (stops password guessing from many IPs)."""
    if ratelimit.count(db, _fail_bucket(identifier), timedelta(minutes=15)) >= 10:
        raise AuthenticationRequired("Too many wrong attempts for this account. Wait 15 minutes, or reset your password.")


def record_failed_login(db: Session, identifier: str) -> None:
    with contextlib.suppress(ratelimit.RateLimited):  # only counting here; guard_login_attempts enforces the limit
        ratelimit.hit(db, _fail_bucket(identifier), 10_000, timedelta(minutes=15))


def _fail_bucket(identifier: str) -> str:
    return f"login-fail:{hashlib.sha256(identifier.strip().lower().encode()).hexdigest()[:24]}"
