"""Automatic portfolio reconstruction from imported/connected accounts ("My Portfolio").

Position source per account (one source per account, so nothing is counted twice)
---------------------------------------------------------------------------------
1. the latest holdings snapshot for the account (by as-of date, then import order), else
2. positions derived from the account's transactions with the chosen cost-basis method.
The same account reported by two sources resolves to one ``accounts`` row (institution + external
id), so consolidation sums *different* accounts only; conflicting sources surface in reconciliation.

Valuation
---------
Each symbol is priced from stored market data (real external data preferred over synthetic).
Positions without stored prices fall back to the imported price, flagged as such; positions with
neither are reported as unpriced and excluded from totals. Non-USD positions are converted with a
stored ``<CCY>USD=X`` rate when available, otherwise excluded from totals with a warning.

History
-------
* Accounts with transactions: daily share counts are reconstructed from the transactions and the
  daily return is the return of the positions held at the prior close (time-weighted, so deposits,
  withdrawals and trades are not counted as performance).
* Accounts with only a holdings snapshot: a **backcast** — today's quantities applied to historical
  prices. It shows how the current holdings would have behaved, not the account's actual history,
  and is labelled accordingly.
"""

from __future__ import annotations

import math
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.analytics.correlation import correlation_matrix
from app.connectivity.cost_basis import compute as cost_basis
from app.connectivity.cost_basis import quantity_path
from app.core.config import get_settings
from app.core.errors import InsufficientDataError, NotFoundError
from app.models import Account, Asset, CompanyProfile, Dataset, Holding, ImportBatch, MarketData, Transaction
from app.risk.contribution import concentration, risk_contributions
from app.risk.var import var_summary
from app.services.market_data import load_panel

LIVE_DATASET = "LIVE-MARKET"
_cache: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_lock = Lock()


def invalidate() -> None:
    with _lock:
        _cache.clear()


@dataclass
class PriceInfo:
    price: float
    date: date
    dataset_id: int
    dataset_code: str
    is_synthetic: bool
    name: str | None = None
    sector: str | None = None
    asset_type: str | None = None


@dataclass
class Book:
    scope: str
    accounts: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    history_method: dict[int, str] = field(default_factory=dict)
    tx_by_account: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    quantities: dict[str, dict[int, float]] = field(default_factory=dict)  # symbol -> account -> qty (for backcast)
    prices: dict[str, PriceInfo] = field(default_factory=dict)
    fx: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------- data access


def scope_accounts(db: Session, scope: str) -> list[Account]:
    if scope in ("all", "", None):
        return list(db.scalars(select(Account).order_by(Account.id)))
    try:
        aid = int(scope)
    except ValueError as exc:
        raise NotFoundError(f"unknown scope '{scope}'") from exc
    a = db.get(Account, aid)
    if a is None:
        raise NotFoundError(f"account {aid} not found")
    return [a]


def latest_prices(db: Session, symbols: list[str]) -> dict[str, PriceInfo]:
    if not symbols:
        return {}
    last = (
        select(MarketData.asset_id, func.max(MarketData.date).label("d"))
        .join(Asset, Asset.id == MarketData.asset_id)
        .where(Asset.symbol.in_(symbols))
        .group_by(MarketData.asset_id)
        .subquery()
    )
    rows = db.execute(
        select(
            Asset.symbol,
            Asset.name,
            Asset.sector,
            Asset.asset_type,
            MarketData.adj_close,
            MarketData.close,
            MarketData.date,
            Dataset.id,
            Dataset.code,
            Dataset.is_synthetic,
        )
        .join(last, (last.c.asset_id == MarketData.asset_id) & (last.c.d == MarketData.date))
        .join(Asset, Asset.id == MarketData.asset_id)
        .join(Dataset, Dataset.id == Asset.dataset_id)
    ).all()
    out: dict[str, PriceInfo] = {}
    for sym, name, sector, atype, _adj, close, d, ds_id, code, synth in rows:
        cand = PriceInfo(float(close), d, ds_id, code, bool(synth), name, sector, atype)
        cur = out.get(sym)
        # Prefer real data over synthetic; then the most recent bar.
        if cur is None or (cur.is_synthetic and not synth) or (cur.is_synthetic == synth and d > cur.date):
            out[sym] = cand
    return out


def holdings_by_account(
    db: Session, accounts: list[Account], method: str
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, str], dict[int, list[dict[str, Any]]], list[str]]:
    positions: dict[int, list[dict[str, Any]]] = {}
    source: dict[int, str] = {}
    txs: dict[int, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    for a in accounts:
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
        txs[a.id] = tx
        latest = db.execute(
            select(Holding.batch_id, ImportBatch.as_of, ImportBatch.source_label)
            .join(ImportBatch, ImportBatch.id == Holding.batch_id)
            .where(Holding.account_id == a.id)
            .order_by(ImportBatch.as_of.desc(), Holding.batch_id.desc())
            .limit(1)
        ).first()
        if latest:
            rows = db.scalars(select(Holding).where(Holding.account_id == a.id, Holding.batch_id == latest[0])).all()
            positions[a.id] = [
                {
                    "symbol": h.symbol,
                    "quantity": h.quantity,
                    "average_cost": h.average_cost,
                    "price": h.price,
                    "currency": h.currency,
                    "asset_class": h.asset_class,
                    "description": h.description,
                    "holding_id": h.id,
                }
                for h in rows
            ]
            source[a.id] = f"holdings snapshot as of {latest[1]} ({latest[2]}, batch #{latest[0]})"
        elif tx:
            cb = cost_basis(tx, method)  # type: ignore[arg-type]
            positions[a.id] = [
                {
                    "symbol": p["symbol"],
                    "quantity": p["quantity"],
                    "average_cost": p["average_cost"],
                    "price": None,
                    "currency": a.base_currency,
                    "asset_class": "unknown",
                    "description": None,
                    "holding_id": None,
                }
                for p in cb["positions"]
                if p["quantity"] > 1e-9
            ]
            source[a.id] = f"derived from {len(tx)} transactions ({method.upper()})"
            if cb["issues"]:
                warnings.append(f"{a.name}: {len(cb['issues'])} cost-basis issue(s) (e.g. {cb['issues'][0]['issue']})")
        else:
            positions[a.id] = []
            source[a.id] = "no holdings or transactions"
    return positions, source, txs, warnings


# ---------------------------------------------------------------- book assembly


def build_book(db: Session, scope: str = "all", method: str = "fifo") -> Book:
    accts = scope_accounts(db, scope)
    per_acct, source, txs, warnings = holdings_by_account(db, accts, method)
    symbols = sorted({p["symbol"] for rows in per_acct.values() for p in rows if p["symbol"] != "CASH"})
    currencies = sorted({p["currency"] for rows in per_acct.values() for p in rows} - {"USD"})
    prices = latest_prices(db, symbols + [f"{c}USD=X" for c in currencies])
    fx = {"USD": 1.0}
    for c in currencies:
        pi = prices.get(f"{c}USD=X")
        if pi:
            fx[c] = pi.price
        else:
            warnings.append(f"no stored FX rate for {c}; {c} positions are shown but excluded from USD totals")
    profiles = (
        {p.symbol: p for p in db.scalars(select(CompanyProfile).where(CompanyProfile.symbol.in_(symbols)))} if symbols else {}
    )

    agg: dict[str, dict[str, Any]] = {}
    quantities: dict[str, dict[int, float]] = defaultdict(dict)
    acct_rows = []
    for a in accts:
        a_value = 0.0
        for p in per_acct[a.id]:
            sym = p["symbol"]
            pi = prices.get(sym)
            if sym == "CASH":
                px, px_src, px_date = 1.0, "cash", None
            elif pi:
                px, px_src, px_date = (
                    pi.price,
                    f"market data {pi.dataset_code}{' (synthetic)' if pi.is_synthetic else ''}",
                    pi.date.isoformat(),
                )
            elif p["price"] is not None:
                px, px_src, px_date = p["price"], "imported price (no stored market data)", None
            else:
                px, px_src, px_date = None, "unpriced", None
            rate = fx.get(p["currency"])
            mv_local = p["quantity"] * px if px is not None else None
            mv = mv_local * rate if (mv_local is not None and rate is not None) else None
            if mv is not None:
                a_value += mv
            e = agg.setdefault(
                sym,
                {
                    "symbol": sym,
                    "quantity": 0.0,
                    "market_value": 0.0,
                    "cost_basis": 0.0,
                    "cost_known": True,
                    "price": px,
                    "price_date": px_date,
                    "price_source": px_src,
                    "currency": p["currency"],
                    "fx_rate": rate,
                    "asset_class": p["asset_class"],
                    "accounts": [],
                    "valued": True,
                    "description": p.get("description"),
                },
            )
            e["quantity"] += p["quantity"]
            if mv is None:
                e["valued"] = False
            else:
                e["market_value"] += mv
            if p["average_cost"] is not None and rate is not None:
                e["cost_basis"] += p["average_cost"] * p["quantity"] * rate
            else:
                e["cost_known"] = False
            e["accounts"].append({"account_id": a.id, "account": a.name, "quantity": p["quantity"], "source": source[a.id]})
            if sym != "CASH":
                quantities[sym][a.id] = quantities[sym].get(a.id, 0.0) + p["quantity"]
        acct_rows.append(
            {
                "id": a.id,
                "name": a.name,
                "institution": a.institution,
                "external_id": a.external_id,
                "base_currency": a.base_currency,
                "position_source": source[a.id],
                "positions": len(per_acct[a.id]),
                "market_value": a_value,
                "transactions": len(txs[a.id]),
            }
        )

    total = sum(e["market_value"] for e in agg.values() if e["valued"])
    positions = []
    for sym, e in sorted(agg.items(), key=lambda kv: -kv[1]["market_value"]):
        pi = prices.get(sym)
        prof = profiles.get(sym)
        if e["asset_class"] == "unknown" and pi is not None:
            e["asset_class"] = {
                "etf": "etf",
                "equity": "equity",
                "index": "index",
                "commodity": "commodity",
                "bond_index": "bond",
            }.get((pi.asset_type or "").lower(), "equity" if prof else "unknown")
        classification, class_source = None, None
        if prof and prof.sic_major_group:
            classification, class_source = prof.sic_major_group, f"SEC SIC {prof.sic} ({prof.sic_description})"
        elif pi and pi.is_synthetic and pi.sector:
            classification, class_source = pi.sector, "synthetic dataset sector label"
        positions.append(
            {
                **{k: v for k, v in e.items() if k != "cost_known"},
                "market_value": e["market_value"] if e["valued"] else None,
                "weight": (e["market_value"] / total) if (e["valued"] and total > 0) else None,
                "cost_basis": e["cost_basis"] if e["cost_known"] else None,
                "unrealized_pnl": (e["market_value"] - e["cost_basis"])
                if (e["valued"] and e["cost_known"] and sym != "CASH")
                else None,
                "name": (prof.name if prof else None) or (pi.name if pi else None) or e.get("description") or sym,
                "industry": classification,
                "industry_source": class_source,
                "country": prof.business_country if prof else None,
                "data_class": "synthetic" if (pi and pi.is_synthetic) else "real_external" if pi else "user_imported",
            }
        )
    unpriced = [p["symbol"] for p in positions if p["market_value"] is None]
    if unpriced:
        warnings.append(
            f"{len(unpriced)} position(s) have no usable price or FX rate and are excluded from totals: {', '.join(unpriced[:8])}"
        )
    classes = {p["data_class"] for p in positions if p["symbol"] != "CASH"}
    if {"synthetic", "real_external"} <= classes:
        warnings.append("holdings are priced from a mix of real and synthetic market data")
    method_map = {
        a.id: ("transactions" if txs[a.id] and "derived" in source[a.id] else "backcast" if per_acct[a.id] else "none")
        for a in accts
    }
    # Accounts with a snapshot *and* transactions still use transactions for history where they exist.
    for a in accts:
        if txs[a.id]:
            method_map[a.id] = "transactions"
    return Book(
        scope=scope,
        accounts=acct_rows,
        positions=positions,
        warnings=warnings,
        history_method=method_map,
        tx_by_account=txs,
        quantities=dict(quantities),
        prices=prices,
        fx=fx,
    )


def price_history(db: Session, symbols: list[str], prices: dict[str, PriceInfo]) -> pd.DataFrame:
    """Adjusted closes for the symbols, taken from the dataset that supplied each symbol's latest price."""
    by_ds: dict[int, list[str]] = defaultdict(list)
    for s in symbols:
        if s in prices:
            by_ds[prices[s].dataset_id].append(s)
    frames = []
    for ds_id, syms in by_ds.items():
        panel = load_panel(db, ds_id)
        frames.append(panel.adj_close[[s for s in syms if s in panel.adj_close.columns]])
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1).sort_index()


def benchmark_for(db: Session, book: Book, requested: str | None = None) -> tuple[str | None, pd.Series | None]:
    cands = [requested] if requested else []
    real = any(not p.is_synthetic for p in book.prices.values())
    cands += ["SPY", "NXMKT"] if real else ["NXMKT", "SPY"]
    for c in cands:
        if not c:
            continue
        pi = latest_prices(db, [c]).get(c)
        if pi:
            s = load_panel(db, pi.dataset_id).adj_close[c]
            return c, s
    return None, None


def daily_series(db: Session, book: Book, lookback_days: int = 756) -> dict[str, Any]:
    """Portfolio (securities sleeve) value and time-weighted daily returns plus per-asset contributions."""
    syms = [p["symbol"] for p in book.positions if p["symbol"] != "CASH" and p["symbol"] in book.prices]
    # Symbols that were traded historically but are no longer held also need prices.
    traded = {t["symbol"] for tx in book.tx_by_account.values() for t in tx if t["symbol"]}
    all_syms = sorted(set(syms) | (traded & set(book.prices)) | {s for s in traded if s not in book.prices})
    extra = latest_prices(db, [s for s in all_syms if s not in book.prices])
    prices = {**book.prices, **extra}
    px = price_history(db, [s for s in all_syms if s in prices], prices).ffill()
    if px.empty:
        raise InsufficientDataError("no stored market data for any holding — connect a market-data source and sync")
    qty = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    methods = set()
    for acct_id, m in book.history_method.items():
        if m == "transactions":
            methods.add("transactions")
            for sym, path in quantity_path(book.tx_by_account[acct_id]).items():
                if sym not in qty.columns:
                    continue
                s = pd.Series({pd.Timestamp(d): q for d, q in path})
                s = s[~s.index.duplicated(keep="last")].reindex(qty.index.union(s.index)).ffill().reindex(qty.index).fillna(0.0)
                qty[sym] += s
        elif m == "backcast":
            methods.add("backcast")
            for sym, per in book.quantities.items():
                if sym in qty.columns and acct_id in per:
                    qty[sym] += per[acct_id]
    fx = pd.Series(
        {
            s: book.fx.get(next((p["currency"] for p in book.positions if p["symbol"] == s), "USD"), 1.0) or 0.0
            for s in qty.columns
        }
    )
    value_by_asset = qty * px * fx
    held = qty.shift(1) * px.shift(1) * fx
    base = held.sum(axis=1)
    pnl = (qty.shift(1) * (px - px.shift(1)) * fx).fillna(0.0)
    active = base > 0
    first = active.idxmax() if active.any() else None
    if first is None:
        raise InsufficientDataError("no positions with price history to build a return series")
    if "backcast" in methods and "transactions" not in methods:
        first = max(first, px.index[-1] - pd.Timedelta(days=int(lookback_days * 365 / 252)))
    idx = base.loc[first:].index[1:]
    r = (pnl.sum(axis=1) / base).loc[idx].replace([np.inf, -np.inf], np.nan).dropna()
    contrib = pnl.div(base.where(base > 0), axis=0).loc[r.index]
    weights_prev = held.div(base.where(base > 0), axis=0).loc[r.index]
    return {
        "returns": r,
        "value": value_by_asset.sum(axis=1).loc[r.index],
        "contrib": contrib,
        "weights": weights_prev,
        "asset_returns": px.pct_change(fill_method=None).loc[r.index],
        "methods": sorted(methods),
        "method_label": " + ".join(
            {
                "transactions": "reconstructed from transactions (time-weighted)",
                "backcast": "backcast of current holdings (not actual account history)",
            }[m]
            for m in sorted(methods)
        ),
    }


# ---------------------------------------------------------------- analytics


def _cache_key(db: Session, scope: str, method: str, extra: tuple[Any, ...]) -> tuple[Any, ...]:
    fp = (
        db.scalar(select(func.max(Holding.id))),
        db.scalar(select(func.max(Transaction.id))),
        db.scalar(select(func.sum(Dataset.version))),
        db.scalar(select(func.max(CompanyProfile.id))),
    )
    return (scope, method, fp, extra)


def analytics(
    db: Session, scope: str = "all", method: str = "fifo", benchmark: str | None = None, risk_free_rate: float | None = None
) -> dict[str, Any]:
    rf = get_settings().risk_free_rate if risk_free_rate is None else risk_free_rate
    key = _cache_key(db, scope, method, (benchmark, rf))
    with _lock:
        if key in _cache:
            return _cache[key]
    book = build_book(db, scope, method)
    total = sum(p["market_value"] or 0 for p in book.positions)
    cash = sum(p["market_value"] or 0 for p in book.positions if p["symbol"] == "CASH")
    secs = [p for p in book.positions if p["symbol"] != "CASH" and p["market_value"]]
    out: dict[str, Any] = {
        "scope": scope,
        "cost_basis_method": method,
        "risk_free_rate": rf,
        "accounts": book.accounts,
        "positions": book.positions,
        "warnings": list(book.warnings),
        "totals": {
            "market_value": total,
            "cash": cash,
            "securities": total - cash,
            "positions": len(secs),
            "accounts": len(book.accounts),
        },
        "allocation": _group(book.positions, "asset_class", total),
        "industry": _group(secs, "industry", total - cash, "Unclassified (no SEC SIC / sector metadata)"),
        "country": _group(secs, "country", total - cash, "Unclassified (no SEC address metadata)"),
        "currency": _group(book.positions, "currency", total),
        "concentration": None,
        "history": None,
        "metrics": None,
        "risk": None,
        "benchmark": None,
    }
    if secs:
        w = pd.Series({p["symbol"]: p["market_value"] for p in secs})
        w = w / w.sum()
        c = concentration(w)
        srt = w.sort_values(ascending=False)
        out["concentration"] = {
            **c,
            "top10_weight": float(srt.head(10).sum()),
            "largest_position": srt.index[0],
            "largest_weight": float(srt.iloc[0]),
        }
    try:
        ds = daily_series(db, book)
    except InsufficientDataError as exc:
        out["warnings"].append(exc.message)
        _put(key, out)
        return out
    r = ds["returns"]
    bsym, bclose = benchmark_for(db, book, benchmark)
    b_r = bclose.pct_change(fill_method=None).reindex(r.index) if bclose is not None else None
    if len(r) >= 2:
        out["metrics"] = M.performance_summary(r, b_r, rf)
        out["benchmark"] = {
            "symbol": bsym,
            "summary": M.performance_summary(b_r.dropna(), None, rf) if b_r is not None and b_r.notna().sum() > 2 else None,
        }
    dd = M.drawdown_series(r)
    out["history"] = {
        "method": ds["methods"],
        "method_label": ds["method_label"],
        "dates": [d.date().isoformat() for d in r.index],
        "value": _l(ds["value"]),
        "returns": _l(r),
        "drawdown": _l(dd),
        "benchmark_growth": _l((1 + b_r.fillna(0)).cumprod()) if b_r is not None else None,
        "portfolio_growth": _l((1 + r).cumprod()),
    }
    risk: dict[str, Any] = {}
    for conf in (0.95, 0.99):
        try:
            risk[str(int(conf * 100))] = var_summary(r, conf, 1, out["totals"]["securities"])
        except InsufficientDataError as exc:
            risk[str(int(conf * 100))] = {"error": exc.message}
    held_syms = [s for s in w.index if s in ds["asset_returns"].columns] if secs else []
    ar = ds["asset_returns"][held_syms].iloc[-756:] if held_syms else pd.DataFrame()
    if len(held_syms) >= 1 and len(ar.dropna()) >= 30:
        ww = w[held_syms] / w[held_syms].sum()
        try:
            risk["decomposition"] = risk_contributions(ww, ar)
        except InsufficientDataError as exc:
            risk["decomposition"] = {"error": exc.message}
        if len(held_syms) >= 2:
            risk["correlation"] = correlation_matrix(ar, cluster=len(held_syms) > 2)
    out["risk"] = risk
    _put(key, out)
    return out


def _put(key: tuple[Any, ...], value: dict[str, Any]) -> None:
    with _lock:
        _cache[key] = value
        while len(_cache) > 32:
            _cache.popitem(last=False)


def _group(
    positions: list[dict[str, Any]], field_: str, total: float, missing_label: str = "Unclassified"
) -> list[dict[str, Any]]:
    g: dict[str, float] = defaultdict(float)
    for p in positions:
        if p["market_value"]:
            g[p.get(field_) or missing_label] += p["market_value"]
    return sorted(
        [{"label": k, "market_value": v, "weight": v / total if total else None} for k, v in g.items()],
        key=lambda x: -x["market_value"],
    )


def _l(s: pd.Series) -> list[float | None]:
    return [None if v is None or not math.isfinite(v) else float(v) for v in s.to_numpy(dtype=float)]
