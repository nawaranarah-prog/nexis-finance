"""Transaction analytics, cross-source reconciliation, the Financial Intelligence Graph and data lineage."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectivity.cost_basis import METHODS, quantity_path
from app.connectivity.cost_basis import compute as cost_basis
from app.core.errors import ConfigurationError, InsufficientDataError, NotFoundError
from app.models import (
    Account,
    Asset,
    Connection,
    Dataset,
    Experiment,
    Holding,
    ImportBatch,
    IngestionRun,
    SourceRecord,
    SyncRun,
    Transaction,
)
from app.services import book as B

# ---------------------------------------------------------------- transactions


def transaction_rows(
    db: Session,
    account_id: int | None = None,
    symbol: str | None = None,
    tx_type: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    q = (
        select(Transaction, Account, ImportBatch)
        .join(Account, Account.id == Transaction.account_id)
        .join(ImportBatch, ImportBatch.id == Transaction.batch_id)
    )
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    if symbol:
        q = q.where(Transaction.symbol == symbol.upper())
    if tx_type:
        q = q.where(Transaction.tx_type == tx_type)
    if start:
        q = q.where(Transaction.trade_date >= start)
    if end:
        q = q.where(Transaction.trade_date <= end)
    return [
        {
            "id": t.id,
            "code": t.code,
            "account_id": a.id,
            "account": a.name,
            "date": t.trade_date.isoformat(),
            "symbol": t.symbol,
            "type": t.tx_type,
            "quantity": t.quantity,
            "price": t.price,
            "fees": t.fees,
            "amount": t.amount,
            "currency": t.currency,
            "notional": (t.quantity or 0) * (t.price or 0) if t.tx_type in ("buy", "sell") else None,
            "source": b.source_label,
            "batch_id": b.id,
            "source_record_id": t.source_record_id,
            "description": t.description,
        }
        for t, a, b in db.execute(q.order_by(Transaction.trade_date, Transaction.id))
    ]


def transaction_analytics(
    db: Session,
    method: str = "fifo",
    account_id: int | None = None,
    symbol: str | None = None,
    tx_type: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    if method not in METHODS:
        raise ConfigurationError(f"cost-basis method must be one of {METHODS}")
    rows = transaction_rows(db, account_id, symbol, tx_type, start, end)
    trades = [r for r in rows if r["type"] in ("buy", "sell")]
    notional = sum(r["notional"] or 0 for r in trades)
    months = max(1, len({r["date"][:7] for r in rows}))
    by_month: dict[str, dict[str, float]] = defaultdict(lambda: {"buys": 0.0, "sells": 0.0, "fees": 0.0, "count": 0})
    for r in rows:
        m = by_month[r["date"][:7]]
        if r["type"] == "buy":
            m["buys"] += r["notional"] or 0
        elif r["type"] == "sell":
            m["sells"] += r["notional"] or 0
        m["fees"] += r["fees"] or 0
        m["count"] += 1
    # Realised/unrealised P&L always uses the full history of each account (never a filtered subset),
    # otherwise sells would be matched against missing buys.
    accts = [db.get(Account, account_id)] if account_id else list(db.scalars(select(Account)))
    pnl_rows, issues = [], []
    totals = {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "income": 0.0, "fees": 0.0}
    for a in accts:
        if a is None:
            raise NotFoundError("account not found")
        tx = [
            {
                "code": t.code,
                "trade_date": t.trade_date,
                "symbol": t.symbol,
                "tx_type": t.tx_type,
                "quantity": t.quantity,
                "price": t.price,
                "fees": t.fees,
                "amount": t.amount,
            }
            for t in db.scalars(select(Transaction).where(Transaction.account_id == a.id))
        ]
        if not tx:
            continue
        syms = sorted({t["symbol"] for t in tx if t["symbol"]})
        prices = {s: p.price for s, p in B.latest_prices(db, syms).items()}
        cb = cost_basis(tx, method, prices)  # type: ignore[arg-type]
        for p in cb["positions"]:
            if symbol and p["symbol"] != symbol.upper():
                continue
            pnl_rows.append({"account": a.name, **{k: v for k, v in p.items() if k != "open_lots"}, "open_lots": p["open_lots"]})
        for k in totals:
            totals[k] += (
                sum((p.get(k) or 0.0) for p in cb["positions"] if not symbol or p["symbol"] == symbol.upper())
                if k != "fees"
                else cb["totals"]["fees"]
            )
        issues += [{"account": a.name, **i} for i in cb["issues"]]
    first = min((r["date"] for r in rows), default=None)
    last = max((r["date"] for r in rows), default=None)
    return {
        "cost_basis_method": method,
        "filters": {
            "account_id": account_id,
            "symbol": symbol,
            "type": tx_type,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
        "counts": {
            "transactions": len(rows),
            "trades": len(trades),
            "buys": sum(1 for r in trades if r["type"] == "buy"),
            "sells": sum(1 for r in trades if r["type"] == "sell"),
            "dividends": sum(1 for r in rows if r["type"] == "dividend"),
            "deposits": sum(1 for r in rows if r["type"] == "deposit"),
            "withdrawals": sum(1 for r in rows if r["type"] == "withdrawal"),
        },
        "fees_total": sum(r["fees"] or 0 for r in rows),
        "traded_notional": notional,
        "trades_per_month": len(trades) / months,
        "period": [first, last],
        "monthly": [{"month": k, **v} for k, v in sorted(by_month.items())],
        "pnl": {"positions": pnl_rows, "totals": totals, "issues": issues},
        "methodology": {
            "fifo": "FIFO: sells consume the oldest open lots first. Realised P&L = sale proceeds − fees − cost of the consumed lots.",
            "average": "Average cost: all open shares share one running average cost (fees capitalised); sells remove shares at that average.",
            "common": "Buy fees are capitalised into cost. Dividends/interest are income, reported separately. Unrealised P&L uses the latest stored market price. Oversells are flagged and ignored.",
        },
        "rows": rows,
    }


# ---------------------------------------------------------------- reconciliation


def reconciliation(db: Session, tolerance: float = 1e-6) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for a in db.scalars(select(Account)):
        # 1) Latest snapshot per source (connection) for this account: compare quantities across sources.
        by_batch: dict[int, tuple[ImportBatch, dict[str, Holding]]] = {}
        for h, b in db.execute(
            select(Holding, ImportBatch).join(ImportBatch, ImportBatch.id == Holding.batch_id).where(Holding.account_id == a.id)
        ):
            by_batch.setdefault(b.id, (b, {}))[1][h.symbol] = h
        latest_per_source: dict[int, tuple[ImportBatch, dict[str, Holding]]] = {}
        for b, hs in by_batch.values():
            src = b.connection_id if b.connection_id is not None else -b.id
            cur = latest_per_source.get(src)
            if cur is None or (b.as_of or date.min, b.id) > (cur[0].as_of or date.min, cur[0].id):
                latest_per_source[src] = (b, hs)
        srcs = list(latest_per_source.values())
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                (b1, h1), (b2, h2) = srcs[i], srcs[j]
                for sym in sorted(set(h1) | set(h2)):
                    q1 = h1[sym].quantity if sym in h1 else 0.0
                    q2 = h2[sym].quantity if sym in h2 else 0.0
                    if abs(q1 - q2) > tolerance:
                        issues.append(
                            {
                                "type": "holdings_mismatch",
                                "account_id": a.id,
                                "account": a.name,
                                "symbol": sym,
                                "source_a": b1.source_label,
                                "quantity_a": q1,
                                "as_of_a": b1.as_of.isoformat() if b1.as_of else None,
                                "source_b": b2.source_label,
                                "quantity_b": q2,
                                "as_of_b": b2.as_of.isoformat() if b2.as_of else None,
                                "difference": q2 - q1,
                                "records": [x for x in (h1.get(sym), h2.get(sym)) if x is not None and x.source_record_id],
                            }
                        )
        # 2) Snapshot vs transaction-implied quantity (as of the snapshot date).
        tx = [
            {"code": t.code, "trade_date": t.trade_date, "symbol": t.symbol, "tx_type": t.tx_type, "quantity": t.quantity}
            for t in db.scalars(select(Transaction).where(Transaction.account_id == a.id))
        ]
        if tx and srcs:
            b, hs = max(srcs, key=lambda s: (s[0].as_of or date.min, s[0].id))
            implied = {s: path for s, path in quantity_path([t for t in tx if not b.as_of or t["trade_date"] <= b.as_of]).items()}
            for sym in sorted(set(hs) | set(implied)):
                if sym == "CASH":
                    continue
                snap_q = hs[sym].quantity if sym in hs else 0.0
                tx_q = implied[sym][-1][1] if sym in implied else 0.0
                if abs(snap_q - tx_q) > tolerance:
                    issues.append(
                        {
                            "type": "snapshot_vs_transactions",
                            "account_id": a.id,
                            "account": a.name,
                            "symbol": sym,
                            "source_a": b.source_label,
                            "quantity_a": snap_q,
                            "as_of_a": b.as_of.isoformat() if b.as_of else None,
                            "source_b": "transaction history",
                            "quantity_b": tx_q,
                            "as_of_b": b.as_of.isoformat() if b.as_of else None,
                            "difference": tx_q - snap_q,
                            "records": [hs[sym]] if sym in hs and hs[sym].source_record_id else [],
                        }
                    )
    # 3) Near-duplicate transactions arriving from different sources.
    seen: dict[tuple, Transaction] = {}
    for t in db.scalars(select(Transaction).order_by(Transaction.id)):
        k = (t.account_id, t.trade_date, t.symbol, t.tx_type, round(t.quantity or 0, 6), round(t.price or 0, 4))
        if k in seen and seen[k].batch_id != t.batch_id and seen[k].connection_id != t.connection_id:
            o = seen[k]
            issues.append(
                {
                    "type": "possible_duplicate_transaction",
                    "account_id": t.account_id,
                    "account": db.get(Account, t.account_id).name,
                    "symbol": t.symbol,
                    "source_a": o.code,
                    "quantity_a": o.quantity,
                    "as_of_a": o.trade_date.isoformat(),
                    "source_b": t.code,
                    "quantity_b": t.quantity,
                    "as_of_b": t.trade_date.isoformat(),
                    "difference": 0.0,
                    "records": [],
                }
            )
        else:
            seen.setdefault(k, t)
    for i in issues:
        i["source_record_ids"] = [h.source_record_id for h in i.pop("records")]
    return {
        "issues": issues,
        "count": len(issues),
        "policy": "Potential reconciliation issues are reported for review only; Nexis never overwrites imported records automatically.",
    }


# ---------------------------------------------------------------- intelligence graph


def intelligence_graph(db: Session, scope: str = "all", method: str = "fifo") -> dict[str, Any]:
    """Nodes and edges derived from stored relationships and computed analytics (no decorative nodes)."""
    a = B.analytics(db, scope, method)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(nid: str, kind: str, label: str, **attrs: Any) -> str:
        nodes.setdefault(nid, {"id": nid, "type": kind, "label": label, **attrs})
        return nid

    port = node(
        "portfolio:" + scope,
        "portfolio",
        "Consolidated portfolio" if scope == "all" else f"Account portfolio {scope}",
        value=a["totals"]["market_value"],
    )
    for acct in a["accounts"]:
        aid = node(f"account:{acct['id']}", "account", acct["name"], value=acct["market_value"], source=acct["position_source"])
        edges.append({"source": aid, "target": port, "type": "contributes_to", "weight": acct["market_value"]})
    dec = {x["symbol"]: x for x in ((a.get("risk") or {}).get("decomposition") or {}).get("assets", [])}
    risk_node = node(
        "risk:volatility",
        "risk",
        "Portfolio volatility",
        value=((a.get("risk") or {}).get("decomposition") or {}).get("portfolio_volatility"),
    )
    bsym = (a.get("benchmark") or {}).get("symbol")
    bench = node(f"benchmark:{bsym}", "benchmark", bsym, beta=(a.get("metrics") or {}).get("beta")) if bsym else None
    if bench:
        edges.append(
            {"source": port, "target": bench, "type": "measured_against", "weight": (a.get("metrics") or {}).get("beta")}
        )
    # Market-derived factor tags relative to the holdings themselves (momentum and volatility terciles).
    tags = _factor_tags(db, [p for p in a["positions"] if p["symbol"] != "CASH" and p["market_value"]])
    for p in a["positions"]:
        if not p["market_value"]:
            continue
        sid = node(
            f"asset:{p['symbol']}",
            "asset",
            p["symbol"],
            name=p["name"],
            weight=p["weight"],
            value=p["market_value"],
            data_class=p["data_class"],
        )
        edges.append({"source": port, "target": sid, "type": "holds", "weight": p["weight"]})
        for acc in p["accounts"]:
            edges.append({"source": f"account:{acc['account_id']}", "target": sid, "type": "holds", "weight": acc["quantity"]})
        if p["symbol"] == "CASH":
            continue
        if p.get("industry"):
            iid = node(f"industry:{p['industry']}", "industry", p["industry"], source=p.get("industry_source"))
            edges.append({"source": sid, "target": iid, "type": "classified_as", "weight": p["weight"]})
        for t in tags.get(p["symbol"], []):
            fid = node(f"factor:{t['factor']}", "factor", t["factor"])
            edges.append({"source": sid, "target": fid, "type": "exposed_to", "weight": t["value"]})
        if p["symbol"] in dec:
            edges.append(
                {"source": sid, "target": risk_node, "type": "contributes_risk", "weight": dec[p["symbol"]]["pct_contribution"]}
            )
    if bench:
        edges.append({"source": risk_node, "target": bench, "type": "compared_with", "weight": None})
    from app.services.insights import _regime_for

    reg = _regime_for(db, a)
    if reg and bench:
        rid = node(f"regime:{reg['code']}", "regime", reg.get("regime") or "regime", date=reg.get("date"), model=reg["code"])
        edges.append({"source": bench, "target": rid, "type": "classified_in", "weight": None})
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "layers": ["account", "portfolio", "asset", "industry", "factor", "risk", "benchmark", "regime"],
        "note": "Generated from stored accounts, holdings, SEC classifications, price-derived factor terciles, risk decomposition, benchmark and regime model.",
    }


def _factor_tags(db: Session, positions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    syms = [p["symbol"] for p in positions]
    prices = B.latest_prices(db, syms)
    px = B.price_history(db, [s for s in syms if s in prices], prices)
    if px.shape[1] < 3 or len(px) < 260:
        return {}
    mom = (px.iloc[-22] / px.iloc[-253] - 1).dropna()
    vol = px.pct_change(fill_method=None).iloc[-63:].std().dropna() * math.sqrt(252)
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for series, hi, lo in (
        (mom, "High momentum (12-1m)", "Low momentum (12-1m)"),
        (vol, "High volatility (63d)", "Low volatility (63d)"),
    ):
        if len(series) < 3:
            continue
        q1, q2 = series.quantile(1 / 3), series.quantile(2 / 3)
        for s, v in series.items():
            if v >= q2:
                out[s].append({"factor": hi, "value": float(v)})
            elif v <= q1:
                out[s].append({"factor": lo, "value": float(v)})
    return dict(out)


def entity_detail(db: Session, kind: str, key: str, scope: str = "all", method: str = "fifo") -> dict[str, Any]:
    a = B.analytics(db, scope, method)
    if kind == "asset":
        sym = key.upper()
        p = next((x for x in a["positions"] if x["symbol"] == sym), None)
        if p is None:
            raise NotFoundError(f"{sym} is not held in this scope")
        dec = next((x for x in ((a.get("risk") or {}).get("decomposition") or {}).get("assets", []) if x["symbol"] == sym), None)
        corr = (a.get("risk") or {}).get("correlation")
        related = []
        if corr and sym in corr["symbols"]:
            i = corr["symbols"].index(sym)
            related = sorted(
                [
                    {"symbol": s, "correlation": corr["matrix"][i][j]}
                    for j, s in enumerate(corr["symbols"])
                    if s != sym and corr["matrix"][i][j] is not None
                ],
                key=lambda x: -abs(x["correlation"]),
            )[:6]
        beta = None
        pi = B.latest_prices(db, [sym]).get(sym)
        bsym = (a.get("benchmark") or {}).get("symbol")
        vol = None
        if pi and bsym:
            from app.analytics import metrics as M
            from app.services.market_data import load_panel

            panel = load_panel(db, pi.dataset_id)
            r = panel.adj_close[sym].pct_change(fill_method=None).iloc[-756:]
            vol = M._safe(lambda: M.annualized_volatility(r.dropna()))
            bp = B.latest_prices(db, [bsym]).get(bsym)
            if bp:
                br = load_panel(db, bp.dataset_id).adj_close[bsym].pct_change(fill_method=None)
                beta = M._safe(lambda: M.beta(r, br))
        tags = _factor_tags(db, [x for x in a["positions"] if x["symbol"] != "CASH" and x["market_value"]]).get(sym, [])
        return {
            "kind": "asset",
            "symbol": sym,
            "position": p,
            "risk_contribution": dec,
            "correlated_holdings": related,
            "annualized_volatility": vol,
            "benchmark": bsym,
            "beta_to_benchmark": beta,
            "factors": tags,
            "transactions": [t for t in transaction_rows(db, symbol=sym)][-50:],
        }
    if kind == "account":
        acct = next((x for x in a["accounts"] if str(x["id"]) == key), None)
        if acct is None:
            raise NotFoundError("account not in scope")
        held = [
            {"symbol": p["symbol"], "quantity": ac["quantity"], "weight_in_portfolio": p["weight"]}
            for p in a["positions"]
            for ac in p["accounts"]
            if str(ac["account_id"]) == key
        ]
        return {"kind": "account", "account": acct, "holdings": held}
    if kind == "industry":
        members = [p for p in a["positions"] if (p.get("industry") or "") == key]
        return {
            "kind": "industry",
            "industry": key,
            "weight": sum(p["weight"] or 0 for p in members),
            "holdings": [{"symbol": p["symbol"], "weight": p["weight"], "source": p["industry_source"]} for p in members],
        }
    raise ConfigurationError("entity kind must be asset, account or industry")


# ---------------------------------------------------------------- lineage


def lineage_overview(db: Session) -> dict[str, Any]:
    batches = db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc())).all()
    runs = db.scalars(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(50)).all()
    datasets = db.scalars(select(Dataset)).all()
    syncs = db.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(50)).all()
    conns = {c.id: c for c in db.scalars(select(Connection))}
    return {
        "import_batches": [
            {
                "id": b.id,
                "source": b.source_label,
                "file": b.file_name,
                "kind": b.record_kind,
                "data_class": b.data_class,
                "is_sample": b.is_sample,
                "imported": b.rows_imported,
                "rejected": b.rows_rejected,
                "duplicates": b.rows_duplicate,
                "sha256": b.file_sha256,
                "created_at": b.created_at.isoformat(),
                "mapping": b.mapping,
            }
            for b in batches
        ],
        "datasets": [
            {
                "id": d.id,
                "code": d.code,
                "provider": d.source,
                "synthetic": d.is_synthetic,
                "version": d.version_label,
                "coverage": [d.start_date.isoformat() if d.start_date else None, d.end_date.isoformat() if d.end_date else None],
                "records": d.record_count,
                "hash": d.content_hash,
                "updated_at": d.updated_at.isoformat(),
                "experiments_using": len(db.scalars(select(Experiment.id).where(Experiment.dataset_id == d.id)).all()),
                "transformations": [
                    "schema normalisation",
                    "date/number validation",
                    "OHLC consistency checks",
                    "symbol+date de-duplication",
                    "adjusted-close fallback to close",
                ],
            }
            for d in datasets
        ],
        "ingestion_runs": [
            {
                "id": r.id,
                "dataset_id": r.dataset_id,
                "provider": r.provider,
                "status": r.status,
                "started_at": r.started_at.isoformat(),
                "received": r.records_received,
                "inserted": r.records_inserted,
                "rejected": r.records_rejected,
                "version": r.dataset_version,
            }
            for r in runs
        ],
        "sync_runs": [
            {
                "id": s.id,
                "connection": conns[s.connection_id].display_name if s.connection_id in conns else s.connection_id,
                "status": s.status,
                "started_at": s.started_at.isoformat(),
                "added": s.records_added,
                "updated": s.records_updated,
                "removed": s.records_removed,
            }
            for s in syncs
        ],
    }


def batch_records(db: Session, batch_id: int, status: str | None = None, limit: int = 2000) -> dict[str, Any]:
    b = db.get(ImportBatch, batch_id)
    if b is None:
        raise NotFoundError(f"import batch {batch_id} not found")
    q = select(SourceRecord).where(SourceRecord.batch_id == batch_id)
    if status:
        q = q.where(SourceRecord.status == status)
    recs = db.scalars(q.order_by(SourceRecord.row_number).limit(limit)).all()
    tx_codes = {t.id: t.code for t in db.scalars(select(Transaction).where(Transaction.batch_id == batch_id))}
    return {
        "batch": {
            "id": b.id,
            "source": b.source_label,
            "file": b.file_name,
            "kind": b.record_kind,
            "mapping": b.mapping,
            "imported_at": b.created_at.isoformat(),
            "data_class": b.data_class,
            "is_sample": b.is_sample,
        },
        "records": [
            {
                "id": r.id,
                "row": r.row_number,
                "status": r.status,
                "message": r.message,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "normalized_ref": tx_codes.get(r.entity_id)
                if r.entity_type == "transaction"
                else (f"HOLDING-{r.entity_id}" if r.entity_type == "holding" and r.entity_id else None),
                "raw": r.raw,
            }
            for r in recs
        ],
    }


def record_lineage(db: Session, entity_type: str, entity_id: int) -> dict[str, Any]:
    if entity_type == "transaction":
        e = db.get(Transaction, entity_id)
    elif entity_type == "holding":
        e = db.get(Holding, entity_id)
    else:
        raise ConfigurationError("entity_type must be transaction or holding")
    if e is None:
        raise NotFoundError(f"{entity_type} {entity_id} not found")
    b = db.get(ImportBatch, e.batch_id)
    sr = db.get(SourceRecord, e.source_record_id) if e.source_record_id else None
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "normalized_id": getattr(e, "code", f"HOLDING-{entity_id}"),
        "source": b.source_label if b else None,
        "source_type": b.source_type if b else None,
        "file": b.file_name if b else None,
        "imported_at": b.created_at.isoformat() if b else None,
        "data_class": b.data_class if b else None,
        "original_row": sr.row_number if sr else None,
        "original_record": sr.raw if sr else None,
        "mapping": b.mapping if b else None,
        "sha256": b.file_sha256 if b else None,
    }


def _unused() -> None:  # keep optional imports referenced for type checkers
    _ = (np, pd, InsufficientDataError, Asset)


def source_record(db: Session, record_id: int) -> dict[str, Any]:
    sr = db.get(SourceRecord, record_id)
    if sr is None:
        raise NotFoundError(f"source record {record_id} not found")
    b = db.get(ImportBatch, sr.batch_id)
    ref = None
    if sr.entity_type == "transaction" and sr.entity_id:
        t = db.get(Transaction, sr.entity_id)
        ref = t.code if t else None
    elif sr.entity_type == "holding" and sr.entity_id:
        ref = f"HOLDING-{sr.entity_id}"
    return {
        "record_id": sr.id,
        "normalized_id": ref,
        "entity_type": sr.entity_type,
        "entity_id": sr.entity_id,
        "status": sr.status,
        "message": sr.message,
        "original_row": sr.row_number,
        "original_record": sr.raw,
        "source": b.source_label if b else None,
        "source_type": b.source_type if b else None,
        "file": b.file_name if b else None,
        "sha256": b.file_sha256 if b else None,
        "imported_at": b.created_at.isoformat() if b else None,
        "data_class": b.data_class if b else None,
        "batch_id": sr.batch_id,
    }
