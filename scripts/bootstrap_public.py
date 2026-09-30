"""Initialise a deployed public instance through its REST API.

Usage:
    python scripts/bootstrap_public.py https://nexis-finance-api.vercel.app

Runs the same plan as ``seed_live.py`` (see ``live_plan.py``) but only with the HTTP requests the
web UI itself makes — useful when the database is not directly reachable (serverless hosts,
firewalled Postgres ports). Safe to re-run: existing connections are re-synced incrementally and
research that already exists is skipped. Needs only ``httpx``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_plan as plan

TERMINAL = {"succeeded", "failed", "cancelled"}


class Api:
    def __init__(self, base: str) -> None:
        self.c = httpx.Client(base_url=base.rstrip("/") + "/api", timeout=310.0)

    def req(self, method: str, path: str, **kw: Any) -> Any:
        r = self.c.request(method, path, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> HTTP {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else None

    def get(self, path: str, **kw: Any) -> Any:
        return self.req("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.req("POST", path, **kw)

    def job(self, j: dict[str, Any]) -> dict[str, Any]:
        """Wait for a job (jobs run inline on serverless hosts, so this usually returns at once)."""
        while j["status"] not in TERMINAL:
            time.sleep(2)
            j = self.get(f"/jobs/{j['id']}")
        if j["status"] != "succeeded":
            raise SystemExit(f"job {j['job_type']} {j['status']}: {j.get('error')}")
        return j["result"]


def items(x: Any) -> list[dict[str, Any]]:
    return x["items"] if isinstance(x, dict) and "items" in x else x


def step(msg: str) -> float:
    print(f"\n==> {msg}", flush=True)
    return time.perf_counter()


def done(t0: float, extra: str = "") -> None:
    print(f"    done in {time.perf_counter() - t0:.1f}s {extra}", flush=True)


def ensure_connection(api: Api, key: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    live = [c for c in api.get("/connections") if c["provider_key"] == key and c["status"] != "disconnected"]
    if live:
        c = api.req("PATCH", f"/connections/{live[0]['id']}", json={"config": config}) if config else live[0]
    else:
        c = api.post("/connections", json={"provider_key": key, **({"config": config} if config else {})})
    if c["status"] != "connected":
        print(f"    {key}: {c['status']} ({c.get('last_error')})")
        return None
    return c


def sync(api: Api, c: dict[str, Any]) -> dict[str, Any]:
    return api.job(api.post(f"/connections/{c['id']}/sync"))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    api = Api(sys.argv[1])
    print("API:", api.get("/health"))

    n = len(plan.EQUITIES) + len(plan.ETFS) + 1
    t = step(f"Market data: {n} real symbols from {plan.START} (Yahoo Finance chart endpoint)")
    c = ensure_connection(api, "yahoo_market", plan.MARKET_CONFIG)
    if c is None:
        raise SystemExit("market data provider unavailable — nothing to build on")
    r = sync(api, c)
    done(t, f"{r.get('records_added', 0):,} bars added")

    t = step("SEC EDGAR: company profiles, SIC industries and XBRL fundamentals")
    c = ensure_connection(api, "sec_edgar", plan.SEC_CONFIG)
    if c is not None:
        r = sync(api, c)
        done(t, f"{r.get('records_added', 0):,} records added")

    t = step("Economic data: US Treasury yield curve and World Bank indicators")
    for key in ("us_treasury", "world_bank"):
        c = ensure_connection(api, key)
        if c is not None:
            print(f"    {key}: {sync(api, c).get('records_added', 0):,} added")
    done(t)

    ds = next(d for d in api.get("/datasets") if d["code"] == "LIVE-MARKET")
    end = ds["end_date"]

    t = step("Data-quality assessment")
    dq = api.post("/data-quality/run", params={"dataset_id": ds["id"]})
    done(t, f"score={dq.get('overall_score', 0):.2%} status={dq.get('status')}")

    t = step("Portfolios (weights estimated 2017–2021, evaluated out of sample afterwards)")
    have = {p["name"]: p for p in items(api.get("/portfolios"))}
    created = [have.get(spec["name"]) or api.post("/portfolios", json=spec) for spec in plan.portfolios(ds["id"])]
    for p in created:
        print(f"    {p['name']} (id {p['id']})")
    done(t)

    t = step("Portfolio analytics, risk and stress tests")
    for p in created:
        api.get(f"/portfolios/{p['id']}/analytics")
    api.post("/risk/analyze", json={"portfolio_id": created[0]["id"], "confidences": [0.95, 0.99], "lookback_days": 756})
    presets = api.get("/stress-tests/presets")["presets"]
    st_ids = [
        api.post(
            "/stress-tests",
            json={"portfolio_id": created[3]["id"], **{k: pr[k] for k in ("name", "scenario_type", "parameters")}},
        )["id"]
        for pr in presets
    ]
    done(t, f"{len(st_ids)} scenarios")

    results: dict[str, dict[str, Any]] = {}
    have_bt = {b["name"] for b in items(api.get("/backtests"))}
    for kind, cfg in plan.backtests(ds["id"], end):
        if cfg["name"] in have_bt:
            continue
        t = step(f"{'Walk-forward' if kind == 'walk_forward' else 'Backtest'}: {cfg['name']}")
        results[cfg["name"]] = api.job(api.post("/backtests/walk-forward" if kind == "walk_forward" else "/backtests", json=cfg))
        done(t, results[cfg["name"]].get("experiment_code", ""))

    have_exp = {e["name"] for e in items(api.get("/experiments"))}
    for kind, cfg in plan.ml_experiments(ds["id"]):
        if cfg["name"] in have_exp:
            continue
        t = step(f"ML: {cfg['name']}")
        results[cfg["name"]] = api.job(api.post(f"/ml/experiments/{kind}", json=cfg))
        done(t, results[cfg["name"]].get("experiment_code", ""))

    mom = results.get("Momentum top-8, IS-selected lookback")
    if mom:
        t = step("Research report (PDF)")
        exp_ids = [r["experiment_id"] for r in results.values() if "experiment_id" in r and r is not mom]
        rep = api.job(
            api.post(
                "/reports",
                json={
                    "title": plan.REPORT_TITLE,
                    "portfolio_id": created[0]["id"],
                    "backtest_id": mom["backtest_id"],
                    "experiment_ids": exp_ids[:4],
                    "stress_test_ids": st_ids[:3],
                    "notes": plan.REPORT_NOTES,
                },
            )
        )
        done(t, f"report {rep.get('report_id')}")
    print(f"\nPublic instance ready (market data through {end}).")


if __name__ == "__main__":
    main()
