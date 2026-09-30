"""Connection lifecycle and the sync engine.

Status model: ``connected`` only after a real ``verify()`` call succeeds; ``syncing`` while a sync
runs; ``needs_attention`` when authorisation fails or credentials expired; ``disconnected`` after
the user disconnects (credentials are wiped); ``unavailable`` when the provider is disabled by
configuration. A sync is only reported successful if every step actually completed.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectivity.base import BrokerageProvider, EconomicDataProvider, FinancialConnectionProvider
from app.connectivity.catalog import PROVIDERS, catalog, get_provider_class
from app.connectivity.security import decrypt_json, encrypt_json, mask
from app.core.config import get_settings
from app.core.errors import ConfigurationError, NexisError, NotFoundError, ProviderError
from app.data.providers import PublicMarketDataProvider, SyntheticMarketDataProvider
from app.data.synthetic import SyntheticConfig
from app.db.base import utcnow
from app.models import (
    Account,
    Asset,
    CompanyProfile,
    Connection,
    Dataset,
    EconomicObservation,
    EconomicSeries,
    Fundamental,
    Holding,
    ImportBatch,
    SourceRecord,
    SyncRun,
    Transaction,
)
from app.services import audit, ingestion
from app.services.notifications import notify
from app.services.webhooks import safe_emit

LIVE_DATASET = "LIVE-MARKET"


def _provider_enabled(key: str) -> bool:
    s = get_settings()
    if key == "yahoo_market":
        return s.public_provider_enabled
    if key in ("us_treasury", "world_bank", "fred", "sec_edgar", "alpaca"):
        return s.external_data_enabled
    return True


def get_connection(db: Session, cid: int) -> Connection:
    c = db.get(Connection, cid)
    if c is None:
        raise NotFoundError(f"connection {cid} not found")
    return c


def instantiate(c: Connection, client: Any = None) -> FinancialConnectionProvider:
    cls = get_provider_class(c.provider_key)
    creds = decrypt_json(c.credentials_encrypted) if c.credentials_encrypted else {}
    return cls(credentials=creds, config=c.config or {}, client=client)


def marketplace(db: Session) -> list[dict[str, Any]]:
    conns = db.scalars(select(Connection)).all()
    out = []
    for spec in catalog():
        mine = [serialize(c) for c in conns if c.provider_key == spec["key"]]
        spec["connections"] = mine
        spec["enabled"] = spec["implemented"] and _provider_enabled(spec["key"])
        out.append(spec)
    return out


def connect(
    db: Session,
    provider_key: str,
    display_name: str | None,
    credentials: dict[str, Any] | None,
    config: dict[str, Any] | None,
    client: Any = None,
) -> Connection:
    cls = get_provider_class(provider_key)
    spec = cls.spec
    if spec.auth_type == "file":
        raise ConfigurationError("file sources are created by importing a file")
    if spec.credential_fields and get_settings().public_instance:
        raise ConfigurationError(
            "this is a shared public workspace, so it does not store API keys or account credentials — "
            "run your own instance (see the README) to connect "
            f"{spec.name}; keyless sources and file imports work here"
        )
    missing = [f.name for f in spec.credential_fields if f.required and not (credentials or {}).get(f.name)]
    if missing:
        raise ConfigurationError(f"missing credentials: {', '.join(missing)}")
    if credentials:
        unexpected = set(credentials) - {f.name for f in spec.credential_fields}
        if unexpected:
            raise ConfigurationError(f"unexpected credential fields: {sorted(unexpected)}")
    c = Connection(
        provider_key=provider_key,
        display_name=(display_name or spec.name)[:160],
        category=spec.category,
        auth_type=spec.auth_type,
        status="disconnected",
        config=config or {},
    )
    if credentials:
        c.credentials_encrypted = encrypt_json(credentials)
        secret_val = next((credentials[f.name] for f in spec.credential_fields if f.secret and credentials.get(f.name)), None)
        c.credential_hint = mask(secret_val)
    db.add(c)
    db.flush()
    if not _provider_enabled(provider_key):
        c.status, c.last_error = "unavailable", "provider disabled by server configuration"
        db.commit()
        return c
    try:
        info = instantiate(c, client).verify()
        c.status, c.last_error = "connected", None
        c.config = {**(c.config or {}), "verified": info}
    except ProviderError as exc:
        c.status = "needs_attention"
        c.last_error = exc.message
    db.commit()
    audit.record(
        db, "connection.connected", "connection", c.id, {"provider": provider_key, "status": c.status, "error": c.last_error}
    )
    return c


def update_config(db: Session, cid: int, config: dict[str, Any]) -> Connection:
    c = get_connection(db, cid)
    c.config = {**(c.config or {}), **config}
    db.commit()
    return c


def disconnect(db: Session, cid: int, client: Any = None) -> Connection:
    c = get_connection(db, cid)
    try:
        if c.provider_key in PROVIDERS and c.credentials_encrypted:
            instantiate(c, client).disconnect()
    except NexisError:
        pass
    c.credentials_encrypted, c.credential_hint, c.token_expires_at = None, None, None
    c.status = "disconnected"
    db.commit()
    audit.record(db, "connection.disconnected", "connection", c.id, {"provider": c.provider_key})
    return c


def delete(db: Session, cid: int) -> None:
    c = get_connection(db, cid)
    db.delete(c)
    db.commit()


def held_symbols(db: Session) -> list[str]:
    return sorted(
        {s for (s,) in db.execute(select(Holding.symbol).distinct()) if s and s != "CASH"}
        | {s for (s,) in db.execute(select(Transaction.symbol).distinct()) if s}
    )


def held_currencies(db: Session) -> list[str]:
    return sorted({c for (c,) in db.execute(select(Holding.currency).distinct()) if c and c != "USD"})


def check_syncable(db: Session, c: Connection) -> None:
    """Raise before any work is queued if the connection cannot be synced right now."""
    if c.status == "disconnected":
        raise ConfigurationError("connection is disconnected — reconnect before syncing")
    if c.provider_key == "file_import":
        raise ConfigurationError("file sources cannot be re-synced; import the updated file instead")
    if not _provider_enabled(c.provider_key):
        c.status = "unavailable"
        db.commit()
        raise ConfigurationError("provider disabled by server configuration")
    if c.token_expires_at and c.token_expires_at < utcnow():
        c.status = "needs_attention"
        db.commit()
        raise ConfigurationError("authorisation expired — reconnect this source")


def sync(db: Session, cid: int, client: Any = None) -> dict[str, Any]:
    c = get_connection(db, cid)
    check_syncable(db, c)
    run = SyncRun(connection_id=c.id, status="running")
    prev_status = c.status
    c.status = "syncing"
    db.add(run)
    db.commit()
    t0 = time.perf_counter()
    try:
        prov = instantiate(c, client)
        handler = {
            "markets": _sync_market,
            "economics": _sync_economic,
            "regulatory": _sync_regulatory,
            "accounts": _sync_brokerage,
        }[c.category]
        result = handler(db, c, prov)
        run.records_added, run.records_updated, run.records_removed = (
            result.get("added", 0),
            result.get("updated", 0),
            result.get("removed", 0),
        )
        run.warnings = result.get("warnings") or []
        run.details = {k: v for k, v in result.items() if k not in ("warnings",)} | {
            "duration_seconds": round(time.perf_counter() - t0, 2)
        }
        run.status = "warning" if run.warnings else "success"
        c.status, c.last_error, c.last_sync_status = "connected", None, run.status
    except NexisError as exc:
        db.rollback()
        run = db.get(SyncRun, run.id)
        c = get_connection(db, cid)
        run.status, run.errors = "failed", [exc.message]
        auth = isinstance(exc.details, dict) and exc.details.get("auth")
        c.status = "needs_attention" if auth else (prev_status if prev_status != "syncing" else "connected")
        c.last_error, c.last_sync_status = exc.message, "failed"
    except Exception as exc:
        db.rollback()
        run = db.get(SyncRun, run.id)
        c = get_connection(db, cid)
        run.status, run.errors = "failed", [f"internal error ({exc.__class__.__name__})"]
        c.status, c.last_error, c.last_sync_status = "needs_attention", run.errors[0], "failed"
    run.finished_at = utcnow()
    c.last_sync_at = run.finished_at
    db.commit()
    payload = {
        "connection_id": c.id,
        "provider": c.provider_key,
        "sync_run_id": run.id,
        "status": run.status,
        "added": run.records_added,
        "updated": run.records_updated,
        "removed": run.records_removed,
    }
    if run.status == "failed":
        audit.record(db, "connection.sync_failed", "connection", c.id, payload | {"error": (run.errors or [""])[0]})
        notify(db, "error", "sync", f"Sync failed: {c.display_name}", (run.errors or ["unknown error"])[0], link="/connections")
        safe_emit("data.sync.failed", payload)
    else:
        audit.record(db, "connection.synced", "connection", c.id, payload)
        if c.category == "markets":
            audit.record(db, "market_data.refreshed", "connection", c.id, payload)
        notify(
            db,
            "success" if run.status == "success" else "warning",
            "sync",
            f"Synced {c.display_name}",
            f"{run.records_added} added, {run.records_updated} updated, {run.records_removed} removed"
            + (f"; {len(run.warnings or [])} warning(s)" if run.warnings else "."),
            link="/connections",
        )
        safe_emit("data.sync.completed", payload)
        from app.services import book

        book.invalidate()
    db.commit()
    return serialize_run(run)


# ---------------------------------------------------------------- category handlers


def _sync_market(db: Session, c: Connection, prov: FinancialConnectionProvider) -> dict[str, Any]:
    if c.provider_key == "synthetic_market":
        cfg = SyntheticConfig()
        p = SyntheticMarketDataProvider(cfg)
        run = ingestion.ingest(
            db,
            p,
            "SYN-MULTI-DEMO",
            "Nexis Synthetic Multi-Sector Universe",
            None,
            None,
            None,
            "incremental",
            "Synthetic multi-sector universe (DEMO / SYNTHETIC DATA MODE).",
            {"generator": cfg.to_dict(), "manifest": p.manifest},
        )
        return {"added": run.records_inserted, "updated": 0, "removed": 0, "dataset_version": run.dataset_version}
    cfg = c.config or {}
    extra = [s.strip().upper() for s in str(cfg.get("symbols", "SPY")).split(",") if s.strip()]
    symbols = sorted(set(extra) | set(held_symbols(db)) | {f"{ccy}USD=X" for ccy in held_currencies(db)} | {"SPY"})
    start = pd.Timestamp(cfg.get("start") or "2019-01-01").date()
    provider = PublicMarketDataProvider(timeout=get_settings().public_provider_timeout_seconds, client=prov._client)
    warnings, added = [], 0
    version = None
    # Fetch symbol by symbol so one unknown ticker does not fail the whole sync.
    ok: list[str] = []
    for s in symbols:
        try:
            run = ingestion.ingest(
                db,
                provider,
                LIVE_DATASET,
                "Live market data (public provider)",
                [s],
                start,
                None,
                "incremental",
                "Daily bars from the Yahoo Finance public chart endpoint (unofficial, no SLA).",
                None,
                ["SPY"],
            )
            added += run.records_inserted
            version = run.dataset_version
            ok.append(s)
        except ProviderError as exc:
            warnings.append(f"{s}: {exc.message}")
    if not ok:
        raise ProviderError("market-data sync failed for every symbol", details={"warnings": warnings[:10]})
    return {
        "added": added,
        "updated": 0,
        "removed": 0,
        "symbols": ok,
        "dataset": LIVE_DATASET,
        "dataset_version": version,
        "warnings": warnings,
    }


def _sync_economic(db: Session, c: Connection, prov: FinancialConnectionProvider) -> dict[str, Any]:
    assert isinstance(prov, EconomicDataProvider)
    added = updated = 0
    warnings: list[str] = []
    fetched = []
    if c.provider_key == "us_treasury":
        existing = db.scalars(select(EconomicSeries).where(EconomicSeries.source == "us_treasury")).all()
        start = min((s.coverage_end for s in existing if s.coverage_end), default=None)
        fetched = prov.fetch_all(start - timedelta(days=7) if start else None)  # type: ignore[attr-defined]
    else:
        for code in prov.list_series():
            try:
                fetched.append(prov.get_series(code, None))
            except (ProviderError, ConfigurationError) as exc:
                warnings.append(f"{code}: {exc.message}")
    if not fetched:
        raise ProviderError("no series could be retrieved", details={"warnings": warnings})
    for sd in fetched:
        s = db.scalars(
            select(EconomicSeries).where(EconomicSeries.source == c.provider_key, EconomicSeries.series_code == sd.code)
        ).first()
        if s is None:
            s = EconomicSeries(source=c.provider_key, series_code=sd.code, title=sd.title, connection_id=c.id)
            db.add(s)
            db.flush()
        s.title, s.units, s.frequency, s.country, s.last_retrieved = sd.title, sd.units, sd.frequency, sd.country, utcnow()
        have = {
            d: (oid, v)
            for oid, d, v in db.execute(
                select(EconomicObservation.id, EconomicObservation.date, EconomicObservation.value).where(
                    EconomicObservation.series_id == s.id
                )
            )
        }
        for row in sd.observations.itertuples(index=False):
            d, v = row.date, (None if pd.isna(row.value) else float(row.value))
            if d in have:
                if have[d][1] != v:
                    db.get(EconomicObservation, have[d][0]).value = v
                    updated += 1
            else:
                db.add(EconomicObservation(series_id=s.id, date=d, value=v))
                added += 1
        db.flush()
        dates = [d for (d,) in db.execute(select(EconomicObservation.date).where(EconomicObservation.series_id == s.id))]
        s.coverage_start, s.coverage_end = (min(dates), max(dates)) if dates else (None, None)
    return {"added": added, "updated": updated, "removed": 0, "series": [s.code for s in fetched], "warnings": warnings}


def _sync_regulatory(db: Session, c: Connection, prov: FinancialConnectionProvider) -> dict[str, Any]:
    syms = [s for s in held_symbols(db) if not s.endswith("=X")]
    extra = [s.strip().upper() for s in str((c.config or {}).get("symbols", "")).split(",") if s.strip()]
    syms = sorted(set(syms) | set(extra))
    if not syms:
        raise ConfigurationError("no holdings to look up — import holdings first (or add symbols in the connection settings)")
    added = updated = 0
    warnings: list[str] = []
    for sym in syms:
        try:
            prof = prov.get_profile(sym)  # type: ignore[attr-defined]
        except ProviderError as exc:
            warnings.append(f"{sym}: {exc.message}")
            continue
        if prof is None:
            warnings.append(f"{sym}: not an SEC-registered operating company ticker (left unclassified)")
            continue
        p = db.scalars(select(CompanyProfile).where(CompanyProfile.symbol == sym)).first()
        if p is None:
            p = CompanyProfile(symbol=sym, cik=prof["cik"], name=prof["name"], connection_id=c.id)
            db.add(p)
            added += 1
        else:
            updated += 1
        for k in (
            "cik",
            "name",
            "sic",
            "sic_description",
            "sic_major_group",
            "sic_division",
            "business_state_or_country",
            "business_country",
            "state_of_incorporation",
            "exchanges",
            "fiscal_year_end",
        ):
            setattr(p, k, prof[k])
        p.fetched_at = utcnow()
        db.flush()
        if prof.get("sic_major_group"):
            # Live-market assets carry no sector from the price feed; use the filing's SIC major group.
            for a in db.scalars(
                select(Asset).join(Dataset).where(Dataset.code == LIVE_DATASET, Asset.symbol == sym, Asset.sector.is_(None))
            ):
                a.sector = prof["sic_major_group"][:64]
        if prof.get("sic"):
            try:
                facts = prov.get_fundamentals(prof["cik"])  # type: ignore[attr-defined]
            except ProviderError as exc:
                warnings.append(f"{sym} facts: {exc.message}")
                facts = []
            existing = {
                (f.concept, f.period_end, f.fp): f for f in db.scalars(select(Fundamental).where(Fundamental.profile_id == p.id))
            }
            for f in facts:
                k = (f["concept"], f["period_end"], f["fp"])
                if k in existing:
                    if existing[k].value != f["value"]:
                        existing[k].value = f["value"]
                        updated += 1
                else:
                    db.add(Fundamental(profile_id=p.id, **f))
                    added += 1
    return {"added": added, "updated": updated, "removed": 0, "symbols": syms, "warnings": warnings}


def _sync_brokerage(db: Session, c: Connection, prov: FinancialConnectionProvider) -> dict[str, Any]:
    assert isinstance(prov, BrokerageProvider)
    from app.services.imports import tx_hash

    added = removed = 0
    since = c.last_sync_at.date() - timedelta(days=3) if c.last_sync_at else None
    for acc in prov.get_accounts():
        key = f"{acc['institution'].lower()}:{acc['external_id'].lower()}"
        a = db.scalars(select(Account).where(Account.account_key == key)).first()
        if a is None:
            a = Account(
                account_key=key,
                institution=acc["institution"],
                external_id=acc["external_id"],
                name=acc["name"],
                account_type=acc.get("account_type"),
                base_currency=acc.get("base_currency", "USD"),
                first_connection_id=c.id,
            )
            db.add(a)
            db.flush()
        batch = ImportBatch(
            connection_id=c.id,
            source_label=c.display_name,
            source_type="api",
            data_class="real_external",
            record_kind="holdings",
            as_of=date.today(),
        )
        db.add(batch)
        db.flush()
        prev = db.execute(
            select(ImportBatch.id)
            .join(Holding, Holding.batch_id == ImportBatch.id)
            .where(Holding.account_id == a.id, ImportBatch.connection_id == c.id, ImportBatch.id != batch.id)
            .order_by(ImportBatch.id.desc())
            .limit(1)
        ).scalar()
        prev_syms = (
            set(db.scalars(select(Holding.symbol).where(Holding.batch_id == prev, Holding.account_id == a.id))) if prev else set()
        )
        rows = prov.get_holdings(acc["external_id"])
        for i, h in enumerate(rows):
            sr = SourceRecord(
                batch_id=batch.id,
                row_number=i + 1,
                raw={k: str(v) for k, v in (h.get("raw") or h).items()},
                status="imported",
                entity_type="holding",
            )
            db.add(sr)
            db.flush()
            hold = Holding(
                account_id=a.id,
                batch_id=batch.id,
                connection_id=c.id,
                symbol=h["symbol"],
                asset_class=h["asset_class"],
                quantity=h["quantity"],
                average_cost=h["average_cost"],
                price=h["price"],
                market_value=h["market_value"],
                currency=h["currency"],
                as_of=date.today(),
                source_record_id=sr.id,
            )
            db.add(hold)
            db.flush()
            sr.entity_id = hold.id
        batch.rows_total = batch.rows_imported = len(rows)
        removed += len(prev_syms - {h["symbol"] for h in rows})
        added += len({h["symbol"] for h in rows} - prev_syms)
        txs = prov.get_transactions(acc["external_id"], since)
        tbatch = ImportBatch(
            connection_id=c.id,
            source_label=c.display_name,
            source_type="api",
            data_class="real_external",
            record_kind="transactions",
            rows_total=len(txs),
        )
        db.add(tbatch)
        db.flush()
        existing = set(db.scalars(select(Transaction.dedupe_hash).where(Transaction.account_id == a.id)))
        from sqlalchemy import func as _f

        next_code = (db.scalar(select(_f.max(Transaction.id))) or 0) + 1
        occ: dict[tuple, int] = {}
        for i, t in enumerate(txs):
            k = (t["date"], t["symbol"], t["type"], t["quantity"], t["price"], t["amount"])
            occ[k] = occ.get(k, 0) + 1
            hsh = tx_hash(a.account_key, t["date"], t["symbol"], t["type"], t["quantity"], t["price"], t["amount"], occ[k])
            sr = SourceRecord(
                batch_id=tbatch.id,
                row_number=i + 1,
                raw={k2: str(v) for k2, v in (t.get("raw") or {}).items()},
                status="duplicate" if hsh in existing else "imported",
                entity_type="transaction",
            )
            db.add(sr)
            db.flush()
            if hsh in existing:
                tbatch.rows_duplicate += 1
                continue
            tx = Transaction(
                code=f"TX-{next_code:06d}",
                account_id=a.id,
                batch_id=tbatch.id,
                connection_id=c.id,
                trade_date=t["date"],
                symbol=t["symbol"],
                tx_type=t["type"],
                quantity=t["quantity"],
                price=t["price"],
                fees=t["fees"],
                amount=t["amount"],
                currency="USD",
                dedupe_hash=hsh,
                source_record_id=sr.id,
            )
            next_code += 1
            db.add(tx)
            db.flush()
            sr.entity_id = tx.id
            existing.add(hsh)
            tbatch.rows_imported += 1
            added += 1
    return {"added": added, "updated": 0, "removed": removed}


# ---------------------------------------------------------------- serialisation


def serialize(c: Connection) -> dict[str, Any]:
    spec = PROVIDERS[c.provider_key].spec if c.provider_key in PROVIDERS else None
    return {
        "id": c.id,
        "provider_key": c.provider_key,
        "display_name": c.display_name,
        "category": c.category,
        "status": c.status,
        "auth_type": c.auth_type,
        "authorized": bool(c.credentials_encrypted) or c.auth_type in ("none", "file"),
        "credential_hint": c.credential_hint,
        "token_expires_at": c.token_expires_at.isoformat() if c.token_expires_at else None,
        "config": {k: v for k, v in (c.config or {}).items() if k != "verified"},
        "verification": (c.config or {}).get("verified"),
        "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        "last_sync_status": c.last_sync_status,
        "last_error": c.last_error,
        "capabilities": list(spec.capabilities) if spec else [],
        "data_class": spec.data_class if spec else None,
        "can_sync": c.provider_key != "file_import",
        "created_at": c.created_at.isoformat(),
    }


def serialize_run(r: SyncRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "connection_id": r.connection_id,
        "status": r.status,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "records_added": r.records_added,
        "records_updated": r.records_updated,
        "records_removed": r.records_removed,
        "warnings": r.warnings or [],
        "errors": r.errors or [],
        "details": r.details,
    }


def list_runs(db: Session, cid: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    q = select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
    if cid:
        q = q.where(SyncRun.connection_id == cid)
    return [serialize_run(r) for r in db.scalars(q)]
