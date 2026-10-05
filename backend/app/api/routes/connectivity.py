"""Connections, imports, portfolio intelligence, lineage, reconciliation, audit and developer settings."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectivity.cost_basis import METHODS
from app.connectivity.security import new_api_key
from app.core.config import PROJECT_DIR, get_settings
from app.core.errors import ConfigurationError, NotFoundError
from app.db.base import utcnow
from app.db.session import get_db
from app.models import ApiKey, ImportBatch
from app.services import assistant, audit, auth, book, connections, imports, insights, intel, jobs, webhooks

router = APIRouter(tags=["connectivity & intelligence"])
SAMPLES_DIR = PROJECT_DIR / "data" / "samples"


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_key: str
    display_name: str | None = Field(default=None, max_length=160)
    credentials: dict[str, str] | None = None
    config: dict[str, Any] | None = None


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any]


def _method(m: str) -> str:
    if m not in METHODS:
        raise ConfigurationError(f"cost-basis method must be one of {METHODS}")
    return m


# ---------------------------------------------------------------- connections


@router.get("/connections/marketplace")
def marketplace(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return connections.marketplace(db)


@router.get("/connections")
def list_connections(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    from app.models import Connection

    return [connections.serialize(c) for c in db.scalars(select(Connection).order_by(Connection.id))]


@router.post("/connections", status_code=201, dependencies=[Depends(auth.workspace_editor)])
def create_connection(req: ConnectRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return connections.serialize(connections.connect(db, req.provider_key, req.display_name, req.credentials, req.config))


@router.patch("/connections/{cid}", dependencies=[Depends(auth.workspace_editor)])
def update_connection(cid: int, req: ConfigUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return connections.serialize(connections.update_config(db, cid, req.config))


@router.post("/connections/{cid}/disconnect", dependencies=[Depends(auth.workspace_editor)])
def disconnect(cid: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return connections.serialize(connections.disconnect(db, cid))


@router.delete("/connections/{cid}", status_code=204, dependencies=[Depends(auth.workspace_editor)])
def delete_connection(cid: int, db: Session = Depends(get_db)) -> Response:
    connections.delete(db, cid)
    return Response(status_code=204)


@router.post("/connections/{cid}/sync", status_code=202, dependencies=[Depends(auth.workspace_editor)])
def sync(cid: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    connections.check_syncable(db, connections.get_connection(db, cid))
    return jobs.serialize(
        jobs.submit(db, "sync", {"connection_id": cid}, lambda s, p, prog: connections.sync(s, p["connection_id"]))
    )


@router.get("/connections/sync-runs")
def sync_runs(connection_id: int | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return connections.list_runs(db, connection_id)


# ---------------------------------------------------------------- imports


async def _read(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise ConfigurationError(f"file exceeds the {get_settings().max_upload_mb} MB upload limit")
    name = (file.filename or "").lower()
    if not name.endswith((".csv", ".json", ".xlsx", ".txt")):
        raise ConfigurationError("only .csv, .json or .xlsx files are accepted")
    return content


@router.post("/imports/preview", dependencies=[Depends(auth.workspace_editor)])
async def import_preview(file: UploadFile = File(...), kind: str | None = Form(None)) -> dict[str, Any]:
    return imports.preview(await _read(file), file.filename or "upload.csv", kind or None)


@router.post("/imports", status_code=201, dependencies=[Depends(auth.workspace_editor)])
async def import_commit(
    file: UploadFile = File(...),
    source_label: str = Form(..., min_length=2, max_length=160),
    kind: str = Form(...),
    mapping: str = Form(...),
    options: str = Form("{}"),
    force: bool = Form(False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        mp = json.loads(mapping)
        opts = json.loads(options)
    except json.JSONDecodeError as exc:
        raise ConfigurationError("mapping and options must be JSON objects") from exc
    if not isinstance(mp, dict) or not isinstance(opts, dict):
        raise ConfigurationError("mapping and options must be JSON objects")
    return imports.import_file(db, await _read(file), file.filename or "upload.csv", source_label, kind, mp, opts, force=force)


@router.get("/imports")
def list_imports(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [imports.serialize_batch(b) for b in db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc()))]


SAMPLE_FILES = [
    {"file": "brokerage_a_holdings.csv", "label": "Sample Brokerage A", "kind": "holdings", "options": {"as_of": "2026-09-25"}},
    {"file": "brokerage_a_transactions.csv", "label": "Sample Brokerage A", "kind": "transactions", "options": {}},
    {
        "file": "brokerage_b_positions.xlsx",
        "label": "Sample Brokerage B",
        "kind": "holdings",
        "options": {"institution": "Sample Brokerage B", "as_of": "2026-09-26"},
    },
    {
        "file": "retirement_account.json",
        "label": "Sample Retirement Plan",
        "kind": "holdings",
        "options": {
            "institution": "Sample Retirement Plan",
            "account": "IRA-001",
            "account_name": "Retirement (IRA)",
            "as_of": "2026-09-24",
        },
    },
]


@router.get("/imports/samples")
def samples() -> list[dict[str, Any]]:
    return [{**s, "available": (SAMPLES_DIR / s["file"]).exists()} for s in SAMPLE_FILES]


@router.post("/imports/samples/load", status_code=201, dependencies=[Depends(auth.workspace_editor)])
def load_samples(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Import the bundled, clearly fictional sample statements through the normal import pipeline."""
    results = []
    for s in SAMPLE_FILES:
        path: Path = SAMPLES_DIR / s["file"]
        if not path.exists():
            raise NotFoundError(f"sample file {s['file']} is missing")
        content = path.read_bytes()
        prev = imports.preview(content, s["file"], s["kind"])
        opts = {"institution": s["label"], **s["options"]}
        try:
            results.append(
                imports.import_file(
                    db, content, s["file"], s["label"], s["kind"], prev["proposal"]["mapping"], opts, is_sample=True
                )
            )
        except Exception as exc:
            db.rollback()
            results.append({"file_name": s["file"], "error": getattr(exc, "message", str(exc))})
    return {
        "results": results,
        "note": "Sample files contain fictional quantities for demonstration; they are labelled as sample user-imported data.",
    }


# ---------------------------------------------------------------- intelligence


@router.get("/intelligence/summary")
def data_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    return insights.data_summary(db)


@router.get("/intelligence/overview")
def overview(scope: str = "all", method: str = "fifo", db: Session = Depends(get_db)) -> dict[str, Any]:
    return insights.financial_intelligence(db, scope, _method(method))


@router.get("/intelligence/portfolio")
def my_portfolio(
    scope: str = "all",
    method: str = "fifo",
    benchmark: str | None = None,
    risk_free_rate: float | None = Query(default=None, ge=-0.05, le=0.25),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return book.analytics(db, scope, _method(method), benchmark, risk_free_rate)


@router.get("/intelligence/xray")
def xray(scope: str = "all", method: str = "fifo", db: Session = Depends(get_db)) -> dict[str, Any]:
    return insights.xray(db, scope, _method(method))


@router.get("/intelligence/attribution")
def attribution(
    scope: str = "all", start: date | None = None, end: date | None = None, method: str = "fifo", db: Session = Depends(get_db)
) -> dict[str, Any]:
    return insights.attribution(
        db, scope, start.isoformat() if start else None, end.isoformat() if end else None, _method(method)
    )


@router.get("/intelligence/risk-drilldown")
def drilldown(
    scope: str = "all",
    method: str = "fifo",
    confidence: float = Query(default=0.95, ge=0.9, le=0.99),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return insights.risk_drilldown(db, scope, _method(method), confidence)


@router.get("/intelligence/diagnostics")
def diagnostics(scope: str = "all", method: str = "fifo", db: Session = Depends(get_db)) -> dict[str, Any]:
    return insights.diagnostics(db, scope, _method(method))


@router.get("/intelligence/graph")
def graph(scope: str = "all", method: str = "fifo", db: Session = Depends(get_db)) -> dict[str, Any]:
    return intel.intelligence_graph(db, scope, _method(method))


@router.get("/intelligence/entity/{kind}/{key}")
def entity(kind: str, key: str, scope: str = "all", method: str = "fifo", db: Session = Depends(get_db)) -> dict[str, Any]:
    return intel.entity_detail(db, kind, key, scope, _method(method))


@router.get("/intelligence/transactions")
def transactions(
    method: str = "fifo",
    account_id: int | None = None,
    symbol: str | None = None,
    tx_type: str | None = None,
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return intel.transaction_analytics(db, _method(method), account_id, symbol, tx_type, start, end)


@router.get("/intelligence/reconciliation")
def reconciliation(db: Session = Depends(get_db)) -> dict[str, Any]:
    return intel.reconciliation(db)


@router.get("/intelligence/economic")
def economic(db: Session = Depends(get_db)) -> dict[str, Any]:
    return insights.economic_overview(db)


@router.get("/intelligence/fundamentals")
def fundamentals(symbols: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return insights.fundamentals(db, [s.strip().upper() for s in symbols.split(",")] if symbols else None)


# ---------------------------------------------------------------- lineage & audit


@router.get("/lineage")
def lineage(db: Session = Depends(get_db)) -> dict[str, Any]:
    return intel.lineage_overview(db)


@router.get("/lineage/batches/{batch_id}")
def lineage_batch(batch_id: int, status: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return intel.batch_records(db, batch_id, status)


@router.get("/lineage/source-record/{record_id}")
def lineage_source_record(record_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return intel.source_record(db, record_id)


@router.get("/lineage/{entity_type}/{entity_id}")
def lineage_record(entity_type: str, entity_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return intel.record_lineage(db, entity_type, entity_id)


@router.get("/audit-log")
def audit_log(
    action: str | None = None,
    object_type: str | None = None,
    limit: int = Query(default=500, le=5000),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return audit.list_entries(db, action, object_type, limit)


# ---------------------------------------------------------------- developer: API keys & webhooks


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["read"])


@router.get("/developer/api-keys")
def list_keys(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": k.id,
            "name": k.name,
            "prefix": k.prefix,
            "scopes": k.scopes,
            "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
        }
        for k in db.scalars(select(ApiKey).order_by(ApiKey.id))
    ]


@router.post("/developer/api-keys", status_code=201, dependencies=[Depends(auth.workspace_editor)])
def create_key(req: KeyRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if any(s not in ("read",) for s in req.scopes):
        raise ConfigurationError("only the 'read' scope is supported")
    token, prefix, h = new_api_key()
    k = ApiKey(name=req.name, prefix=prefix, key_hash=h, scopes=req.scopes)
    db.add(k)
    db.commit()
    audit.record(db, "api_key.created", "api_key", k.id, {"name": req.name, "prefix": prefix})
    return {
        "id": k.id,
        "name": k.name,
        "prefix": prefix,
        "api_key": token,
        "note": "Store this key now — it is shown only once. Only a SHA-256 hash is kept.",
    }


@router.post("/developer/api-keys/{kid}/revoke", dependencies=[Depends(auth.workspace_editor)])
def revoke_key(kid: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    k = db.get(ApiKey, kid)
    if k is None:
        raise NotFoundError("API key not found")
    k.revoked_at = utcnow()
    db.commit()
    audit.record(db, "api_key.revoked", "api_key", k.id, {"prefix": k.prefix})
    return {"id": k.id, "revoked_at": k.revoked_at.isoformat()}


class WebhookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=500)
    events: list[str]
    description: str | None = Field(default=None, max_length=200)


@router.get("/developer/webhooks")
def list_webhooks(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"endpoints": webhooks.list_endpoints(db), "events": webhooks.EVENTS, "deliveries": webhooks.list_deliveries(db)}


@router.post("/developer/webhooks", status_code=201, dependencies=[Depends(auth.workspace_editor)])
def create_webhook(req: WebhookRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    ep, secret = webhooks.create_endpoint(db, req.url, req.events, req.description)
    audit.record(db, "webhook.created", "webhook", ep.id, {"url": req.url, "events": req.events})
    return {
        "id": ep.id,
        "url": ep.url,
        "events": ep.events,
        "signing_secret": secret,
        "note": "Store the signing secret now — it is shown only once. Verify X-Nexis-Signature with HMAC-SHA256.",
    }


@router.delete("/developer/webhooks/{wid}", status_code=204, dependencies=[Depends(auth.workspace_editor)])
def delete_webhook(wid: int, db: Session = Depends(get_db)) -> Response:
    webhooks.delete_endpoint(db, wid)
    audit.record(db, "webhook.deleted", "webhook", wid)
    return Response(status_code=204)


@router.post("/developer/webhooks/test", dependencies=[Depends(auth.workspace_editor)])
def test_webhook() -> dict[str, Any]:
    return {"queued": webhooks.emit("ping", {"message": "Test event from Nexis Finance"})}


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=500)


@router.get("/assistant/suggestions")
def assistant_suggestions() -> list[str]:
    return assistant.SUGGESTIONS


@router.post("/assistant/ask", dependencies=[Depends(auth.compute_limit)])
def assistant_ask(req: AskRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return assistant.ask(db, req.question)
