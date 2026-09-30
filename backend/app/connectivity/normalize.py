"""Universal import: column detection, mapping and normalisation into the canonical model.

Canonical record kinds and fields
---------------------------------
holdings      account, symbol, quantity*, average_cost, price, market_value, currency, description, asset_class
transactions  account, date*, symbol, type*, quantity, price, fees, amount, currency, description
market_data   symbol*, date*, open, high, low, close*, adj_close, volume         (* required)

Detection scores every source column against each field's synonyms: exact canonical name → 1.0,
known synonym → 0.9, fuzzy match → the similarity ratio (only ≥ 0.8 is proposed). Mappings with any
proposed field below 0.9 are reported as *uncertain* so the user reviews them before importing.
"""

from __future__ import annotations

import difflib
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from app.core.errors import ConfigurationError

KINDS = ("holdings", "transactions", "market_data")

FIELDS: dict[str, dict[str, list[str]]] = {
    "holdings": {
        "account": [
            "account",
            "account_id",
            "account number",
            "account_number",
            "acct",
            "acct no",
            "account no",
            "acct number",
            "portfolio",
            "account name",
        ],
        "symbol": ["symbol", "ticker", "security", "security id", "instrument", "code", "stock"],
        "quantity": ["quantity", "qty", "shares", "units", "position", "holding", "quantity held", "nominal"],
        "average_cost": [
            "average cost",
            "avg cost",
            "average_cost",
            "cost per share",
            "avg price",
            "average price",
            "unit cost",
            "book price",
        ],
        "price": ["price", "current price", "last price", "last", "market price", "close", "mark"],
        "market_value": ["market value", "value", "mkt value", "market_value", "current value", "position value"],
        "currency": ["currency", "ccy", "curr", "cur"],
        "description": ["description", "name", "security name", "security description", "instrument name"],
        "asset_class": ["asset class", "asset_class", "type", "security type", "instrument type", "class"],
    },
    "transactions": {
        "account": [
            "account",
            "account_id",
            "account number",
            "account_number",
            "acct",
            "acct no",
            "account no",
            "acct number",
            "portfolio",
            "account name",
        ],
        "date": ["date", "trade date", "trade_date", "transaction date", "settlement date", "execution date", "run date"],
        "symbol": ["symbol", "ticker", "security", "instrument", "stock"],
        "type": ["type", "action", "transaction type", "activity", "side", "trans type", "transaction"],
        "quantity": ["quantity", "qty", "shares", "units"],
        "price": ["price", "trade price", "execution price", "unit price", "fill price"],
        "fees": ["fees", "fee", "commission", "commissions", "fees & comm", "fees and commissions", "charges"],
        "amount": ["amount", "net amount", "total", "net", "value", "proceeds"],
        "currency": ["currency", "ccy", "curr"],
        "description": ["description", "memo", "details", "note"],
    },
    "market_data": {
        "symbol": ["symbol", "ticker", "security"],
        "date": ["date", "timestamp", "day", "trade date"],
        "open": ["open", "o"],
        "high": ["high", "h"],
        "low": ["low", "l"],
        "close": ["close", "c", "price", "last"],
        "adj_close": ["adj close", "adj_close", "adjusted close", "adjclose"],
        "volume": ["volume", "vol", "v"],
    },
}
REQUIRED = {"holdings": ["symbol", "quantity"], "transactions": ["date", "type"], "market_data": ["symbol", "date", "close"]}

TX_TYPES: dict[str, list[str]] = {
    "buy": ["buy", "bought", "purchase", "buy to open", "reinvest", "reinvestment", "dividend reinvestment", "b"],
    "sell": ["sell", "sold", "sale", "sell to close", "s"],
    "dividend": ["dividend", "div", "cash dividend", "qualified dividend", "ordinary dividend", "distribution"],
    "interest": ["interest", "credit interest", "int"],
    "fee": ["fee", "fees", "commission", "service fee", "adr fee", "tax", "withholding"],
    "deposit": ["deposit", "contribution", "wire in", "ach in", "transfer in cash", "cash in"],
    "withdrawal": ["withdrawal", "wire out", "ach out", "cash out", "distribution out"],
    "split": ["split", "stock split", "forward split"],
    "transfer_in": ["transfer in", "journal in", "acat in", "shares in"],
    "transfer_out": ["transfer out", "journal out", "acat out", "shares out"],
}
CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "C$": "CAD", "A$": "AUD", "CHF": "CHF"}
CASH_SYMBOLS = {"CASH", "USD", "MMF", "CASH & CASH INVESTMENTS", "MONEY MARKET", "SWEEP", "CORE"}


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


@dataclass
class MappingProposal:
    kind: str
    mapping: dict[str, str | None]  # canonical field -> source column
    confidence: dict[str, float]
    uncertain: list[str]
    missing_required: list[str]
    kind_scores: dict[str, float] = field(default_factory=dict)


def propose_mapping(columns: list[str], kind: str | None = None) -> MappingProposal:
    cols = [str(c) for c in columns]
    keyed = {c: _key(c) for c in cols}
    scores: dict[str, float] = {}
    proposals: dict[str, tuple[dict[str, str | None], dict[str, float]]] = {}
    for k in KINDS:
        used: set[str] = set()
        mapping: dict[str, str | None] = {}
        conf: dict[str, float] = {}
        for fld, syns in FIELDS[k].items():
            best, best_s = None, 0.0
            for c, ck in keyed.items():
                if c in used:
                    continue
                keyed_syns = {_key(x) for x in syns}
                if ck == fld.replace("_", " "):
                    s = 1.0
                elif ck in keyed_syns:
                    s = 0.9
                else:
                    s = max(difflib.SequenceMatcher(None, ck, syn).ratio() for syn in [*syns, fld.replace("_", " ")])
                    s = s * 0.85 if s >= 0.8 else 0.0
                if s > best_s:
                    best, best_s = c, s
            mapping[fld] = best
            conf[fld] = round(best_s, 3)
            if best:
                used.add(best)
        req = REQUIRED[k]
        scores[k] = sum(conf[f] for f in req) / len(req) + 0.05 * sum(1 for v in conf.values() if v >= 0.9)
        proposals[k] = (mapping, conf)
    chosen = kind or max(scores, key=lambda k: scores[k])
    if chosen not in KINDS:
        raise ConfigurationError(f"record kind must be one of {KINDS}")
    mapping, conf = proposals[chosen]
    return MappingProposal(
        kind=chosen,
        mapping=mapping,
        confidence=conf,
        uncertain=[f for f, c in conf.items() if mapping.get(f) and c < 0.9],
        missing_required=[f for f in REQUIRED[chosen] if not mapping.get(f)],
        kind_scores={k: round(v, 3) for k, v in scores.items()},
    )


# ---------------------------------------------------------------- file parsing


def read_table(content: bytes, file_name: str) -> tuple[pd.DataFrame, str]:
    """Parse CSV / JSON / XLSX into a string-typed DataFrame (normalisation happens later)."""
    name = file_name.lower()
    try:
        if name.endswith(".csv") or name.endswith(".txt"):
            text = content.decode("utf-8-sig", errors="strict")
            # Brokerage exports often have preamble lines: start at the first line with ≥ 3 delimiters.
            lines = text.splitlines()
            start = next((i for i, ln in enumerate(lines) if ln.count(",") >= 2), 0)
            df = pd.read_csv(io.StringIO("\n".join(lines[start:])), dtype=str, keep_default_na=False, skip_blank_lines=True)
            fmt = "csv"
        elif name.endswith(".json"):
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, dict):
                data = next((v for v in data.values() if isinstance(v, list)), [data])
            df = pd.DataFrame(data).astype(str)
            fmt = "json"
        elif name.endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(content), dtype=str, engine="openpyxl").fillna("")
            fmt = "xlsx"
        else:
            raise ConfigurationError("unsupported file type — use .csv, .json or .xlsx")
    except UnicodeDecodeError as exc:
        raise ConfigurationError("file is not valid UTF-8 text") from exc
    except (ValueError, pd.errors.ParserError) as exc:
        raise ConfigurationError(f"could not parse file: {exc.__class__.__name__}") from exc
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
    # Drop trailing footer/total rows that are completely empty after the first column.
    df = (
        df[~(df.drop(columns=df.columns[:1]).apply(lambda r: all(str(v).strip() == "" for v in r), axis=1))]
        if df.shape[1] > 1
        else df
    )
    return df.reset_index(drop=True), fmt


# ---------------------------------------------------------------- value normalisation


def parse_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if isinstance(v, float) and math.isnan(v) else float(v)
    s = str(v).strip()
    if s in ("", "-", "--", "n/a", "N/A", "nan", "None"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("$", "").replace("€", "").replace("£", "").replace("%", "").strip()
    if s.startswith("+"):
        s = s[1:]
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def parse_date(v: Any, dayfirst: bool = False) -> date | None:
    s = str(v).strip()
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", s):
            return pd.Timestamp(s[:10]).date()
        ts = pd.to_datetime(s, dayfirst=dayfirst, errors="raise")
        return ts.date()
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_symbol(v: Any) -> str | None:
    s = str(v or "").strip().upper()
    if not s or s in ("NAN", "NONE"):
        return None
    s = re.sub(r"\s+", " ", s)
    # Class-share separator normalised to the market-data convention (BRK.B → BRK-B).
    if re.fullmatch(r"[A-Z]{1,5}\.[A-Z]", s):
        s = s.replace(".", "-")
    return s


def normalize_currency(v: Any, default: str = "USD") -> tuple[str, bool]:
    """Returns (ISO code, defaulted?)."""
    s = str(v or "").strip()
    if not s:
        return default, True
    if s in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[s], False
    s = s.upper()
    if re.fullmatch(r"[A-Z]{3}", s):
        return s, False
    return default, True


def normalize_tx_type(v: Any) -> str | None:
    k = _key(v)
    if not k:
        return None
    for canon, syns in TX_TYPES.items():
        if k == canon or k in syns:
            return canon
    for canon, syns in TX_TYPES.items():  # prefix match, e.g. "BUY - MARKET"
        if any(k.startswith(sy + " ") for sy in syns if len(sy) > 2):
            return canon
    return None


def infer_asset_class(symbol: str | None, raw_class: str | None) -> str:
    rc = _key(raw_class or "")
    if (symbol and symbol.upper() in CASH_SYMBOLS) or "cash" in rc or "money market" in rc:
        return "cash"
    for key, cls in (
        ("etf", "etf"),
        ("fund", "fund"),
        ("bond", "bond"),
        ("fixed", "bond"),
        ("option", "option"),
        ("crypto", "crypto"),
        ("equity", "equity"),
        ("stock", "equity"),
        ("share", "equity"),
    ):
        if key in rc:
            return cls
    return "unknown"
