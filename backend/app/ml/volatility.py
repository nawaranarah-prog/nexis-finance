"""Volatility forecasting experiment.

Target: annualised realised volatility over the next ``horizon`` trading days.
Models are compared against two no-fit baselines (trailing realised vol and EWMA). A fitted
model is only marked *preferred* if it improves on the best baseline's validation RMSE by more
than ``min_improvement`` — otherwise the simpler baseline is preferred.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.inspection import partial_dependence, permutation_importance
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.core.errors import ConfigurationError, InsufficientDataError
from app.ml.features import VOL_FEATURES, build_volatility_panel
from app.ml.splits import purged_split

MODEL_NAMES = ("naive_hist_vol", "ewma_vol", "ridge", "random_forest", "gradient_boosting")
BASELINES = ("naive_hist_vol", "ewma_vol")


def regression_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float | None]:
    if len(y) == 0:
        return {"mae": None, "rmse": None, "r2": None, "qlike": None, "n": 0}
    ys, ps = np.maximum(y, 1e-6), np.maximum(p, 1e-6)
    # QLIKE loss on variances: robust to noise in the volatility proxy (Patton, 2011).
    qlike = float(np.mean(ys**2 / ps**2 - np.log(ys**2 / ps**2) - 1))
    return {
        "mae": float(mean_absolute_error(y, p)),
        "rmse": float(math.sqrt(mean_squared_error(y, p))),
        "r2": float(r2_score(y, p)) if len(y) > 1 else None,
        "qlike": qlike,
        "n": len(y),
    }


def _make_model(name: str, seed: int) -> Any:
    if name == "ridge":
        return make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    if name == "random_forest":
        return RandomForestRegressor(
            n_estimators=200, max_depth=8, min_samples_leaf=50, max_features=0.5, random_state=seed, n_jobs=1
        )
    if name == "gradient_boosting":
        return HistGradientBoostingRegressor(
            max_depth=4,
            learning_rate=0.05,
            max_iter=300,
            min_samples_leaf=50,
            l2_regularization=1.0,
            early_stopping=False,
            random_state=seed,
        )
    raise ConfigurationError(f"unknown model '{name}'")


def run_volatility_experiment(
    close: pd.DataFrame,
    high: pd.DataFrame,
    low: pd.DataFrame,
    volume: pd.DataFrame,
    bench_close: pd.Series | None,
    config: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    symbols: list[str] = config["symbols"]
    horizon = int(config.get("horizon", 10))
    seed = int(config.get("seed", 42))
    features = config.get("features") or list(VOL_FEATURES)
    models = config.get("models") or list(MODEL_NAMES)
    min_improvement = float(config.get("min_improvement", 0.02))
    unknown = set(features) - set(VOL_FEATURES)
    if unknown:
        raise ConfigurationError(f"unknown features: {sorted(unknown)}", details={"available": list(VOL_FEATURES)})
    if not 1 <= horizon <= 63:
        raise ConfigurationError("horizon must be between 1 and 63 trading days")
    if not symbols or len(symbols) > 20:
        raise ConfigurationError("select between 1 and 20 symbols")

    panel = build_volatility_panel(close, high, low, volume, bench_close, symbols, horizon)
    if panel.empty:
        raise InsufficientDataError("no symbol has enough history for feature construction")
    start, end = config.get("start"), config.get("end")
    if start:
        panel = panel[panel["date"] >= pd.Timestamp(start)]
    if end:
        panel = panel[panel["date"] <= pd.Timestamp(end)]
    panel = panel.dropna(subset=[*features, "naive_hist_vol", "target"]).reset_index(drop=True)
    if len(panel) < 300:
        raise InsufficientDataError(f"only {len(panel)} complete rows after feature construction; need 300")

    split = purged_split(panel["date"], config["train_end"], config.get("validation_end"), horizon)
    X = panel[features].to_numpy(dtype=float)
    y = panel["target"].to_numpy(dtype=float)
    if progress:
        progress(0.1, "features built")

    preds: dict[str, np.ndarray] = {}
    fitted: dict[str, Any] = {}
    for i, name in enumerate(models):
        if name not in MODEL_NAMES:
            raise ConfigurationError(f"unknown model '{name}'", details={"available": MODEL_NAMES})
        if name == "naive_hist_vol":
            preds[name] = panel["naive_hist_vol"].to_numpy(dtype=float)
        elif name == "ewma_vol":
            preds[name] = panel["ewma_vol"].to_numpy(dtype=float) if "ewma_vol" in panel else panel["naive_hist_vol"].to_numpy()
        else:
            m = _make_model(name, seed)
            m.fit(X[split.train], y[split.train])  # fitted on the purged training period only
            fitted[name] = m
            preds[name] = m.predict(X)
        if progress:
            progress(0.1 + 0.6 * (i + 1) / len(models), f"model {name} done")

    metrics: dict[str, dict[str, Any]] = {}
    for name, p in preds.items():
        metrics[name] = {
            "train": regression_metrics(y[split.train], p[split.train]),
            "validation": regression_metrics(y[split.validation], p[split.validation]),
            "test": regression_metrics(y[split.test], p[split.test]),
        }

    sel_split = "validation" if split.validation.any() else "train"
    baseline_rmse = {b: metrics[b][sel_split]["rmse"] for b in BASELINES if b in metrics}
    best_baseline = min(baseline_rmse, key=lambda k: baseline_rmse[k]) if baseline_rmse else None
    fitted_rmse = {k: metrics[k][sel_split]["rmse"] for k in fitted}
    best_fitted = min(fitted_rmse, key=lambda k: fitted_rmse[k]) if fitted_rmse else None
    preferred = best_baseline or best_fitted
    improvement = None
    if best_baseline and best_fitted:
        improvement = 1 - fitted_rmse[best_fitted] / baseline_rmse[best_baseline]
        preferred = best_fitted if improvement > min_improvement else best_baseline

    # --- Explainability (on the test split, never training data) -------------------------------------
    explain: dict[str, Any] = {}
    Xte, yte = X[split.test], y[split.test]
    rng = np.random.RandomState(seed)
    sample = rng.choice(len(Xte), size=min(len(Xte), 3000), replace=False) if len(Xte) else np.array([], int)
    for name, m in fitted.items():
        entry: dict[str, Any] = {}
        est = m[-1] if hasattr(m, "steps") else m
        if hasattr(est, "feature_importances_"):
            entry["impurity_importance"] = dict(zip(features, map(float, est.feature_importances_), strict=True))
        if hasattr(est, "coef_"):
            entry["standardized_coefficients"] = dict(zip(features, map(float, est.coef_), strict=True))
        if len(sample) > 50:
            pi = permutation_importance(
                m, Xte[sample], yte[sample], n_repeats=5, random_state=seed, scoring="neg_root_mean_squared_error"
            )
            entry["permutation_importance"] = {
                f: {"mean": float(pi.importances_mean[j]), "std": float(pi.importances_std[j])} for j, f in enumerate(features)
            }
        explain[name] = entry
    if progress:
        progress(0.85, "explainability computed")

    pdp: dict[str, Any] = {}
    tree_models = [k for k in ("gradient_boosting", "random_forest") if k in fitted]
    if tree_models and len(sample) > 50:
        tm = tree_models[0]
        pim = explain[tm].get("permutation_importance", {})
        top = sorted(pim, key=lambda f: pim[f]["mean"], reverse=True)[:3]
        for f in top:
            j = features.index(f)
            res = partial_dependence(fitted[tm], Xte[sample], [j], grid_resolution=20, kind="average")
            pdp[f] = {"grid": [float(v) for v in res["grid_values"][0]], "average": [float(v) for v in res["average"][0]]}
        pdp = {"model": tm, "features": pdp}

    split_label = np.where(split.train, "train", np.where(split.validation, "validation", np.where(split.test, "test", "purged")))
    pred_rows = []
    keep = split_label != "train"
    for name, p in preds.items():
        for idx in np.nonzero(keep)[0]:
            if split_label[idx] == "purged":
                continue
            pred_rows.append(
                {
                    "model": name,
                    "symbol": panel.at[idx, "symbol"],
                    "date": panel.at[idx, "date"].date(),
                    "split": split_label[idx],
                    "y_true": float(y[idx]),
                    "y_pred": float(p[idx]),
                }
            )

    return {
        "metrics": metrics,
        "selection": {
            "selection_split": sel_split,
            "best_baseline": best_baseline,
            "best_fitted_model": best_fitted,
            "rmse_improvement_vs_baseline": improvement,
            "min_improvement": min_improvement,
            "preferred_model": preferred,
            "rule": "fitted model preferred only if validation RMSE improves on the best baseline by more than min_improvement",
        },
        "explainability": explain,
        "partial_dependence": pdp,
        "split": {
            "train": split.train_dates,
            "validation": split.validation_dates,
            "test": split.test_dates,
            "purged_rows": split.purged_rows,
            "horizon": horizon,
            "rows": {"train": int(split.train.sum()), "validation": int(split.validation.sum()), "test": int(split.test.sum())},
        },
        "features": features,
        "models": list(preds),
        "predictions": pred_rows,
        "duration_seconds": time.perf_counter() - t0,
    }
