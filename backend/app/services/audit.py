"""Research audit log. Details are passed through ``redact`` so secrets can never be recorded."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectivity.security import redact
from app.core.logging import get_logger
from app.models import AuditLog

log = get_logger(__name__)
LOCAL_ACTOR = "local-user"

ACTIONS = (
    "portfolio.created",
    "portfolio.modified",
    "portfolio.deleted",
    "dataset.imported",
    "market_data.refreshed",
    "backtest.executed",
    "ml_experiment.executed",
    "experiment.reproduced",
    "report.generated",
    "connection.connected",
    "connection.disconnected",
    "connection.synced",
    "connection.sync_failed",
    "file.imported",
    "api_key.created",
    "api_key.revoked",
    "webhook.created",
    "webhook.deleted",
    "data_quality.assessed",
)


def record(
    db: Session,
    action: str,
    object_type: str | None = None,
    object_id: Any = None,
    details: dict[str, Any] | None = None,
    actor: str = LOCAL_ACTOR,
    commit: bool = True,
) -> None:
    try:
        db.add(
            AuditLog(
                actor=actor,
                action=action,
                object_type=object_type,
                object_id=None if object_id is None else str(object_id),
                details=redact(details or {}),
            )
        )
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        log.exception("failed to write audit record %s", action)


def list_entries(
    db: Session, action: str | None = None, object_type: str | None = None, limit: int = 500
) -> list[dict[str, Any]]:
    q = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(min(limit, 5000))
    if action:
        q = q.where(AuditLog.action == action)
    if object_type:
        q = q.where(AuditLog.object_type == object_type)
    return [
        {
            "id": a.id,
            "created_at": a.created_at.isoformat(),
            "actor": a.actor,
            "action": a.action,
            "object_type": a.object_type,
            "object_id": a.object_id,
            "details": a.details,
        }
        for a in db.scalars(q)
    ]
