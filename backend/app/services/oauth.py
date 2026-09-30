"""Sign in with Google and Sign in with Apple (OpenID Connect authorization-code flow).

Enabled per provider when its credentials are configured:

* Google — ``NEXIS_GOOGLE_CLIENT_ID`` / ``NEXIS_GOOGLE_CLIENT_SECRET`` (Google Cloud console → OAuth client,
  redirect URI ``{NEXIS_PUBLIC_URL}/api/auth/oauth/google/callback``);
* Apple — ``NEXIS_APPLE_CLIENT_ID`` (Services ID), ``NEXIS_APPLE_TEAM_ID``, ``NEXIS_APPLE_KEY_ID`` and
  ``NEXIS_APPLE_PRIVATE_KEY`` (the .p8 key), redirect URI ``{NEXIS_PUBLIC_URL}/api/auth/oauth/apple/callback``.

State, nonce and the PKCE verifier travel in a short-lived HMAC-signed cookie. The ID token is received
directly from the provider's token endpoint over TLS (OpenID Connect Core §3.1.3.7), and its issuer,
audience, expiry and nonce are checked before the account is looked up or created.
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
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import Request, Response

from app.connectivity.security import signing_key
from app.core.config import get_settings
from app.core.errors import AuthenticationRequired, ConfigurationError

STATE_COOKIE = "nexis_oauth"
PROVIDERS = ("google", "apple")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def configured() -> dict[str, bool]:
    s = get_settings()
    return {
        "google": bool(s.google_client_id and s.google_client_secret),
        "apple": bool(s.apple_client_id and s.apple_team_id and s.apple_key_id and s.apple_private_key),
    }


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
        raise ConfigurationError(f"{provider.title()} sign-in is not set up on this server yet")
    s = get_settings()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    nxt = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    redirect_uri = f"{public_url(request)}/api/auth/oauth/{provider}/callback"
    response.set_cookie(
        STATE_COOKIE,
        _sign({"p": provider, "s": state, "n": nonce, "v": verifier, "next": nxt, "exp": time.time() + 600}),
        max_age=600,
        httponly=True,
        secure=s.env != "development",
        # Apple returns with a cross-site POST (form_post), which SameSite=Lax cookies would not survive.
        samesite="none" if s.env != "development" else "lax",
        path="/api/auth/oauth",
    )
    if provider == "google":
        params = {
            "client_id": s.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": _b64u(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    params = {
        "client_id": s.apple_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "response_mode": "form_post",
        "scope": "name email",
        "state": state,
        "nonce": nonce,
    }
    return "https://appleid.apple.com/auth/authorize?" + urlencode(params)


def _apple_client_secret() -> str:
    s = get_settings()
    key = serialization.load_pem_private_key((s.apple_private_key or "").replace("\\n", "\n").encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    now = int(time.time())
    header = _b64u(json.dumps({"alg": "ES256", "kid": s.apple_key_id}).encode())
    payload = _b64u(
        json.dumps(
            {"iss": s.apple_team_id, "iat": now, "exp": now + 300, "aud": "https://appleid.apple.com", "sub": s.apple_client_id}
        ).encode()
    )
    der = key.sign(f"{header}.{payload}".encode(), ec.ECDSA(hashes.SHA256()))
    r, sig_s = decode_dss_signature(der)
    return f"{header}.{payload}.{_b64u(r.to_bytes(32, 'big') + sig_s.to_bytes(32, 'big'))}"


def finish(provider: str, request: Request, code: str | None, state: str | None, user_json: str | None = None) -> dict[str, Any]:
    """Exchange the code and return ``{"sub", "email", "name", "next"}`` for a verified identity."""
    cookie = request.cookies.get(STATE_COOKIE)
    if not cookie:
        raise AuthenticationRequired("sign-in session expired — try again")
    st = _verify(cookie)
    if st["p"] != provider or not state or not hmac.compare_digest(st["s"], state):
        raise AuthenticationRequired("sign-in could not be verified — try again")
    if not code:
        raise AuthenticationRequired("sign-in was cancelled")
    s = get_settings()
    redirect_uri = f"{public_url(request)}/api/auth/oauth/{provider}/callback"
    if provider == "google":
        token_url, issuers, aud = (
            "https://oauth2.googleapis.com/token",
            {"https://accounts.google.com", "accounts.google.com"},
            s.google_client_id,
        )
        form = {"code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret, "redirect_uri": redirect_uri,
                "grant_type": "authorization_code", "code_verifier": st["v"]}  # fmt: skip
    else:
        token_url, issuers, aud = "https://appleid.apple.com/auth/token", {"https://appleid.apple.com"}, s.apple_client_id
        form = {"code": code, "client_id": s.apple_client_id, "client_secret": _apple_client_secret(), "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"}  # fmt: skip
    try:
        r = httpx.post(token_url, data=form, timeout=15)
    except httpx.HTTPError as exc:
        raise AuthenticationRequired(f"{provider.title()} could not be reached — try again") from exc
    if r.status_code != 200:
        raise AuthenticationRequired(f"{provider.title()} rejected the sign-in ({r.status_code})")
    id_token = r.json().get("id_token")
    if not id_token or id_token.count(".") != 2:
        raise AuthenticationRequired(f"{provider.title()} returned no identity")
    claims = json.loads(_b64u_decode(id_token.split(".")[1]))
    if (
        claims.get("iss") not in issuers
        or claims.get("aud") != aud
        or claims.get("exp", 0) < time.time()
        or claims.get("nonce") != st["n"]
    ):
        raise AuthenticationRequired("the identity token failed verification")
    email = claims.get("email") if str(claims.get("email_verified", "false")).lower() == "true" else None
    name = claims.get("name")
    if provider == "apple" and user_json:  # Apple sends the name only on the first sign-in
        try:
            n = json.loads(user_json).get("name") or {}
            name = " ".join(x for x in (n.get("firstName"), n.get("lastName")) if x) or name
        except ValueError:
            pass
    return {"sub": str(claims["sub"]), "email": email, "name": name, "next": st.get("next", "/")}
