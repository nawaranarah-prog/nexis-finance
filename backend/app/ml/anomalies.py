"""Unsupervised anomaly detection on daily bars.

Two methods are run side by side so the machine-learning model can be compared with a
transparent baseline:

* ``rolling_zscore`` — flag when max(|return z|, volume z, |range z|) exceeds ``z_threshold``.
* ``isolation_forest`` — Isolation Forest on the same standardised features; the top
  ``contamination`` fraction of scores is flagged.

Each flagged observation is then categorised *ex post* (using the following bar, which makes
this a diagnostic, not a real-time signal):

* ``data_quality`` — a jump that fully reverses on the next bar with unremarkable volume
  (classic bad-tick signature), or a record already listed by the data-quality engine.
* ``market_behaviour`` — everything else. A market anomaly is an unusual observation, not
  evidence of wrongdoing.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.core.errors import ConfigurationError, InsufficientDataError
from app.ml.features import ANOMALY_FEATURES, anomaly_features

MODEL_FEATURES = ["ret_z", "volume_z", "range_z", "gap_z", "rv_ratio"]


def _severity_from_z(zmax: float) -> str:
    return "critical" if zmax >= 10 else "high" if zmax >= 7 else "medium"


def _category(row: pd.Series, dq_keys: set[tuple[str, str]]) -> tuple[str, str]:
    key = (row["symbol"], row["date"].date().isoformat())
    if key in dq_keys:
        return "data_quality", "record listed by the data-quality engine"
    r, nr, vz = row["ret"], row["next_ret"], row["volume_z"]
    if pd.notna(r) and pd.notna(nr) and abs(r) > 0.08:
        reverted = abs((1 + r) * (1 + nr) - 1) < 0.25 * abs(r)
        if reverted and (pd.isna(vz) or vz < 2.0):
            return "data_quality", "large move fully reversed next bar on normal volume (suspected bad tick)"
    return "market_behaviour", "unusual market observation"


def run_anomaly_experiment(
    close: pd.DataFrame,
    open_: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    volume: pd.DataFrame,
    config: dict[str, Any],
    dq_keys: set[tuple[str, str]] | None = None,
    injected: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    seed = int(config.get("seed", 42))
    contamination = float(config.get("contamination", 0.002))
    z_threshold = float(config.get("z_threshold", 6.0))
    symbols = config.get("symbols") or list(close.columns)
    if not 0.0001 <= contamination <= 0.05:
        raise ConfigurationError("contamination must be between 0.0001 and 0.05")
    if not 3 <= z_threshold <= 20:
        raise ConfigurationError("z_threshold must be between 3 and 20")
    dq_keys = dq_keys or set()

    frames = []
    for s in symbols:
        if s not in close:
            raise ConfigurationError(f"unknown symbol {s}")
        c = close[s].dropna()
        if len(c) < 120:
            continue
        f = anomaly_features(
            c, open_[s].reindex(c.index), high[s].reindex(c.index), low[s].reindex(c.index), volume[s].reindex(c.index)
        )
        f["symbol"] = s
        frames.append(f)
    if not frames:
        raise InsufficientDataError("no symbol has enough history (120 bars) for anomaly features")
    panel = pd.concat(frames).rename_axis("date").reset_index()
    if config.get("start"):
        panel = panel[panel["date"] >= pd.Timestamp(config["start"])]
    if config.get("end"):
        panel = panel[panel["date"] <= pd.Timestamp(config["end"])]
    panel = panel.dropna(subset=MODEL_FEATURES).sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)
    if len(panel) < 500:
        raise InsufficientDataError("fewer than 500 complete observations for anomaly detection")

    X = panel[MODEL_FEATURES].to_numpy(dtype=float)
    X = np.clip(X, -50, 50)
    iso = IsolationForest(n_estimators=300, contamination=contamination, random_state=seed, n_jobs=1).fit(X)
    score = -iso.score_samples(X)
    panel["if_score"] = score
    panel["if_flag"] = iso.predict(X) == -1
    zabs = np.column_stack([panel["ret_z"].abs(), panel["volume_z"], panel["range_z"].abs()])
    panel["z_max"] = np.nanmax(zabs, axis=1)
    panel["z_flag"] = panel["z_max"] > z_threshold
    pct = pd.Series(score).rank(pct=True).to_numpy()

    injected = injected or []
    inj_keys = {(e["symbol"], e["date"]) for e in injected}

    rows = []
    for method, flag_col in (("isolation_forest", "if_flag"), ("rolling_zscore", "z_flag")):
        sub = panel[panel[flag_col]]
        for i, row in sub.iterrows():
            contrib = {f: float(row[f]) for f in MODEL_FEATURES}
            top = max(("ret_z", "volume_z", "range_z", "gap_z"), key=lambda f: abs(row[f]) if pd.notna(row[f]) else -1)
            cat, reason = _category(row, dq_keys)
            if method == "isolation_forest":
                sev = "critical" if pct[i] >= 0.9995 else "high" if pct[i] >= 0.998 else "medium"
                sc = float(row["if_score"])
            else:
                sev = _severity_from_z(float(row["z_max"]))
                sc = float(row["z_max"])
            key = (row["symbol"], row["date"].date().isoformat())
            rows.append(
                {
                    "method": method,
                    "symbol": row["symbol"],
                    "date": row["date"].date(),
                    "score": sc,
                    "severity": sev,
                    "category": cat,
                    "category_reason": reason,
                    "top_feature": top,
                    "features": {**contrib, "return": _f(row["ret"]), "next_return": _f(row["next_ret"])},
                    "matches_injected_event": (key in inj_keys) if injected else None,
                }
            )

    evaluation = None
    if injected:
        ev: dict[str, Any] = {"injected_events": len(inj_keys), "methods": {}}
        in_panel = {k for k in inj_keys if ((panel["symbol"] == k[0]) & (panel["date"] == pd.Timestamp(k[1]))).any()}
        ev["injected_events_in_sample"] = len(in_panel)
        for method in ("isolation_forest", "rolling_zscore"):
            flagged = {(r["symbol"], r["date"].isoformat()) for r in rows if r["method"] == method}
            hits = flagged & in_panel
            ev["methods"][method] = {
                "flagged": len(flagged),
                "true_positives": len(hits),
                "recall": len(hits) / len(in_panel) if in_panel else None,
                "precision_vs_injected": len(hits) / len(flagged) if flagged else None,
                "missed": sorted([f"{s} {d}" for s, d in in_panel - flagged]),
            }
        ev["note"] = (
            "Precision is measured only against deliberately injected events; the generator's own fat-tailed "
            "jumps are genuine unusual observations, so flagged non-injected points are not necessarily false positives."
        )
        evaluation = ev

    by_method = {m: sum(1 for r in rows if r["method"] == m) for m in ("isolation_forest", "rolling_zscore")}
    overlap = len(
        {(r["symbol"], r["date"]) for r in rows if r["method"] == "isolation_forest"}
        & {(r["symbol"], r["date"]) for r in rows if r["method"] == "rolling_zscore"}
    )
    hist_counts, hist_edges = np.histogram(score, bins=60)
    return {
        "anomalies": rows,
        "summary": {
            "observations": len(panel),
            "symbols": int(panel["symbol"].nunique()),
            "flagged_by_method": by_method,
            "flagged_by_both": overlap,
            "data_quality_flags": sum(1 for r in rows if r["category"] == "data_quality"),
            "market_behaviour_flags": sum(1 for r in rows if r["category"] == "market_behaviour"),
            "period": [panel["date"].min().date().isoformat(), panel["date"].max().date().isoformat()],
        },
        "score_histogram": {
            "edges": hist_edges.tolist(),
            "counts": hist_counts.tolist(),
            "threshold": float(np.min(score[panel["if_flag"].to_numpy()])) if panel["if_flag"].any() else None,
        },
        "evaluation_vs_injected": evaluation,
        "features": {f: ANOMALY_FEATURES[f] for f in MODEL_FEATURES},
        "parameters": {"contamination": contamination, "z_threshold": z_threshold, "seed": seed},
    }


def _f(v: Any) -> float | None:
    return None if v is None or pd.isna(v) else float(v)
