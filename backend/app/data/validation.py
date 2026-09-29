"""Record-level validation applied to every ingested batch, regardless of provider.

Policy
------
* **Rejected** (not stored): unparseable/future dates, missing or non-numeric close,
  non-positive prices, impossible OHLC relationships, negative volume.
* **Deduplicated**: repeated ``symbol + date`` — the first occurrence is kept, later ones logged.
* **Stored with warning**: missing volume, zero volume on a day the price changed.
* **Flagged, stored**: unusually large daily moves. These are labelled *potential anomaly* and
  never deleted automatically — a large move may be real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

PRICE_COLS = ["open", "high", "low", "close", "adj_close"]


@dataclass
class ValidationReport:
    clean: pd.DataFrame
    issues: list[dict[str, Any]] = field(default_factory=list)
    received: int = 0
    rejected: int = 0
    duplicates: int = 0
    warnings: int = 0
    flagged: int = 0

    def summary(self) -> dict[str, Any]:
        by_check: dict[str, int] = {}
        for i in self.issues:
            by_check[i["check"]] = by_check.get(i["check"], 0) + 1
        return {
            "received": self.received,
            "accepted": len(self.clean),
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "warnings": self.warnings,
            "flagged": self.flagged,
            "by_check": by_check,
        }


def _issue(
    row: pd.Series | None,
    check: str,
    category: str,
    severity: str,
    action: str,
    detail: str,
    symbol: str | None = None,
    when: Any = None,
) -> dict[str, Any]:
    sym = symbol if symbol is not None else (None if row is None else row.get("symbol"))
    dt = when if when is not None else (None if row is None else row.get("date"))
    d = None
    if dt is not None and not (isinstance(dt, float) and np.isnan(dt)) and pd.notna(dt):
        try:
            d = pd.Timestamp(dt).date()
        except (ValueError, TypeError):
            d = None
    return {
        "symbol": None if sym is None or pd.isna(sym) else str(sym),
        "date": d,
        "check": check,
        "category": category,
        "severity": severity,
        "action": action,
        "detail": detail,
    }


def validate_bars(
    df: pd.DataFrame, today: date | None = None, outlier_sigma: float = 8.0, outlier_floor: float = 0.15
) -> ValidationReport:
    today = today or date.today()
    report = ValidationReport(clean=df.iloc[0:0].copy(), received=len(df))
    if df.empty:
        return report
    work = df.copy()
    work["symbol"] = work["symbol"].astype("string").str.strip().str.upper()
    reject = pd.Series(False, index=work.index)

    def mark(mask: pd.Series, check: str, category: str, detail_fn: Any) -> None:
        nonlocal reject
        new = mask & ~reject
        for idx in work.index[new]:
            report.issues.append(_issue(work.loc[idx], check, category, "error", "rejected", detail_fn(work.loc[idx])))
        reject |= new

    # --- Dates and identifiers ---------------------------------------------------------
    parsed = pd.to_datetime(work["date"], errors="coerce")
    missing_sym = work["symbol"].isna() | (work["symbol"] == "")
    mark(missing_sym, "missing_symbol", "validity", lambda r: "symbol is empty")
    bad_date = parsed.isna()
    for idx in work.index[bad_date & ~reject]:
        report.issues.append(
            _issue(
                None,
                "invalid_date",
                "validity",
                "error",
                "rejected",
                f"unparseable date '{work.at[idx, 'date']}'",
                symbol=work.at[idx, "symbol"],
            )
        )
    reject |= bad_date
    work["date"] = parsed.dt.normalize()
    mark(work["date"] > pd.Timestamp(today), "future_date", "validity", lambda r: "date is in the future")

    # --- Numeric fields -------------------------------------------------------------
    for c in [*PRICE_COLS, "volume"]:
        work[c] = pd.to_numeric(work[c], errors="coerce")
    mark(work["close"].isna(), "missing_close", "completeness", lambda r: "close price missing or non-numeric")
    nonpos = pd.Series(False, index=work.index)
    for c in PRICE_COLS:
        nonpos |= work[c].notna() & (work[c] <= 0)
    mark(nonpos, "non_positive_price", "validity", lambda r: "one or more prices are zero or negative")

    o, h, lo, c = work["open"], work["high"], work["low"], work["close"]
    has_hl = h.notna() & lo.notna()
    mark(has_hl & (lo > h), "low_above_high", "consistency", lambda r: f"low {r['low']:.4f} > high {r['high']:.4f}")
    tol = 1e-9
    ohlc_bad = has_hl & ((o.notna() & ((o > h * (1 + tol)) | (o < lo * (1 - tol)))) | (c > h * (1 + tol)) | (c < lo * (1 - tol)))
    mark(ohlc_bad, "ohlc_out_of_range", "consistency", lambda r: "open/close outside the [low, high] range")
    mark(work["volume"].notna() & (work["volume"] < 0), "negative_volume", "validity", lambda r: f"volume {r['volume']:.0f} < 0")

    report.rejected = int(reject.sum())
    work = work[~reject]

    # --- Uniqueness -------------------------------------------------------------------
    dup = work.duplicated(subset=["symbol", "date"], keep="first")
    for idx in work.index[dup]:
        report.issues.append(
            _issue(
                work.loc[idx],
                "duplicate_symbol_date",
                "uniqueness",
                "warning",
                "deduplicated",
                "repeated symbol+date; first occurrence kept",
            )
        )
    report.duplicates = int(dup.sum())
    work = work[~dup].sort_values(["symbol", "date"]).reset_index(drop=True)

    # --- Warnings (stored) -----------------------------------------------------------------
    for idx in work.index[work["volume"].isna()]:
        report.issues.append(_issue(work.loc[idx], "missing_volume", "completeness", "warning", "stored", "volume missing"))
        report.warnings += 1
    work["adj_close"] = work["adj_close"].fillna(work["close"])

    prev_close = work.groupby("symbol")["close"].shift(1)
    ret = work["close"] / prev_close - 1.0
    zero_vol = (work["volume"] == 0) & ret.notna() & (ret.abs() > 1e-6)
    for idx in work.index[zero_vol]:
        report.issues.append(
            _issue(
                work.loc[idx],
                "zero_volume_price_change",
                "validity",
                "warning",
                "stored",
                f"zero volume but price changed {ret[idx]:+.2%}",
            )
        )
        report.warnings += 1

    # --- Outliers: robust per-symbol scale, flagged not removed ------------------------------------
    def robust_sigma(s: pd.Series) -> float:
        s = s.dropna()
        if len(s) < 20:
            return np.nan
        return float(1.4826 * np.median(np.abs(s - np.median(s))))

    sig = ret.groupby(work["symbol"]).transform(robust_sigma)
    thresh = np.maximum(outlier_sigma * sig, outlier_floor)
    out_mask = ret.abs() > thresh
    for idx in work.index[out_mask.fillna(False)]:
        report.issues.append(
            _issue(
                work.loc[idx],
                "large_price_move",
                "outlier",
                "info",
                "flagged",
                f"potential anomaly: daily move {ret[idx]:+.2%} exceeds {thresh[idx]:.2%} "
                f"({outlier_sigma:g}x robust sigma, floor {outlier_floor:.0%})",
            )
        )
        report.flagged += 1

    work["volume"] = work["volume"].round().astype("Int64")
    report.clean = work[["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]]
    return report
