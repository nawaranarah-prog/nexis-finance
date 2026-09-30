"""Credential protection.

* Secrets are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256) using ``NEXIS_SECRET_KEY``.
* Development without a configured key uses a generated key file (``backend/.nexis_secret``,
  git-ignored). Any other environment refuses to store credentials without an explicit key.
* Plaintext credentials never leave the backend: API responses carry only a masked hint.
* ``redact`` scrubs secret-looking values before anything is logged or audited.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
import secrets
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import BACKEND_DIR, get_settings
from app.core.errors import ConfigurationError
from app.core.logging import get_logger

log = get_logger(__name__)
KEY_FILE = BACKEND_DIR / ".nexis_secret"
_SECRET_FIELDS = re.compile(r"(secret|token|password|api[_-]?key|authorization|credential)", re.I)


def _normalise_key(raw: str) -> bytes:
    raw_b = raw.strip().encode()
    try:
        if len(base64.urlsafe_b64decode(raw_b)) == 32:
            return raw_b
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        pass
    # Any other passphrase is stretched deterministically into a 32-byte key.
    return base64.urlsafe_b64encode(hashlib.sha256(raw_b).digest())


@lru_cache
def get_fernet() -> Fernet:
    settings = get_settings()
    if settings.secret_key:
        return Fernet(_normalise_key(settings.secret_key))
    if settings.env != "development":
        raise ConfigurationError("NEXIS_SECRET_KEY must be set to store connection credentials outside development")
    if KEY_FILE.exists():
        return Fernet(KEY_FILE.read_text().strip().encode())
    key = Fernet.generate_key()
    KEY_FILE.write_text(key.decode())
    with contextlib.suppress(OSError):
        KEY_FILE.chmod(0o600)
    log.warning("generated a development encryption key at %s; set NEXIS_SECRET_KEY in production", KEY_FILE.name)
    return Fernet(key)


def encrypt_json(data: dict[str, Any]) -> str:
    return get_fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict[str, Any]:
    try:
        return json.loads(get_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise ConfigurationError("stored credentials cannot be decrypted (was NEXIS_SECRET_KEY changed?) — reconnect") from exc


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return f"••••{value[-4:]}" if len(value) > 6 else "••••"


def redact(obj: Any) -> Any:
    """Recursively replace values under secret-like keys; used before logging or auditing."""
    if isinstance(obj, dict):
        return {k: ("[redacted]" if _SECRET_FIELDS.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def new_api_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, sha256 hash). Only the hash is persisted."""
    token = "nx_" + secrets.token_urlsafe(32)
    return token, token[:10], hash_api_key(token)


def hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
