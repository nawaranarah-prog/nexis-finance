"""Universal financial-data import: preview → (auto or manual) column mapping → normalise → persist.

Every source row is stored verbatim in ``source_records`` with its outcome (imported / rejected /
duplicate) and a link to the canonical entity it produced, which is what the lineage view shows.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.connectivity import normalize as N
from app.core.errors import ConfigurationError, ConflictError
from app.data.providers import AssetInfo, MarketDataProvider, normalize_columns
from app.models import Account, Connection, Holding, ImportBatch, SourceRecord, Transaction
from app.services import audit, ingestion
from app.services.notifications import notify
from app.services.webhooks import safe_emit

MAX_ROWS = 200_000


def preview(content: bytes, file_name: str, kind: str | None = None) -> dict[str, Any]:
    df, fmt = N.read_table(content, file_name)
    if df.empty:
        raise ConfigurationError("the file contains no data rows")
    prop = N.propose_mapping(list(df.columns), kind)
    return {
        "file_name": file_name,
        "file_format": fmt,
        "row_count": len(df),
        "columns": list(df.columns),
        "sample_rows": df.head(12).to_dict(orient="records"),
        "proposal": {
            "kind": prop.kind,
            "mapping": prop.mapping,
            "confidence": prop.confidence,
            "uncertain": prop.uncertain,
            "missing_required": prop.missing_required,
            "kind_scores": prop.kind_scores,
        },
        "fields": {k: list(v) for k, v in N.FIELDS.items()},
        "required": N.REQUIRED,
        "needs_review": bool(prop.uncertain or prop.missing_required),
    }


def _file_connection(db: Session, label: str) -> Connection:
    c = db.scalars(select(Connection).where(Connection.provider_key == "file_import", Connection.display_name == label)).first()
    if c is None:
        c = Connection(
            provider_key="file_import",
            display_name=label,
            category="files",
            status="connected",
            auth_type="file",
            config={"source_label": label},
        )
        db.add(c)
        db.flush()
    return c


def _account(db: Session, institution: str, external_id: str, name: str | None, currency: str, connection_id: int) -> Account:
    key = f"{institution.strip().lower()}:{external_id.strip().lower()}"
    a = db.scalars(select(Account).where(Account.account_key == key)).first()
    if a is None:
        a = Account(
            account_key=key,
            institution=institution.strip(),
            external_id=external_id.strip(),
            name=name or f"{institution} {external_id}",
            base_currency=currency,
            first_connection_id=connection_id,
        )
        db.add(a)
        db.flush()
    return a


def _next_tx_code(db: Session) -> int:
    last = db.scalar(select(func.max(Transaction.id))) or 0
    return int(last) + 1


def import_file(
    db: Session,
    content: bytes,
    file_name: str,
    source_label: str,
    kind: str,
    mapping: dict[str, str | None],
    options: dict[str, Any] | None = None,
    is_sample: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    options = dict(options or {})
    if kind not in N.KINDS:
        raise ConfigurationError(f"kind must be one of {N.KINDS}")
    df, fmt = N.read_table(content, file_name)
    if len(df) > MAX_ROWS:
        raise ConfigurationError(f"file has {len(df):,} rows; the limit is {MAX_ROWS:,}")
    unknown = {v for v in mapping.values() if v and v not in df.columns}
    if unknown:
        raise ConfigurationError("mapping references columns not in the file", details={"columns": sorted(unknown)})
    missing = [f for f in N.REQUIRED[kind] if not mapping.get(f)]
    if missing:
        raise ConfigurationError(f"required fields not mapped: {', '.join(missing)}")
    sha = hashlib.sha256(content).hexdigest()
    prior = db.scalars(select(ImportBatch).where(ImportBatch.file_sha256 == sha, ImportBatch.record_kind == kind)).first()
    if prior and not force:
        raise ConflictError(
            f"this exact file was already imported as batch #{prior.id} ({prior.source_label})", details={"batch_id": prior.id}
        )

    conn = _file_connection(db, source_label)
    prop = N.propose_mapping(list(df.columns), kind)
    batch = ImportBatch(
        connection_id=conn.id,
        source_label=source_label,
        source_type="file",
        data_class="user_imported",
        is_sample=is_sample,
        file_name=file_name,
        file_sha256=sha,
        file_format=fmt,
        record_kind=kind,
        mapping=mapping,
        mapping_confidence={f: prop.confidence.get(f) for f in mapping},
        options=options,
        rows_total=len(df),
        as_of=N.parse_date(options.get("as_of")) if options.get("as_of") else date.today(),
    )
    db.add(batch)
    db.flush()

    handler = {"holdings": _import_holdings, "transactions": _import_transactions, "market_data": _import_market_data}[kind]
    issues = handler(db, df, batch, conn, mapping, options)
    batch.issues = issues[:500]
    conn.last_sync_at, conn.last_sync_status, conn.status = batch.created_at, "success", "connected"
    db.commit()

    audit.record(
        db,
        "file.imported",
        "import_batch",
        batch.id,
        {
            "file": file_name,
            "kind": kind,
            "rows": batch.rows_total,
            "imported": batch.rows_imported,
            "rejected": batch.rows_rejected,
            "source": source_label,
            "sample": is_sample,
        },
    )
    level = "warning" if batch.rows_rejected else "success"
    notify(
        db,
        level,
        "import",
        f"Imported {batch.rows_imported:,} {kind.replace('_', ' ')} rows from {file_name}",
        f"{batch.rows_rejected} rejected, {batch.rows_duplicate} duplicates. Source: {source_label}.",
        link="/lineage",
    )
    db.commit()
    if kind in ("holdings", "transactions"):
        from app.services import book

        book.invalidate()
        safe_emit("portfolio.updated", {"reason": "file_import", "batch_id": batch.id, "kind": kind})
    return serialize_batch(batch)


def _val(row: pd.Series, mapping: dict[str, str | None], fld: str) -> Any:
    col = mapping.get(fld)
    return row[col] if col and col in row.index else None


def _record(
    db: Session,
    batch: ImportBatch,
    i: int,
    row: pd.Series,
    status: str,
    message: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> SourceRecord:
    sr = SourceRecord(
        batch_id=batch.id,
        row_number=i + 2,
        raw={k: str(v) for k, v in row.items()},
        status=status,
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    db.add(sr)
    return sr


def _resolve_account(
    db: Session,
    row: pd.Series,
    mapping: dict[str, str | None],
    options: dict[str, Any],
    currency: str,
    conn: Connection,
    cache: dict[str, Account],
) -> Account | None:
    ext = str(_val(row, mapping, "account") or options.get("account") or "").strip()
    if not ext:
        return None
    inst = str(options.get("institution") or conn.display_name)
    if ext not in cache:
        cache[ext] = _account(
            db, inst, ext, options.get("account_name") if not mapping.get("account") else None, currency, conn.id
        )
    return cache[ext]


def _import_holdings(
    db: Session, df: pd.DataFrame, batch: ImportBatch, conn: Connection, mapping: dict[str, str | None], options: dict[str, Any]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    accounts: dict[str, Account] = {}
    seen: set[tuple[int, str]] = set()
    default_ccy = str(options.get("default_currency") or "USD").upper()
    for i, row in df.iterrows():

        def reject(msg: str, i: Any = i, row: pd.Series = row) -> None:
            _record(db, batch, int(i), row, "rejected", msg)
            issues.append({"row": int(i) + 2, "severity": "error", "message": msg})
            batch.rows_rejected += 1

        raw_sym = _val(row, mapping, "symbol")
        desc = str(_val(row, mapping, "description") or "")
        sym = N.normalize_symbol(raw_sym)
        mv = N.parse_number(_val(row, mapping, "market_value"))
        qty = N.parse_number(_val(row, mapping, "quantity"))
        cls = N.infer_asset_class(sym, str(_val(row, mapping, "asset_class") or "") or desc)
        if sym and sym.lower().startswith(("total", "account total", "grand total")):
            reject("summary/total row skipped")
            continue
        if cls == "cash" or (sym is None and "cash" in desc.lower()):
            sym, cls = "CASH", "cash"
            qty = qty if qty is not None and mv is None else mv if mv is not None else qty
        if not sym:
            reject("missing symbol")
            continue
        if qty is None:
            reject("quantity missing or not numeric")
            continue
        if qty < 0:
            reject("negative quantity (short positions are not supported)")
            continue
        price = 1.0 if cls == "cash" else N.parse_number(_val(row, mapping, "price"))
        avg = 1.0 if cls == "cash" else N.parse_number(_val(row, mapping, "average_cost"))
        if (price is not None and price < 0) or (avg is not None and avg < 0):
            reject("negative price or cost")
            continue
        ccy, defaulted = N.normalize_currency(_val(row, mapping, "currency"), default_ccy)
        if defaulted and mapping.get("currency"):
            issues.append({"row": int(i) + 2, "severity": "warning", "message": f"currency missing/invalid; assumed {ccy}"})
        acct = _resolve_account(db, row, mapping, options, ccy, conn, accounts)
        if acct is None:
            reject("no account column mapped and no default account given")
            continue
        if (acct.id, sym) in seen:
            _record(db, batch, int(i), row, "duplicate", f"{sym} already listed for this account in this file")
            batch.rows_duplicate += 1
            continue
        seen.add((acct.id, sym))
        sr = _record(db, batch, int(i), row, "imported", entity_type="holding")
        db.flush()
        h = Holding(
            account_id=acct.id,
            batch_id=batch.id,
            connection_id=conn.id,
            symbol=sym,
            description=desc[:200] or None,
            asset_class=cls,
            quantity=float(qty),
            average_cost=avg,
            price=price,
            market_value=mv if mv is not None else (qty * price if price is not None else None),
            currency=ccy,
            as_of=batch.as_of,
            source_record_id=sr.id,
        )
        db.add(h)
        db.flush()
        sr.entity_id = h.id
        batch.rows_imported += 1
    return issues


def tx_hash(
    account_key: str,
    d: date,
    sym: str | None,
    typ: str,
    qty: float | None,
    price: float | None,
    amount: float | None,
    occurrence: int,
) -> str:
    parts = [
        account_key,
        d.isoformat(),
        sym or "",
        typ,
        f"{qty or 0:.6f}",
        f"{price or 0:.6f}",
        f"{amount or 0:.4f}",
        str(occurrence),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _import_transactions(
    db: Session, df: pd.DataFrame, batch: ImportBatch, conn: Connection, mapping: dict[str, str | None], options: dict[str, Any]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    accounts: dict[str, Account] = {}
    dayfirst = bool(options.get("dayfirst", False))
    default_ccy = str(options.get("default_currency") or "USD").upper()
    occurrences: Counter[tuple] = Counter()
    next_code = _next_tx_code(db)
    existing_hashes: dict[int, set[str]] = {}
    for i, row in df.iterrows():

        def reject(msg: str, i: Any = i, row: pd.Series = row) -> None:
            _record(db, batch, int(i), row, "rejected", msg)
            issues.append({"row": int(i) + 2, "severity": "error", "message": msg})
            batch.rows_rejected += 1

        d = N.parse_date(_val(row, mapping, "date"), dayfirst)
        raw_type = _val(row, mapping, "type")
        typ = N.normalize_tx_type(raw_type)
        if d is None:
            reject(f"invalid date '{_val(row, mapping, 'date')}'")
            continue
        if d > date.today():
            reject("date is in the future")
            continue
        if typ is None:
            reject(f"unrecognised transaction type '{raw_type}'")
            continue
        sym = N.normalize_symbol(_val(row, mapping, "symbol"))
        qty = N.parse_number(_val(row, mapping, "quantity"))
        qty = abs(qty) if qty is not None else None
        price = N.parse_number(_val(row, mapping, "price"))
        fees = abs(N.parse_number(_val(row, mapping, "fees")) or 0.0)
        amount = N.parse_number(_val(row, mapping, "amount"))
        if typ in ("buy", "sell"):
            if not sym:
                reject(f"{typ} without a symbol")
                continue
            if not qty:
                reject(f"{typ} without a positive quantity")
                continue
            if price is None and amount is not None:
                price = max(abs(amount) - (fees if typ == "buy" else -fees), 0.0) / qty
                issues.append({"row": int(i) + 2, "severity": "info", "message": "price derived from amount, fees and quantity"})
            if price is None or price < 0:
                reject(f"{typ} without a valid price")
                continue
        if typ == "split" and not qty:
            reject("split without a ratio in the quantity column")
            continue
        ccy, _ = N.normalize_currency(_val(row, mapping, "currency"), default_ccy)
        acct = _resolve_account(db, row, mapping, options, ccy, conn, accounts)
        if acct is None:
            reject("no account column mapped and no default account given")
            continue
        base_key = (acct.account_key, d, sym, typ, qty, price, amount)
        occurrences[base_key] += 1
        h = tx_hash(acct.account_key, d, sym, typ, qty, price, amount, occurrences[base_key])
        if acct.id not in existing_hashes:
            existing_hashes[acct.id] = set(db.scalars(select(Transaction.dedupe_hash).where(Transaction.account_id == acct.id)))
        if h in existing_hashes[acct.id]:
            _record(db, batch, int(i), row, "duplicate", "identical transaction already imported for this account")
            batch.rows_duplicate += 1
            continue
        existing_hashes[acct.id].add(h)
        sr = _record(db, batch, int(i), row, "imported", entity_type="transaction")
        db.flush()
        t = Transaction(
            code=f"TX-{next_code:06d}",
            account_id=acct.id,
            batch_id=batch.id,
            connection_id=conn.id,
            trade_date=d,
            symbol=sym,
            tx_type=typ,
            quantity=qty,
            price=price,
            fees=fees,
            amount=amount,
            currency=ccy,
            description=str(_val(row, mapping, "description") or "")[:300] or None,
            dedupe_hash=h,
            source_record_id=sr.id,
        )
        next_code += 1
        db.add(t)
        db.flush()
        sr.entity_id = t.id
        batch.rows_imported += 1
    return issues


class _FrameProvider(MarketDataProvider):
    name = "csv_upload"

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df

    def list_assets(self, symbols: list[str] | None = None) -> list[AssetInfo]:
        return [
            AssetInfo(symbol=s, name=s, attributes={"source": "file import"}) for s in sorted(self.df["symbol"].dropna().unique())
        ]

    def fetch(self, symbols: list[str], start: date | None, end: date | None) -> pd.DataFrame:
        return self.df[self.df["symbol"].isin(symbols)]


def _import_market_data(
    db: Session, df: pd.DataFrame, batch: ImportBatch, conn: Connection, mapping: dict[str, str | None], options: dict[str, Any]
) -> list[dict[str, Any]]:
    canon = pd.DataFrame({f: df[c] if c else None for f, c in mapping.items()})
    canon = normalize_columns(canon)
    canon["symbol"] = canon["symbol"].map(N.normalize_symbol)
    code = str(options.get("dataset_code") or f"USER-{conn.id}-MARKET").upper()[:64]
    run = ingestion.ingest(
        db,
        _FrameProvider(canon),
        code,
        str(options.get("dataset_name") or f"{batch.source_label} market data"),
        None,
        None,
        None,
        "incremental",
        f"Imported from {batch.file_name}",
        None,
        [options["benchmark_symbol"]] if options.get("benchmark_symbol") else None,
    )
    for i, row in df.iterrows():
        _record(db, batch, int(i), row, "imported", entity_type="market_data")
    batch.rows_imported = run.records_inserted
    batch.rows_rejected = run.records_rejected
    batch.rows_duplicate = run.duplicates + run.records_skipped_existing
    batch.options = {**options, "dataset_code": code, "ingestion_run_id": run.id}
    return [{"severity": "info", "message": w} for w in (run.warnings or [])]


def serialize_batch(b: ImportBatch) -> dict[str, Any]:
    return {
        "id": b.id,
        "connection_id": b.connection_id,
        "source_label": b.source_label,
        "source_type": b.source_type,
        "data_class": b.data_class,
        "is_sample": b.is_sample,
        "file_name": b.file_name,
        "file_sha256": b.file_sha256,
        "file_format": b.file_format,
        "record_kind": b.record_kind,
        "mapping": b.mapping,
        "mapping_confidence": b.mapping_confidence,
        "options": b.options,
        "as_of": b.as_of.isoformat() if b.as_of else None,
        "rows_total": b.rows_total,
        "rows_imported": b.rows_imported,
        "rows_rejected": b.rows_rejected,
        "rows_duplicate": b.rows_duplicate,
        "issues": b.issues or [],
        "created_at": b.created_at.isoformat(),
    }
