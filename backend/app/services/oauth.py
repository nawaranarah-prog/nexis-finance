"""Sign in with Google (OpenID Connect authorization-code flow with PKCE).

Enabled when ``NEXIS_GOOGLE_CLIENT_ID`` / ``NEXIS_GOOGLE_CLIENT_SECRET`` are set (Google Cloud console →
OAuth client of type "Web application", redirect URI ``{NEXIS_PUBLIC_URL}/api/auth/oauth/google/callback``).

State, nonce and the PKCE verifier travel in a short-lived HMAC-signed cookie. The ID token is received
directly from Google's token endpoint over TLS (OpenID Connect Core §3.1.3.7), and its issuer, audience,
expiry and nonce are checked before the account is looked up or created.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Request, Response

from app.connectivity.security import signing_key
from app.core.config import get_settings
from app.core.errors import AuthenticationRequired, ConfigurationError

STATE_COOKIE = "nexis_oauth"
PROVIDERS = ("google",)


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def configured() -> dict[str, bool]:
    s = get_settings()
    return {"google": bool(s.google_client_id and s.google_client_secret)}


def public_url(request: Request) -> str:
    s = get_settings()
    if s.public_url:
        return s.public_url.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}"


def _sign(data: dict[str, Any]) -> str:
    raw = _b64u(json.dumps(data, separators=(",", ":")).encode())
    mac = hmac.new(signing_key(), raw.encode(), hashlib.sha256).digest()
    return f"{raw}.{_b64u(mac)}"


def _verify(token: str) -> dict[str, Any]:
    try:
        raw, mac = token.split(".")
    except ValueError as exc:
        raise AuthenticationRequired("sign-in session expired — try again") from exc
    good = hmac.new(signing_key(), raw.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(good, _b64u_decode(mac)):
        raise AuthenticationRequired("sign-in session expired — try again")
    data = json.loads(_b64u_decode(raw))
    if data.get("exp", 0) < time.time():
        raise AuthenticationRequired("sign-in took too long — try again")
    return data


def start(provider: str, request: Request, response: Response, next_path: str) -> str:
    if provider not in PROVIDERS:
        raise ConfigurationError("unknown sign-in provider")
    if not configured()[provider]:
        raise ConfigurationError("Google sign-in is not set up on this server yet")
    s = get_settings()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    nxt = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    response.set_cookie(
        STATE_COOKIE,
        _sign({"p": provider, "s": state, "n": nonce, "v": verifier, "next": nxt, "exp": time.time() + 600}),
        max_age=600,
        httponly=True,
        secure=s.env != "development",
        samesite="lax",
        path="/api/auth/oauth",
    )
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": f"{public_url(request)}/api/auth/oauth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64u(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def finish(provider: str, request: Request, code: str | None, state: str | None, user_json: str | None = None) -> dict[str, Any]:
    """Exchange the code and return ``{"sub", "email", "name", "next"}`` for a verified identity."""
    cookie = request.cookies.get(STATE_COOKIE)
    if not cookie:
        raise AuthenticationRequired("sign-in session expired — try again")
    st = _verify(cookie)
    if provider not in PROVIDERS or st["p"] != provider or not state or not hmac.compare_digest(st["s"], state):
        raise AuthenticationRequired("sign-in could not be verified — try again")
    if not code:
        raise AuthenticationRequired("sign-in was cancelled")
    s = get_settings()
    form = {
        "code": code,
        "client_id": s.google_client_id,
        "client_secret": s.google_client_secret,
        "redirect_uri": f"{public_url(request)}/api/auth/oauth/google/callback",
        "grant_type": "authorization_code",
        "code_verifier": st["v"],
    }
    try:
        r = httpx.post("https://oauth2.googleapis.com/token", data=form, timeout=15)
    except httpx.HTTPError as exc:
        raise AuthenticationRequired("Google could not be reached — try again") from exc
    if r.status_code != 200:
        raise AuthenticationRequired(f"Google rejected the sign-in ({r.status_code})")
    id_token = r.json().get("id_token")
    if not id_token or id_token.count(".") != 2:
        raise AuthenticationRequired("Google returned no identity")
    claims = json.loads(_b64u_decode(id_token.split(".")[1]))
    if (
        claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com")
        or claims.get("aud") != s.google_client_id
        or claims.get("exp", 0) < time.time()
        or claims.get("nonce") != st["n"]
    ):
        raise AuthenticationRequired("the identity token failed verification")
    email = claims.get("email") if str(claims.get("email_verified", "false")).lower() == "true" else None
    return {"sub": str(claims["sub"]), "email": email, "name": claims.get("name"), "next": st.get("next", "/")}
