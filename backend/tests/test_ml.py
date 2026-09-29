"""ML pipeline: point-in-time features, leakage-free splits, reproducibility and metrics."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.core.errors import ConfigurationError
from app.ml.anomalies import run_anomaly_experiment
from app.ml.features import anomaly_features, asset_features, forward_realized_vol, regime_features
from app.ml.regimes import run_regime_experiment
from app.ml.splits import purged_split
from app.ml.volatility import regression_metrics, run_volatility_experiment
from app.services.experiments import _jsonable

SYMS = ["TCH1", "UTL1"]


def test_features_are_point_in_time(panel):
    s = "TCH1"
    c, h, lo, v = (panel[k][s].dropna() for k in ("close", "high", "low", "volume"))
    b = panel["close"]["NXMKT"]
    full = asset_features(c, h, lo, v, b)
    for t in (150, 400, 700):
        trunc = asset_features(c.iloc[: t + 1], h.iloc[: t + 1], lo.iloc[: t + 1], v.iloc[: t + 1], b.loc[: c.index[t]])
        pd.testing.assert_series_equal(full.iloc[t], trunc.iloc[-1], check_names=False, atol=1e-12)


def test_anomaly_model_features_are_point_in_time(panel):
    s = "FIN1"
    args = [panel[k][s].dropna() for k in ("close", "open", "high", "low", "volume")]
    full = anomaly_features(*args)
    t = 500
    trunc = anomaly_features(*[a.iloc[: t + 1] for a in args])
    cols = ["ret_z", "volume_z", "range_z", "gap_z", "rv_ratio"]  # next_ret is diagnostic-only, not a model input
    pd.testing.assert_series_equal(full[cols].iloc[t], trunc[cols].iloc[-1], check_names=False, atol=1e-12)


def test_forward_target_definition():
    idx = pd.bdate_range("2024-01-01", periods=10)
    c = pd.Series([100, 101, 99, 102, 103, 101, 100, 104, 105, 103], index=idx, dtype=float)
    tgt = forward_realized_vol(c, 3)
    lr = np.log(c / c.shift(1))
    assert tgt.iloc[2] == pytest.approx(lr.iloc[3:6].std() * math.sqrt(252))
    assert tgt.iloc[-3:].isna().all()  # the last `horizon` rows have no complete future window


def test_purged_split_has_no_overlap_and_purges_horizon():
    dates = pd.Series(np.repeat(pd.bdate_range("2020-01-01", periods=400), 2))  # 2 assets per date
    sp = purged_split(dates, "2020-12-31", "2021-03-31", horizon=5)
    d = pd.DatetimeIndex(dates)
    assert d[sp.train].max() < d[sp.validation].min() < d[sp.test].min()
    # Last 5 trading days before train_end are purged (their 5-day targets overlap validation).
    last_train_day = pd.bdate_range("2020-01-01", "2020-12-31")[-6]
    assert d[sp.train].max() == last_train_day
    assert sp.purged_rows == 2 * 5 * 2  # 5 days × 2 assets at each of the two boundaries
    assert not (sp.train & sp.test).any() and not (sp.train & sp.validation).any()


def test_split_refuses_unsorted_data():
    dates = pd.Series(pd.bdate_range("2020-01-01", periods=300)).sample(frac=1, random_state=0)
    with pytest.raises(ConfigurationError, match="chronologically"):
        purged_split(dates, "2020-06-30", None, 5)


def test_regression_metrics_known_values():
    m = regression_metrics(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 4.0]))
    assert m["mae"] == pytest.approx(1 / 3) and m["rmse"] == pytest.approx(math.sqrt(1 / 3))
    assert m["r2"] == pytest.approx(1 - 1 / 2)
    assert regression_metrics(np.array([0.2, 0.3]), np.array([0.2, 0.3]))["qlike"] == pytest.approx(0.0)


def _vol_cfg(**kw):
    return {
        "symbols": SYMS,
        "horizon": 10,
        "train_end": "2021-06-30",
        "validation_end": "2022-03-31",
        "seed": 11,
        "models": ["naive_hist_vol", "ewma_vol", "ridge", "random_forest"],
        **kw,
    }


@pytest.fixture(scope="module")
def vol_result(panel):
    return run_volatility_experiment(
        panel["close"], panel["high"], panel["low"], panel["volume"], panel["close"]["NXMKT"], _vol_cfg()
    )


def test_volatility_experiment_shapes_and_selection(vol_result):
    r = vol_result
    rows = r["split"]["rows"]
    n_models = len(r["models"])
    assert len(r["predictions"]) == n_models * (rows["validation"] + rows["test"])
    assert {p["split"] for p in r["predictions"]} == {"validation", "test"}
    assert r["selection"]["preferred_model"] in r["models"]
    assert r["split"]["train"][1] < r["split"]["validation"][0] < r["split"]["test"][0]
    for m in r["metrics"].values():
        assert m["test"]["rmse"] > 0


def test_volatility_experiment_reproducible_with_seed(panel, vol_result):
    again = run_volatility_experiment(
        panel["close"], panel["high"], panel["low"], panel["volume"], panel["close"]["NXMKT"], _vol_cfg()
    )
    assert again["metrics"] == vol_result["metrics"]
    assert again["selection"] == vol_result["selection"]


def test_volatility_training_ignores_test_period_targets(panel, vol_result):
    """Scrambling prices after validation_end must not change train/validation metrics (no leakage)."""
    close = panel["close"].copy()
    later = close.index > pd.Timestamp("2022-06-30")
    rng = np.random.default_rng(1)
    close.loc[later] = close.loc[later] * rng.uniform(0.8, 1.2, close.loc[later].shape)
    res = run_volatility_experiment(close, panel["high"], panel["low"], panel["volume"], close["NXMKT"], _vol_cfg())
    for model in vol_result["metrics"]:
        assert res["metrics"][model]["train"] == vol_result["metrics"][model]["train"]


def test_unknown_feature_rejected(panel):
    with pytest.raises(ConfigurationError):
        run_volatility_experiment(
            panel["close"], panel["high"], panel["low"], panel["volume"], None, _vol_cfg(features=["future_return"])
        )


def test_regime_model_fit_on_training_only(panel):
    bench = panel["close"]["NXMKT"]
    uni = panel["close"].drop(columns=["NXMKT", "NXBND"]).pct_change(fill_method=None)
    cfg = {"n_regimes": 3, "train_end": "2021-06-30", "seed": 3}
    a = run_regime_experiment(bench, uni, cfg)
    b = run_regime_experiment(bench, uni, cfg)
    assert a["timeline"]["regime"] == b["timeline"]["regime"]  # reproducible
    # Perturb post-training data: labels inside the training window must not change.
    bench2 = bench.copy()
    bench2.loc[bench2.index > pd.Timestamp("2021-06-30")] *= 0.7
    c = run_regime_experiment(bench2, uni, cfg)
    n_train = sum(a["timeline"]["in_sample"])
    assert a["timeline"]["regime"][:n_train] == c["timeline"]["regime"][:n_train]
    tm = np.array(a["transition_matrix"]["matrix"])
    assert np.allclose(tm.sum(axis=1), 1.0, atol=1e-3)  # stored rounded to 4 dp


def test_regime_features_point_in_time(panel):
    bench = panel["close"]["NXMKT"]
    full = regime_features(bench, None)
    part = regime_features(bench.iloc[:600], None)
    pd.testing.assert_series_equal(full.iloc[599], part.iloc[-1], check_names=False)


def test_anomaly_detection_finds_injected_events_and_is_reproducible(panel, universe):
    syms = [c for c in panel["close"].columns if c not in ("NXMKT", "NXBND")]
    injected = universe.manifest["market_events"] + [d for d in universe.manifest["data_defects"] if d["type"] == "bad_tick"]
    args = (panel["close"], panel["open"], panel["high"], panel["low"], panel["volume"])
    a = run_anomaly_experiment(*args, {"symbols": syms, "seed": 5}, set(), injected)
    b = run_anomaly_experiment(*args, {"symbols": syms, "seed": 5}, set(), injected)
    assert [(r["symbol"], r["date"], r["score"]) for r in a["anomalies"]] == [
        (r["symbol"], r["date"], r["score"]) for r in b["anomalies"]
    ]
    ev = a["evaluation_vs_injected"]
    assert ev["methods"]["rolling_zscore"]["recall"] >= 0.5
    # Bad ticks (reverting next bar on normal volume) are classified as data-quality anomalies.
    bad = {(d["symbol"], d["date"]) for d in universe.manifest["data_defects"] if d["type"] == "bad_tick"}
    flagged_bad = [r for r in a["anomalies"] if (r["symbol"], r["date"].isoformat()) in bad]
    assert flagged_bad and all(r["category"] == "data_quality" for r in flagged_bad)


def test_experiment_serialisation_handles_numpy_and_nan():
    obj = {"a": np.float64(1.5), "b": np.int64(3), "c": float("nan"), "d": [np.bool_(True)], "e": pd.Timestamp("2024-01-02")}
    out = _jsonable(obj)
    assert out == {"a": 1.5, "b": 3, "c": None, "d": [True], "e": "2024-01-02"}
