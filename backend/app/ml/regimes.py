"""Market regime classification by unsupervised clustering.

The model (Gaussian mixture or K-means on standardised features) is fitted on the training
period only; later dates are classified with the frozen model and feature scaler, so the regime
assigned to a date uses only information available at that date.

Cluster names are *descriptive labels derived from each cluster's mean features* (volatility
above/below the training median; momentum sign). They are conventions of this model, not
universal market states, and they are not forecasts.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from app.core.errors import ConfigurationError, InsufficientDataError
from app.ml.features import REGIME_FEATURES


def _label_clusters(centers: pd.DataFrame, vol_median: float) -> dict[int, str]:
    labels: dict[int, str] = {}
    for k, row in centers.iterrows():
        vol = "High Volatility" if row["rv_20"] > vol_median else "Low Volatility"
        mom = "Positive Momentum" if row["mom_60"] >= 0 else "Negative Momentum"
        labels[int(k)] = f"{vol} · {mom}"
    # Disambiguate duplicates by ordering on volatility.
    seen: dict[str, list[int]] = {}
    for k, name in labels.items():
        seen.setdefault(name, []).append(k)
    for name, ks in seen.items():
        if len(ks) > 1:
            ks_sorted = sorted(ks, key=lambda k: centers.loc[k, "rv_20"])
            for rank, k in enumerate(ks_sorted, 1):
                labels[k] = f"{name} ({rank})"
    return labels


def run_regime_experiment(
    bench_close: pd.Series, universe_returns: pd.DataFrame | None, config: dict[str, Any], true_regimes: pd.Series | None = None
) -> dict[str, Any]:
    from app.ml.features import regime_features

    seed = int(config.get("seed", 42))
    k = int(config.get("n_regimes", 4))
    method = config.get("method", "gmm")
    feats = config.get("features") or list(REGIME_FEATURES)
    if not 2 <= k <= 8:
        raise ConfigurationError("n_regimes must be between 2 and 8")
    if method not in ("gmm", "kmeans"):
        raise ConfigurationError("method must be 'gmm' or 'kmeans'")
    unknown = set(feats) - set(REGIME_FEATURES)
    if unknown:
        raise ConfigurationError(f"unknown regime features {sorted(unknown)}")
    if "rv_20" not in feats or "mom_60" not in feats:
        raise ConfigurationError("rv_20 and mom_60 are required: they define the descriptive regime labels")

    f = regime_features(bench_close, universe_returns if "avg_corr_60" in feats else None)[feats].dropna()
    if config.get("start"):
        f = f[f.index >= pd.Timestamp(config["start"])]
    if config.get("end"):
        f = f[f.index <= pd.Timestamp(config["end"])]
    train_end = pd.Timestamp(config["train_end"])
    train = f[f.index <= train_end]
    if len(train) < 250:
        raise InsufficientDataError(f"regime model needs >= 250 training days; got {len(train)}")
    scaler = StandardScaler().fit(train.to_numpy())
    Xtr = scaler.transform(train.to_numpy())
    Xall = scaler.transform(f.to_numpy())

    bic = []
    if method == "gmm":
        for kk in range(2, 7):
            g = GaussianMixture(kk, covariance_type="full", random_state=seed, n_init=3).fit(Xtr)
            bic.append({"k": kk, "bic": float(g.bic(Xtr)), "aic": float(g.aic(Xtr))})
        model = GaussianMixture(k, covariance_type="full", random_state=seed, n_init=5).fit(Xtr)
        states = model.predict(Xall)
        proba = model.predict_proba(Xall)
    else:
        model = KMeans(k, random_state=seed, n_init=10).fit(Xtr)
        states = model.predict(Xall)
        d = model.transform(Xall)
        proba = np.exp(-d) / np.exp(-d).sum(axis=1, keepdims=True)  # soft assignment for display only

    centers = pd.DataFrame(scaler.inverse_transform(model.means_ if method == "gmm" else model.cluster_centers_), columns=feats)
    names = _label_clusters(centers, float(train["rv_20"].median()))
    labels = pd.Series([names[s] for s in states], index=f.index)

    # Empirical transition matrix from the daily label sequence.
    order = sorted(set(names.values()), key=lambda n: centers.loc[next(kk for kk, v in names.items() if v == n), "rv_20"])
    trans = (
        pd.crosstab(labels.iloc[:-1].to_numpy(), labels.iloc[1:].to_numpy(), normalize="index")
        .reindex(index=order, columns=order)
        .fillna(0.0)
    )

    # Run lengths
    runs = []
    cur, start_d, n = labels.iloc[0], labels.index[0], 0
    prev = start_d
    for d, lab in labels.items():
        if lab != cur:
            runs.append({"regime": cur, "start": start_d.date().isoformat(), "end": prev.date().isoformat(), "days": n})
            cur, start_d, n = lab, d, 0
        n += 1
        prev = d
    runs.append({"regime": cur, "start": start_d.date().isoformat(), "end": prev.date().isoformat(), "days": n})

    fwd = bench_close.pct_change(fill_method=None).shift(-1).reindex(f.index)  # ex-post, descriptive only
    stats = []
    for name in order:
        m = labels == name
        dur = [r["days"] for r in runs if r["regime"] == name]
        stats.append(
            {
                "regime": name,
                "days": int(m.sum()),
                "frequency": float(m.mean()),
                "frequency_train": float((labels[labels.index <= train_end] == name).mean()),
                "average_duration_days": float(np.mean(dur)) if dur else None,
                "feature_means": {c: float(f.loc[m, c].mean()) for c in feats},
                "next_day_benchmark_return_mean": float(fwd[m].mean()) if m.any() else None,
                "next_day_benchmark_return_std": float(fwd[m].std()) if m.sum() > 1 else None,
            }
        )

    evaluation = None
    if true_regimes is not None:
        tr = true_regimes.reindex(f.index).dropna()
        if len(tr):
            pred = labels.reindex(tr.index)
            evaluation = {
                "adjusted_rand_index": float(adjusted_rand_score(tr.to_numpy(), pred.to_numpy())),
                "contingency": pd.crosstab(pred, tr).to_dict(),
                "note": (
                    "Synthetic ground truth is available only because the data are generated; "
                    "real markets have no observable true regime."
                ),
            }

    last = f.index[-1]
    return {
        "method": method,
        "n_regimes": k,
        "features": feats,
        "train_period": [train.index[0].date().isoformat(), train.index[-1].date().isoformat()],
        "classification_period": [f.index[0].date().isoformat(), last.date().isoformat()],
        "labels": names,
        "order": order,
        "timeline": {
            "dates": [d.date().isoformat() for d in f.index],
            "regime": labels.tolist(),
            "in_sample": (f.index <= train_end).tolist(),
            "benchmark": bench_close.reindex(f.index).round(4).tolist(),
        },
        "probabilities": {names[j]: proba[:, j].round(4).tolist() for j in range(proba.shape[1])},
        "feature_series": {c: f[c].round(6).tolist() for c in feats},
        "transition_matrix": {"labels": order, "matrix": trans.to_numpy().round(4).tolist()},
        "regime_stats": stats,
        "runs": runs[-200:],
        "model_selection": bic,
        "latest": {
            "date": last.date().isoformat(),
            "regime": labels.iloc[-1],
            "probabilities": {names[j]: float(proba[-1, j]) for j in range(proba.shape[1])},
        },
        "evaluation_vs_synthetic_truth": evaluation,
        "centers": centers.round(6).to_dict(orient="records"),
    }
