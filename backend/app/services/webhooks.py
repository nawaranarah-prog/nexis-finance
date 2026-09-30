"""Outbound webhooks.

Delivery contract
-----------------
``POST <url>`` with a JSON body ``{"id", "event", "created_at", "data"}`` and headers
``X-Nexis-Event``, ``X-Nexis-Delivery`` (UUID) and ``X-Nexis-Signature: sha256=<hex HMAC of the raw
body with the endpoint secret>``. A 2xx response marks the delivery *delivered*; otherwise it is
retried up to 3 attempts with 1 s / 3 s back-off, then marked *failed*. Every attempt is recorded.
Delivery runs on a background thread so a slow receiver never blocks the event producer.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectivity.security import decrypt_json, encrypt_json
from app.core.config import get_settings
from app.core.errors import ConfigurationError, NotFoundError
from app.core.logging import get_logger
from app.db import session as db_session
from app.db.base import utcnow
from app.models import WebhookDelivery, WebhookEndpoint

log = get_logger(__name__)
EVENTS = (
    "backtest.completed",
    "experiment.completed",
    "data.sync.completed",
    "data.sync.failed",
    "portfolio.updated",
    "report.generated",
    "ping",
)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nexis-webhook")
INLINE = get_settings().jobs_inline  # tests may also set this to run synchronously
BACKOFF = (1.0, 3.0)


def validate_url(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ConfigurationError("webhook URL must be an absolute http(s) URL")
    if get_settings().env != "development":
        if u.scheme != "https":
            raise ConfigurationError("webhook URLs must use https outside development")
        try:
            ip = ipaddress.ip_address(u.hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise ConfigurationError("webhook URL must not target a private or loopback address")
        except ValueError:
            if u.hostname in ("localhost",):
                raise ConfigurationError("webhook URL must not target localhost") from None


def create_endpoint(db: Session, url: str, events: list[str], description: str | None) -> tuple[WebhookEndpoint, str]:
    validate_url(url)
    bad = [e for e in events if e not in EVENTS]
    if bad or not events:
        raise ConfigurationError("unknown or empty event list", details={"allowed": EVENTS, "invalid": bad})
    secret = "whsec_" + secrets.token_urlsafe(24)
    ep = WebhookEndpoint(url=url, secret_encrypted=encrypt_json({"secret": secret}), events=events, description=description)
    db.add(ep)
    db.commit()
    return ep, secret


def delete_endpoint(db: Session, endpoint_id: int) -> None:
    ep = db.get(WebhookEndpoint, endpoint_id)
    if ep is None:
        raise NotFoundError(f"webhook {endpoint_id} not found")
    db.delete(ep)
    db.commit()


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def emit(event: str, data: dict[str, Any]) -> int:
    """Queue ``event`` for every active endpoint subscribed to it. Returns the number queued."""
    if event not in EVENTS:
        raise ConfigurationError(f"unknown event {event}")
    with db_session.SessionLocal() as db:
        eps = [e for e in db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))) if event in e.events]
        ids = []
        for ep in eps:
            d = WebhookDelivery(
                endpoint_id=ep.id,
                delivery_uuid=str(uuid.uuid4()),
                event=event,
                payload={"event": event, "created_at": utcnow().isoformat() + "Z", "data": data},
            )
            db.add(d)
            db.flush()
            ids.append(d.id)
        db.commit()
    for did in ids:
        if INLINE:
            _deliver(did)
        else:
            _pool.submit(_deliver, did)
    return len(ids)


def _deliver(delivery_id: int) -> None:
    with db_session.SessionLocal() as db:
        d = db.get(WebhookDelivery, delivery_id)
        if d is None:
            return
        ep = db.get(WebhookEndpoint, d.endpoint_id)
        if ep is None:
            return
        secret = decrypt_json(ep.secret_encrypted)["secret"]
        body = json.dumps({"id": d.delivery_uuid, **d.payload}, separators=(",", ":"), default=str).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Nexis-Event": d.event,
            "X-Nexis-Delivery": d.delivery_uuid,
            "X-Nexis-Signature": sign(secret, body),
            "User-Agent": "Nexis-Webhooks/1.0",
        }
        for attempt in range(3):
            d.attempts = attempt + 1
            try:
                r = httpx.post(ep.url, content=body, headers=headers, timeout=5.0)
                d.response_code = r.status_code
                if 200 <= r.status_code < 300:
                    d.status, d.delivered_at, d.error = "delivered", utcnow(), None
                    db.commit()
                    return
                d.error = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                d.error = exc.__class__.__name__
            db.commit()
            if attempt < 2:
                time.sleep(BACKOFF[attempt])
        d.status = "failed"
        db.commit()
        log.warning("webhook delivery %s to endpoint %s failed after 3 attempts", d.delivery_uuid, ep.id)


def safe_emit(event: str, data: dict[str, Any]) -> None:
    try:
        emit(event, data)
    except Exception:
        log.exception("failed to queue webhook event %s", event)


def list_endpoints(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": e.id,
            "url": e.url,
            "events": e.events,
            "active": e.active,
            "description": e.description,
            "created_at": e.created_at.isoformat(),
        }
        for e in db.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.id))
    ]


def list_deliveries(db: Session, endpoint_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    q = select(WebhookDelivery).order_by(WebhookDelivery.id.desc()).limit(limit)
    if endpoint_id:
        q = q.where(WebhookDelivery.endpoint_id == endpoint_id)
    return [
        {
            "id": d.id,
            "endpoint_id": d.endpoint_id,
            "delivery_uuid": d.delivery_uuid,
            "event": d.event,
            "status": d.status,
            "attempts": d.attempts,
            "response_code": d.response_code,
            "error": d.error,
            "created_at": d.created_at.isoformat(),
            "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
        }
        for d in db.scalars(q)
    ]
