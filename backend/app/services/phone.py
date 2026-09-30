"""Phone numbers: normalisation to E.164 (UAE-first) and optional SMS one-time codes via Twilio Verify.

Without Twilio credentials, phone accounts sign up and sign in with a password (the number is a login
identifier, not verified). With ``NEXIS_TWILIO_ACCOUNT_SID``, ``NEXIS_TWILIO_AUTH_TOKEN`` and
``NEXIS_TWILIO_VERIFY_SID`` set, a 6-digit code is sent by SMS and checked by Twilio — no codes are
stored here.
"""

from __future__ import annotations

import re

import httpx

from app.core.config import get_settings
from app.core.errors import AuthenticationRequired, ProviderError, ValidationFailed

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize(raw: str) -> str:
    """``050 123 4567`` / ``0501234567`` / ``501234567`` / ``00971…`` / ``+971…`` → ``+971501234567``."""
    s = re.sub(r"[\s\-().]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    elif s.startswith("0") and len(s) == 10:  # UAE mobile written locally: 05X XXX XXXX
        s = "+971" + s[1:]
    elif re.fullmatch(r"5\d{8}", s):
        s = "+971" + s
    elif not s.startswith("+") and s.startswith("971"):
        s = "+" + s
    if not E164.match(s):
        raise ValidationFailed("enter a valid mobile number, e.g. 050 123 4567 or +971 50 123 4567")
    return s


def looks_like_phone(identifier: str) -> bool:
    return bool(re.fullmatch(r"[+\d][\d\s\-().]{6,20}", identifier.strip()))


def otp_enabled() -> bool:
    s = get_settings()
    return bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_verify_sid)


def _twilio(path: str, data: dict[str, str]) -> dict:
    s = get_settings()
    try:
        r = httpx.post(
            f"https://verify.twilio.com/v2/Services/{s.twilio_verify_sid}/{path}",
            data=data,
            auth=(s.twilio_account_sid or "", s.twilio_auth_token or ""),
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise ProviderError("the SMS service could not be reached — try again") from exc
    if r.status_code >= 400:
        try:
            msg = r.json().get("message")
        except ValueError:
            msg = None
        raise ProviderError(f"the SMS service rejected the request{': ' + msg if msg else ''}")
    return r.json()


def send_code(number: str) -> None:
    _twilio("Verifications", {"To": number, "Channel": "sms"})


def check_code(number: str, code: str) -> None:
    if not re.fullmatch(r"\d{4,10}", code.strip()):
        raise ValidationFailed("enter the code from the SMS")
    res = _twilio("VerificationCheck", {"To": number, "Code": code.strip()})
    if res.get("status") != "approved":
        raise AuthenticationRequired("that code is not correct or has expired")
