"""Data-quality scoring over *stored* data plus ingestion history.

Score components (each in [0, 1]):

=============  ======  ===================================================================
component      weight  definition
=============  ======  ===================================================================
completeness   0.30    1 − (missing dates + ½·missing volumes) / expected observations.
                       Expected dates = dataset calendar between each symbol's first and last bar.
validity       0.25    1 − (records rejected for invalid values) / records received.
uniqueness     0.15    1 − (duplicate symbol+date records) / records received.
consistency    0.20    1 − (OHLC-inconsistent records, received or stored) / records received.
freshness      0.10    1 if the latest bar is ≤ 1 business day old (live data); decays linearly
                       to 0 at 11 days. Static synthetic datasets are scored against their
                       declared end date and labelled as such.
=============  ======  ===================================================================

Large price moves are reported as *potential anomalies* and do not reduce the score.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

WEIGHTS = {"completeness": 0.30, "validity": 0.25, "uniqueness": 0.15, "consistency": 0.20, "freshness": 0.10}
VALIDITY_CHECKS = {"missing_symbol", "invalid_date", "future_date", "missing_close", "non_positive_price", "negative_volume"}
CONSISTENCY_CHECKS = {"low_above_high", "ohlc_out_of_range"}


def status_for(score: float) -> str:
    return "pass" if score >= 0.98 else "warning" if score >= 0.90 else "fail"


def assess_quality(
    bars: pd.DataFrame,
    ingestion_totals: dict[str, int],
    is_synthetic: bool,
    declared_end: date | None = None,
    today: date | None = None,
    max_issue_rows: int = 500,
) -> dict[str, Any]:
    """``bars``: stored rows with columns symbol, date, open, high, low, close, volume.
    ``ingestion_totals``: aggregated counts over ingestion runs: received, and one key per check name.
    """
    today = today or date.today()
    issues: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    if bars.empty:
        comps = {k: 0.0 for k in WEIGHTS}
        return {
            "overall_score": 0.0,
            "status": "fail",
            "components": comps,
            "checks": [
                {
                    "check": "has_data",
                    "category": "completeness",
                    "status": "fail",
                    "count": 0,
                    "detail": "dataset has no stored records",
                }
            ],
            "per_symbol": [],
            "issues": [],
        }

    df = bars.copy()
    df["date"] = pd.to_datetime(df["date"])
    calendar = pd.DatetimeIndex(sorted(df["date"].unique()))
    dataset_end = calendar[-1]

    # --- Completeness ------------------------------------------------------------------
    per_symbol = []
    expected_total = missing_total = 0
    for sym, g in df.groupby("symbol", sort=True):
        first, last = g["date"].min(), g["date"].max()
        exp_dates = calendar[(calendar >= first) & (calendar <= last)]
        missing = exp_dates.difference(pd.DatetimeIndex(g["date"]))
        expected_total += len(exp_dates)
        missing_total += len(missing)
        for d in missing[: max_issue_rows // 10]:
            issues.append(
                {
                    "symbol": sym,
                    "date": d.date(),
                    "check": "missing_date",
                    "category": "completeness",
                    "severity": "warning",
                    "action": "flagged",
                    "detail": "no bar on a dataset trading day",
                }
            )
        r = g.sort_values("date")["close"].pct_change(fill_method=None)
        mad = np.median(np.abs(r.dropna() - r.median())) * 1.4826 if r.notna().sum() > 20 else np.nan
        thr = max(8 * mad, 0.15) if np.isfinite(mad) else 0.15
        outliers = g.sort_values("date").loc[r.abs() > thr]
        for _, row in outliers.iterrows():
            ri = r.loc[row.name]
            issues.append(
                {
                    "symbol": sym,
                    "date": row["date"].date(),
                    "check": "large_price_move",
                    "category": "outlier",
                    "severity": "info",
                    "action": "flagged",
                    "detail": f"potential anomaly: daily move {ri:+.2%} (threshold {thr:.2%})",
                }
            )
        lag_days = int(np.busday_count(last.date(), dataset_end.date()))
        per_symbol.append(
            {
                "symbol": sym,
                "first_date": first.date().isoformat(),
                "last_date": last.date().isoformat(),
                "observations": len(g),
                "expected": len(exp_dates),
                "missing_dates": len(missing),
                "missing_volume": int(g["volume"].isna().sum()),
                "potential_anomalies": len(outliers),
                "lag_vs_dataset_end_days": lag_days,
                "completeness": 1.0 - len(missing) / len(exp_dates) if len(exp_dates) else 1.0,
            }
        )
        if lag_days > 5:
            issues.append(
                {
                    "symbol": sym,
                    "date": last.date(),
                    "check": "stale_symbol",
                    "category": "freshness",
                    "severity": "warning",
                    "action": "flagged",
                    "detail": f"last bar is {lag_days} business days before dataset end (possible delisting)",
                }
            )

    missing_volume = int(df["volume"].isna().sum())
    completeness = 1.0 - (missing_total + 0.5 * missing_volume) / max(expected_total, 1)

    # --- Stored-data validity & consistency re-checks --------------------------------------
    stored_invalid = int(((df[["open", "high", "low", "close"]] <= 0).any(axis=1) | (df["volume"] < 0).fillna(False)).sum())
    hl = df["high"].notna() & df["low"].notna()
    stored_inconsistent = int(
        (hl & ((df["low"] > df["high"]) | (df["close"] > df["high"] * (1 + 1e-9)) | (df["close"] < df["low"] * (1 - 1e-9)))).sum()
    )
    stored_dups = int(df.duplicated(["symbol", "date"]).sum())

    received = max(int(ingestion_totals.get("received", 0)), len(df))
    rej_validity = sum(int(ingestion_totals.get(k, 0)) for k in VALIDITY_CHECKS)
    rej_consistency = sum(int(ingestion_totals.get(k, 0)) for k in CONSISTENCY_CHECKS)
    dedup = int(ingestion_totals.get("duplicate_symbol_date", 0))
    validity = 1.0 - (rej_validity + stored_invalid) / received
    uniqueness = 1.0 - (dedup + stored_dups) / received
    consistency = 1.0 - (rej_consistency + stored_inconsistent) / received

    # --- Freshness --------------------------------------------------------------------------
    if is_synthetic:
        ref = declared_end or dataset_end.date()
        lag = int(np.busday_count(dataset_end.date(), ref)) if ref > dataset_end.date() else 0
        freshness_note = f"static synthetic dataset; scored against declared end date {ref.isoformat()}"
    else:
        lag = int(np.busday_count(dataset_end.date(), today)) if today > dataset_end.date() else 0
        freshness_note = f"latest bar {dataset_end.date().isoformat()} is {lag} business day(s) old"
    freshness = 1.0 if lag <= 1 else max(0.0, 1.0 - (lag - 1) / 10.0)

    comps = {
        "completeness": completeness,
        "validity": validity,
        "uniqueness": uniqueness,
        "consistency": consistency,
        "freshness": freshness,
    }
    comps = {k: float(min(1.0, max(0.0, v))) for k, v in comps.items()}
    overall = float(sum(WEIGHTS[k] * v for k, v in comps.items()))

    def chk(name: str, category: str, count: int, detail: str, fail_if_any: bool = True) -> None:
        st = "pass" if count == 0 else ("fail" if fail_if_any else "warning")
        checks.append({"check": name, "category": category, "status": st, "count": int(count), "detail": detail})

    chk("missing_dates", "completeness", missing_total, f"{missing_total} of {expected_total} expected bars missing", False)
    chk("missing_volume", "completeness", missing_volume, "stored bars with null volume", False)
    chk("rejected_invalid_values", "validity", rej_validity, "records rejected at ingestion for invalid values", False)
    chk("stored_invalid_values", "validity", stored_invalid, "stored bars with non-positive prices or negative volume")
    chk("deduplicated_records", "uniqueness", dedup, "duplicate symbol+date records removed at ingestion", False)
    chk("stored_duplicates", "uniqueness", stored_dups, "duplicate symbol+date in storage (enforced by unique index)")
    chk("rejected_ohlc_inconsistent", "consistency", rej_consistency, "records rejected for impossible OHLC", False)
    chk("stored_ohlc_inconsistent", "consistency", stored_inconsistent, "stored bars violating low<=open,close<=high")
    stale = sum(1 for p in per_symbol if p["lag_vs_dataset_end_days"] > 5)
    chk("stale_symbols", "freshness", stale, "symbols whose history ends before the dataset end", False)
    n_out = sum(p["potential_anomalies"] for p in per_symbol)
    checks.append(
        {
            "check": "large_price_moves",
            "category": "outlier",
            "status": "info",
            "count": n_out,
            "detail": "potential anomalies (flagged, not removed; may be genuine market moves)",
        }
    )
    checks.append(
        {
            "check": "freshness",
            "category": "freshness",
            "status": "pass" if freshness >= 0.99 else "warning",
            "count": lag,
            "detail": freshness_note,
        }
    )

    return {
        "overall_score": overall,
        "status": status_for(overall),
        "components": comps,
        "weights": WEIGHTS,
        "checks": checks,
        "per_symbol": per_symbol,
        "issues": issues[:max_issue_rows],
        "records_stored": len(df),
        "records_received": received,
        "calendar_days": len(calendar),
    }
