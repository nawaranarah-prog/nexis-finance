"""Sign in with Google, Reddit or X, and link Reddit / X identities to an existing account.

All three use the OAuth 2.0 authorization-code flow (with PKCE where the provider supports it):

* Google — OpenID Connect; ``NEXIS_GOOGLE_CLIENT_ID`` / ``NEXIS_GOOGLE_CLIENT_SECRET``.
* Reddit — reddit.com/prefs/apps, app type "web app"; ``NEXIS_REDDIT_CLIENT_ID`` / ``NEXIS_REDDIT_CLIENT_SECRET``.
* X — developer.x.com, OAuth 2.0 confidential client; ``NEXIS_X_CLIENT_ID`` / ``NEXIS_X_CLIENT_SECRET``.

Every redirect URI is ``{NEXIS_PUBLIC_URL}/api/auth/oauth/{provider}/callback``.

State, nonce, the PKCE verifier and the purpose of the flow (sign in, or link to the signed-in account) travel in a
short-lived HMAC-signed cookie. Identities are read from the provider over TLS right after the code exchange.
Access tokens are used once to read the identity and are never stored.
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
PROVIDERS = ("google", "reddit", "x")
LINKABLE = ("reddit", "x")
LABEL = {"google": "Google", "reddit": "Reddit", "x": "X"}
USER_AGENT = "web:nexis-finance:1.0 (by /u/nexisfinance)"


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _creds(provider: str) -> tuple[str | None, str | None]:
    s = get_settings()
    return {
        "google": (s.google_client_id, s.google_client_secret),
        "reddit": (s.reddit_client_id, s.reddit_client_secret),
        "x": (s.x_client_id, s.x_client_secret),
    }[provider]


def configured() -> dict[str, bool]:
    return {p: all(_creds(p)) for p in PROVIDERS}


def public_url(request: Request) -> str:
    s = get_settings()
    if s.public_url:
        return s.public_url.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}"


def _redirect_uri(request: Request, provider: str) -> str:
    return f"{public_url(request)}/api/auth/oauth/{provider}/callback"


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


def start(
    provider: str, request: Request, response: Response, next_path: str, mode: str = "signin", user_id: int | None = None
) -> str:
    """Set the state cookie and return the provider's authorization URL. ``mode="link"`` attaches the identity to ``user_id``."""
    if provider not in PROVIDERS:
        raise ConfigurationError("unknown sign-in provider")
    if mode == "link" and (provider not in LINKABLE or user_id is None):
        raise ConfigurationError("only Reddit and X accounts can be linked, and you need to be signed in")
    if not configured()[provider]:
        raise ConfigurationError(f"{LABEL[provider]} sign-in is not set up on this server yet")
    s = get_settings()
    client_id, _ = _creds(provider)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    nxt = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    response.set_cookie(
        STATE_COOKIE,
        _sign(
            {"p": provider, "s": state, "n": nonce, "v": verifier, "next": nxt, "m": mode, "u": user_id, "exp": time.time() + 600}
        ),
        max_age=600,
        httponly=True,
        secure=s.env != "development",
        samesite="lax",
        path="/api/auth/oauth",
    )
    challenge = _b64u(hashlib.sha256(verifier.encode()).digest())
    redirect = _redirect_uri(request, provider)
    if provider == "google":
        params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": "openid email profile",
                  "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
                  "prompt": "select_account"}  # fmt: skip
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    if provider == "reddit":
        params = {"client_id": client_id, "response_type": "code", "state": state, "redirect_uri": redirect,
                  "duration": "temporary", "scope": "identity"}  # fmt: skip
        return "https://www.reddit.com/api/v1/authorize?" + urlencode(params)
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": "users.read tweet.read",
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}  # fmt: skip
    return "https://x.com/i/oauth2/authorize?" + urlencode(params)


def _google(request: Request, code: str, st: dict[str, Any]) -> dict[str, Any]:
    client_id, secret = _creds("google")
    form = {"code": code, "client_id": client_id, "client_secret": secret, "redirect_uri": _redirect_uri(request, "google"),
            "grant_type": "authorization_code", "code_verifier": st["v"]}  # fmt: skip
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
        or claims.get("aud") != client_id
        or claims.get("exp", 0) < time.time()
        or claims.get("nonce") != st["n"]
    ):
        raise AuthenticationRequired("the identity token failed verification")
    email = claims.get("email") if str(claims.get("email_verified", "false")).lower() == "true" else None
    return {"sub": str(claims["sub"]), "email": email, "name": claims.get("name"), "username": None, "avatar": None}


def _token(provider: str, request: Request, code: str, st: dict[str, Any]) -> str:
    client_id, secret = _creds(provider)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(request, provider)}
    if provider == "x":
        form |= {"code_verifier": st["v"], "client_id": client_id or ""}
        url = "https://api.x.com/2/oauth2/token"
    else:
        url = "https://www.reddit.com/api/v1/access_token"
    try:
        r = httpx.post(url, data=form, auth=(client_id or "", secret or ""), headers={"User-Agent": USER_AGENT}, timeout=15)
    except httpx.HTTPError as exc:
        raise AuthenticationRequired(f"{LABEL[provider]} could not be reached — try again") from exc
    token = r.json().get("access_token") if r.status_code == 200 else None
    if not token:
        raise AuthenticationRequired(f"{LABEL[provider]} rejected the sign-in ({r.status_code})")
    return str(token)


def _identity(provider: str, token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    try:
        if provider == "reddit":
            r = httpx.get("https://oauth.reddit.com/api/v1/me", headers=headers, timeout=15)
            d = r.json() if r.status_code == 200 else {}
            if not d.get("id") or not d.get("name"):
                raise AuthenticationRequired("Reddit returned no identity")
            icon = str(d.get("icon_img") or "").split("?")[0] or None
            return {"sub": str(d["id"]), "email": None, "name": d["name"], "username": d["name"], "avatar": icon}
        r = httpx.get("https://api.x.com/2/users/me", params={"user.fields": "profile_image_url"}, headers=headers, timeout=15)
        d = (r.json() if r.status_code == 200 else {}).get("data") or {}
        if not d.get("id") or not d.get("username"):
            raise AuthenticationRequired("X returned no identity")
        return {"sub": str(d["id"]), "email": None, "name": d.get("name") or d["username"], "username": d["username"],
                "avatar": d.get("profile_image_url")}  # fmt: skip
    except httpx.HTTPError as exc:
        raise AuthenticationRequired(f"{LABEL[provider]} could not be reached — try again") from exc


def finish(provider: str, request: Request, code: str | None, state: str | None, user_json: str | None = None) -> dict[str, Any]:
    """Exchange the code and return the verified identity plus where to go and what the flow was for."""
    cookie = request.cookies.get(STATE_COOKIE)
    if not cookie:
        raise AuthenticationRequired("sign-in session expired — try again")
    st = _verify(cookie)
    if provider not in PROVIDERS or st["p"] != provider or not state or not hmac.compare_digest(st["s"], state):
        raise AuthenticationRequired("sign-in could not be verified — try again")
    if not code:
        raise AuthenticationRequired("sign-in was cancelled")
    ident = _google(request, code, st) if provider == "google" else _identity(provider, _token(provider, request, code, st))
    return ident | {"next": st.get("next", "/"), "mode": st.get("m", "signin"), "user_id": st.get("u")}


def profile_url(provider: str, username: str) -> str:
    return f"https://www.reddit.com/user/{username}" if provider == "reddit" else f"https://x.com/{username}"
