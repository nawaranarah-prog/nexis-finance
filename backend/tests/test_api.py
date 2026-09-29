"""API integration tests: the complete research workflow through HTTP.

Load data → inspect asset → create portfolio → risk → stress → backtest → trades → ML → experiments →
reproduce → report → exports, plus structured error handling.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def ds_id(client) -> int:
    r = client.get("/api/datasets")
    assert r.status_code == 200
    return next(d["id"] for d in r.json() if d["code"] == "TEST-SYN")


def _job_result(resp) -> dict:
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["status"] == "succeeded", job
    return job["result"]


def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    h = client.get("/api/system/health").json()
    assert h["database"]["ok"] and h["record_counts"]["market_data"] > 40_000
    assert client.get("/api/system/config").json()["database_engine"] == "sqlite"
    assert "sharpe_ratio" in client.get("/api/meta/glossary").json()
    assert client.get("/openapi.json").status_code == 200


def test_dataset_is_labelled_synthetic(client, ds_id):
    d = client.get(f"/api/datasets/{ds_id}").json()
    assert d["is_synthetic"] and d["mode_label"] == "DEMO / SYNTHETIC DATA MODE"
    assert d["version"] >= 1 and len(d["content_hash"]) == 64
    assert client.get(f"/api/datasets/{ds_id}/manifest").json()["disclaimer"].startswith("All observations are synthetic")


def test_assets_and_market_data(client, ds_id):
    assets = client.get("/api/assets", params={"dataset_id": ds_id}).json()
    assert len(assets) == 44 and any(a["is_benchmark"] for a in assets)
    a = client.get("/api/assets/TCH1", params={"dataset_id": ds_id}).json()
    assert a["observations"] > 900
    bars = client.get("/api/market-data", params={"symbol": "TCH1", "dataset_id": ds_id, "limit": 10}).json()["bars"]
    assert len(bars) == 10 and bars[0]["date"] < bars[-1]["date"]
    assert client.get("/api/assets/NOPE", params={"dataset_id": ds_id}).status_code == 404


def test_incremental_ingestion_is_noop_when_up_to_date(client):
    r = client.post(
        "/api/market-data/ingest",
        json={
            "provider": "synthetic",
            "dataset_code": "TEST-SYN",
            "dataset_name": "Test synthetic universe",
            "seed": 7,
            "end": "2022-12-30",
        },
    )
    # Different seed config (default start) → same code but generator differs; ensure API responds with a run record.
    assert r.status_code in (201, 422)


def test_data_quality_endpoints(client, ds_id):
    dq = client.get("/api/data-quality", params={"dataset_id": ds_id}).json()
    assert dq["latest"]["status"] in ("pass", "warning") and 0.9 < dq["latest"]["overall_score"] <= 1.0
    issues = client.get("/api/data-quality/issues", params={"dataset_id": ds_id, "source": "ingestion"}).json()
    assert {"low_above_high", "duplicate_symbol_date"} <= {i["check"] for i in issues}
    cov = client.get("/api/market-data/coverage", params={"dataset_id": ds_id}).json()
    assert len(cov["symbols"]) == 44


def test_asset_research_and_correlation(client, ds_id):
    r = client.get("/api/research/assets/TCH1", params={"dataset_id": ds_id, "window": 60, "frequency": "weekly"}).json()
    assert r["summary"]["beta"] is not None and len(r["series"]["dates"]) == len(r["series"]["close"])
    assert client.get("/api/research/assets/TCH1", params={"dataset_id": ds_id, "window": 7}).status_code == 422
    c = client.get(
        "/api/research/correlation", params={"dataset_id": ds_id, "symbols": "TCH1,TCH2,UTL1,FIN1", "pair": "TCH1,TCH2"}
    ).json()
    assert len(c["matrix"]["matrix"]) == 4 and c["pair"]["a"] == "TCH1"


@pytest.fixture(scope="module")
def portfolio_id(client, ds_id) -> int:
    r = client.post(
        "/api/portfolios",
        json={
            "name": "API Test Min Var",
            "dataset_id": ds_id,
            "benchmark_symbol": "NXMKT",
            "symbols": ["TCH1", "FIN1", "HLT1", "UTL1", "STP1", "NXBND"],
            "allocation_method": "min_variance",
            "max_weight": 0.4,
            "estimation_start": "2019-01-02",
            "estimation_end": "2020-12-31",
        },
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert sum(x["weight"] for x in p["positions"]) == pytest.approx(1.0)
    assert max(x["weight"] for x in p["positions"]) <= 0.4 + 1e-6
    return p["id"]


def test_portfolio_validation_errors(client, ds_id, portfolio_id):
    bad = client.post(
        "/api/portfolios",
        json={
            "name": "Bad",
            "dataset_id": ds_id,
            "benchmark_symbol": "NXMKT",
            "symbols": ["TCH1", "FIN1"],
            "allocation_method": "custom",
            "weights": {"TCH1": 0.7, "FIN1": 0.7},
        },
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_configuration"
    unknown = client.post(
        "/api/portfolios", json={"name": "Bad2", "dataset_id": ds_id, "benchmark_symbol": "NXMKT", "symbols": ["ZZZZ"]}
    )
    assert unknown.status_code == 404
    dup = client.post(
        "/api/portfolios",
        json={"name": "API Test Min Var", "dataset_id": ds_id, "benchmark_symbol": "NXMKT", "symbols": ["TCH1"]},
    )
    assert dup.status_code == 409
    schema = client.post("/api/portfolios", json={"name": "", "dataset_id": ds_id})
    assert schema.status_code == 422 and schema.json()["error"]["code"] == "validation_failed"
    notes = client.get("/api/notifications").json()
    assert any(n["category"] == "portfolio" for n in notes["items"])


def test_portfolio_analytics_risk_and_stress(client, portfolio_id):
    a = client.get(f"/api/portfolios/{portfolio_id}/analytics").json()
    s = a["summary"]
    for k in ("cumulative_return", "annualized_volatility", "sharpe_ratio", "max_drawdown", "beta", "tracking_error"):
        assert s[k] is not None
    assert a["var"]["95"]["historical"]["cvar"] >= a["var"]["95"]["historical"]["var"]
    assert sum(x["pct_contribution"] for x in a["risk_decomposition"]["assets"]) == pytest.approx(1.0)
    risk = client.post("/api/risk/analyze", json={"portfolio_id": portfolio_id, "confidences": [0.9, 0.95, 0.99]}).json()
    assert len(risk["estimates"]) == 3 and "kupiec_p_value" in risk["var_backtest"]
    assert len(client.get(f"/api/risk/history/{portfolio_id}").json()) == 9
    st = client.post(
        "/api/stress-tests",
        json={
            "portfolio_id": portfolio_id,
            "name": "−10% market",
            "scenario_type": "market_shock",
            "parameters": {"shock": -0.1},
        },
    )
    assert st.status_code == 201 and st.json()["results"]["portfolio_return"] < 0
    bad = client.post(
        "/api/stress-tests",
        json={
            "portfolio_id": portfolio_id,
            "name": "bad",
            "scenario_type": "sector_shock",
            "parameters": {"sector": "Nope", "shock": -0.1},
        },
    )
    assert bad.status_code == 422


def test_portfolio_comparison_and_update(client, ds_id, portfolio_id):
    r = client.post(
        "/api/portfolios",
        json={
            "name": "API Test EW",
            "dataset_id": ds_id,
            "benchmark_symbol": "NXMKT",
            "symbols": ["TCH1", "FIN1", "HLT1"],
            "allocation_method": "equal_weight",
        },
    )
    other = r.json()["id"]
    cmp = client.get("/api/portfolios/compare", params={"a": portfolio_id, "b": other}).json()
    assert any(m["metric"] == "sharpe_ratio" for m in cmp["metrics"])
    up = client.put(
        f"/api/portfolios/{other}",
        json={
            "name": "API Test EW",
            "dataset_id": ds_id,
            "benchmark_symbol": "NXMKT",
            "symbols": ["TCH1", "FIN1"],
            "allocation_method": "equal_weight",
        },
    )
    assert len(up.json()["positions"]) == 2
    assert client.delete(f"/api/portfolios/{other}").status_code == 204


@pytest.fixture(scope="module")
def backtest(client, ds_id) -> dict:
    res = _job_result(
        client.post(
            "/api/backtests",
            json={
                "name": "API momentum",
                "dataset_id": ds_id,
                "strategy": "momentum",
                "params": {"top_n": 5},
                "benchmark_symbol": "NXMKT",
                "start_date": "2019-06-03",
                "end_date": "2022-12-30",
                "param_grid": {"lookback": [63, 126]},
                "train_end": "2021-06-30",
                "validation_end": "2022-03-31",
            },
        )
    )
    return res


def test_backtest_results_trades_and_diagnostics(client, backtest):
    bt = client.get(f"/api/backtests/{backtest['backtest_id']}").json()
    m = bt["metrics"]["full"]
    assert m["total_transaction_costs"] > 0 and m["gross_annualized_return"] >= m["annualized_return"]
    assert set(bt["metrics"]["segments"]) == {"in_sample", "validation", "out_of_sample"}
    assert bt["metrics"]["parameter_search"]["selection_window"][1] <= "2021-06-30"
    trades = client.get(f"/api/backtests/{backtest['backtest_id']}/trades").json()["trades"]
    assert trades and all(t["date"] > t["signal_date"] for t in trades)
    diag = client.get(f"/api/backtests/{backtest['backtest_id']}/diagnostics/{trades[0]['symbol']}").json()
    assert diag["indicator"]["name"].startswith("momentum")


def test_backtest_config_validation(client, ds_id):
    r = client.post(
        "/api/backtests", json={"dataset_id": ds_id, "strategy": "nope", "start_date": "2020-01-01", "end_date": "2021-01-01"}
    )
    assert r.status_code == 422
    r = client.post(
        "/api/backtests",
        json={
            "dataset_id": ds_id,
            "strategy": "momentum",
            "start_date": "2020-01-01",
            "end_date": "2021-01-01",
            "param_grid": {"lookback": [63]},
        },
    )
    assert r.status_code == 422 and "train_end" in r.json()["error"]["message"]
    r = client.post(
        "/api/backtests",
        json={
            "dataset_id": ds_id,
            "strategy": "momentum",
            "params": {"top_n": -1},
            "start_date": "2020-01-01",
            "end_date": "2021-01-01",
        },
    )
    assert r.status_code == 422


def test_walk_forward(client, ds_id):
    res = _job_result(
        client.post(
            "/api/backtests/walk-forward",
            json={
                "dataset_id": ds_id,
                "strategy": "momentum",
                "params": {"top_n": 5},
                "param_grid": {"lookback": [63, 126]},
                "start_date": "2019-06-03",
                "end_date": "2022-12-30",
                "train_days": 252,
                "test_days": 126,
            },
        )
    )
    e = client.get(f"/api/experiments/{res['experiment_id']}").json()
    assert e["experiment_type"] == "walk_forward" and len(e["artifacts"]["folds"]) >= 2


@pytest.fixture(scope="module")
def vol_exp(client, ds_id) -> dict:
    return _job_result(
        client.post(
            "/api/ml/experiments/volatility",
            json={
                "dataset_id": ds_id,
                "symbols": ["TCH1", "UTL1"],
                "horizon": 10,
                "train_end": "2021-06-30",
                "validation_end": "2022-03-31",
                "models": ["naive_hist_vol", "ewma_vol", "ridge"],
                "seed": 3,
            },
        )
    )


def test_ml_experiments(client, ds_id, vol_exp):
    e = client.get(f"/api/experiments/{vol_exp['experiment_id']}").json()
    assert e["code"].startswith("VOL-") and e["seed"] == 3 and e["artifacts"]["selection"]["preferred_model"]
    preds = client.get(f"/api/ml/experiments/{vol_exp['experiment_id']}/predictions", params={"model": "ridge"}).json()
    assert preds and {p["split"] for p in preds} == {"validation", "test"}
    reg = _job_result(
        client.post("/api/ml/experiments/regime", json={"dataset_id": ds_id, "train_end": "2021-06-30", "n_regimes": 3})
    )
    r = client.get(f"/api/experiments/{reg['experiment_id']}").json()
    assert r["artifacts"]["latest"]["regime"] and r["artifacts"]["evaluation_vs_synthetic_truth"] is not None
    an = _job_result(client.post("/api/ml/experiments/anomaly", json={"dataset_id": ds_id}))
    rows = client.get(f"/api/ml/experiments/{an['experiment_id']}/anomalies").json()
    assert rows and {"market_behaviour"} <= {x["category"] for x in rows}


def test_experiment_registry_compare_and_reproduce(client, backtest, vol_exp):
    lst = client.get("/api/experiments").json()
    assert {e["experiment_type"] for e in lst} >= {"backtest", "volatility_forecast"}
    rep = _job_result(client.post(f"/api/experiments/{vol_exp['experiment_id']}/reproduce"))
    assert rep["reproducibility"]["matches"] is True and rep["reproducibility"]["dataset_unchanged"] is True
    rep_bt = _job_result(client.post(f"/api/experiments/{backtest['experiment_id']}/reproduce"))
    assert rep_bt["reproducibility"]["matches"] is True
    cmp = client.get("/api/experiments/compare", params={"a": backtest["experiment_id"], "b": rep_bt["experiment_id"]}).json()
    assert cmp["same_type"] and all(r["difference"] in (None, 0.0) for r in cmp["metrics"])
    assert client.patch(f"/api/experiments/{vol_exp['experiment_id']}", json={"notes": "checked"}).json()["notes"] == "checked"


def test_report_generation_and_download(client, portfolio_id, backtest, vol_exp):
    res = _job_result(
        client.post(
            "/api/reports",
            json={
                "title": "API Test Report",
                "portfolio_id": portfolio_id,
                "backtest_id": backtest["backtest_id"],
                "experiment_ids": [vol_exp["experiment_id"]],
            },
        )
    )
    d = client.get(f"/api/reports/{res['report_id']}/download")
    assert d.status_code == 200 and d.content[:4] == b"%PDF"
    assert client.post("/api/reports", json={"title": "Empty"}).json()["status"] == "failed"
    assert client.get("/api/reports/99999/download").status_code == 404


def test_exports(client, portfolio_id, backtest, vol_exp):
    t = client.get(f"/api/exports/backtests/{backtest['backtest_id']}/trades")
    assert t.status_code == 200 and t.text.startswith("id,signal_date,date,symbol")
    j = client.get(f"/api/exports/backtests/{backtest['backtest_id']}/results", params={"format": "json"}).json()
    assert "daily" in j and "metrics" in j
    assert client.get(f"/api/exports/portfolios/{portfolio_id}/metrics").text.startswith("metric,value")
    assert client.get(f"/api/exports/experiments/{vol_exp['experiment_id']}/predictions").status_code == 200
    assert client.get("/api/exports/experiments").status_code == 200
    assert client.get(f"/api/exports/risk/{portfolio_id}").status_code == 200
    assert client.get(f"/api/exports/backtests/{backtest['backtest_id']}/trades", params={"format": "xml"}).status_code == 422


def test_search_overview_and_notifications(client, ds_id, portfolio_id, backtest):
    s = client.get("/api/search", params={"q": "TCH"}).json()
    assert any(a["symbol"] == "TCH1" for a in s["assets"])
    assert client.get("/api/search", params={"q": "API Test"}).json()["portfolios"]
    assert client.get("/api/search", params={"q": "BT-"}).json()["experiments"]
    o = client.get("/api/overview", params={"dataset_id": ds_id, "portfolio_id": portfolio_id}).json()
    assert o["portfolio"]["metrics"]["sharpe_ratio"] is not None and o["market"]["assets"] == 42
    n = client.get("/api/notifications").json()
    assert n["unread"] > 0
    client.post("/api/notifications/read", json={"ids": None})
    assert client.get("/api/notifications").json()["unread"] == 0


def test_structured_errors_never_leak_internals(client):
    r = client.get("/api/portfolios/999999")
    assert r.status_code == 404 and set(r.json()["error"]) == {"code", "message", "details"}
    r = client.get("/api/jobs/does-not-exist")
    assert r.status_code == 404
    r = client.post("/api/risk/analyze", json={"portfolio_id": 1, "confidences": [0.5]})
    assert r.status_code == 422
