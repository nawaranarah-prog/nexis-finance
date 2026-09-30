"""Authenticated public API (read-only).

Clients send ``Authorization: Bearer nx_…``. Keys are created on the Developer page; only their SHA-256
hash is stored, revoked keys are rejected, and every call updates ``last_used_at``. Nexis is a
single-owner deployment, so a key grants read access to that owner's data only — there is no
cross-tenant data in the database to expose.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectivity.security import hash_api_key
from app.core.errors import NexisError
from app.db.base import utcnow
from app.db.session import get_db
from app.models import ApiKey
from app.services import book, experiments, insights, portfolios, reports


class Unauthorized(NexisError):
    status_code = 401
    code = "unauthorized"


def require_key(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> ApiKey:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer API key")
    token = authorization.split(" ", 1)[1].strip()
    k = db.scalars(select(ApiKey).where(ApiKey.key_hash == hash_api_key(token))).first()
    if k is None or k.revoked_at is not None:
        raise Unauthorized("invalid or revoked API key")
    k.last_used_at = utcnow()
    db.commit()
    return k


router = APIRouter(prefix="/v1", tags=["public API v1"], dependencies=[Depends(require_key)])


@router.get("/me")
def me(key: ApiKey = Depends(require_key)) -> dict[str, Any]:
    return {"key_name": key.name, "prefix": key.prefix, "scopes": key.scopes}


@router.get("/portfolio")
def my_portfolio(scope: str = "all", method: str = "fifo", db: Session = Depends(get_db)) -> dict[str, Any]:
    a = book.analytics(db, scope, method)
    return {
        k: a[k]
        for k in (
            "scope",
            "cost_basis_method",
            "totals",
            "accounts",
            "positions",
            "allocation",
            "industry",
            "currency",
            "concentration",
            "metrics",
            "benchmark",
            "warnings",
        )
    }


@router.get("/portfolio/diagnostics")
def my_diagnostics(scope: str = "all", db: Session = Depends(get_db)) -> dict[str, Any]:
    return insights.diagnostics(db, scope)


@router.get("/portfolios")
def research_portfolios(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [portfolios.serialize(p) for p in portfolios.list_portfolios(db)]


@router.get("/portfolios/{pid}/analytics")
def research_analytics(pid: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    a = portfolios.analytics(db, pid)
    return {k: a[k] for k in ("portfolio", "period", "summary", "benchmark", "var", "risk_decomposition", "concentration")}


@router.get("/experiments")
def list_experiments(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [experiments.serialize(e) for e in experiments.list_experiments(db)]


@router.get("/reports")
def list_reports(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return reports.list_reports(db)
