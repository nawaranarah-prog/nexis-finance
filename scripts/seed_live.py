"""Initialise a public instance with real market data only (direct database access).

Usage (from the repository root, with NEXIS_DATABASE_URL pointing at the target database):
    backend/.venv/Scripts/python scripts/seed_live.py

Unlike ``seed_demo.py`` nothing here is synthetic or fictional: prices come from the Yahoo Finance
chart endpoint, industries and fundamentals from SEC EDGAR filings, rates from the US Treasury and
macro indicators from the World Bank. Every downstream result (portfolios, risk, stress tests,
backtests, ML models, the PDF report) is computed from that data by the normal services. Running
it again only fetches new bars and skips research that already exists. When the database is only
reachable through a deployed API, use ``bootstrap_public.py`` instead — it runs the same plan.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import live_plan as plan
from app.core.logging import configure_logging
from app.db import session as db_session
from app.db.init_db import init_db
from app.models import Asset, Backtest, Connection, Dataset, Experiment, MarketData
from app.services import backtests, connections, ml, portfolios, quality, reports, stress
from sqlalchemy import func, select


def step(msg: str) -> float:
    print(f"\n==> {msg}", flush=True)
    return time.perf_counter()


def done(t0: float, extra: str = "") -> None:
    print(f"    done in {time.perf_counter() - t0:.1f}s {extra}", flush=True)


def ensure_connection(db, key: str, config: dict | None = None) -> Connection | None:
    c = db.scalars(select(Connection).where(Connection.provider_key == key, Connection.status != "disconnected")).first()
    if c is None:
        c = connections.connect(db, key, None, None, config)
    elif config:
        c.config = config
        db.commit()
    if c.status != "connected":
        print(f"    {key}: {c.status} ({c.last_error})")
        return None
    return c


def main() -> None:
    configure_logging("WARNING")
    t = step("Initialising database (Alembic migrations)")
    init_db()
    done(t)

    db = db_session.SessionLocal()
    try:
        n = len(plan.EQUITIES) + len(plan.ETFS) + 1
        t = step(f"Market data: {n} real symbols from {plan.START} (Yahoo Finance chart endpoint)")
        c = ensure_connection(db, "yahoo_market", plan.MARKET_CONFIG)
        if c is None:
            raise SystemExit("market data provider unavailable — nothing to build on")
        r = connections.sync(db, c.id)
        done(t, f"sync {r['status']}, {r['records_added']:,} bars added")
        ds = db.scalars(select(Dataset).where(Dataset.code == connections.LIVE_DATASET)).one()

        t = step("SEC EDGAR: company profiles, SIC industries and XBRL fundamentals")
        c = ensure_connection(db, "sec_edgar", plan.SEC_CONFIG)
        if c is not None:
            r = connections.sync(db, c.id)
            done(t, f"sync {r['status']}, {r['records_added']:,} records added")

        t = step("Economic data: US Treasury yield curve and World Bank indicators")
        for key in ("us_treasury", "world_bank"):
            c = ensure_connection(db, key)
            if c is not None:
                r = connections.sync(db, c.id)
                print(f"    {key}: sync {r['status']}, {r['records_added']:,} added")
        done(t)

        last = db.scalar(
            select(func.max(MarketData.date)).join(Asset).where(Asset.dataset_id == ds.id, Asset.symbol == plan.BENCHMARK)
        )
        end = last.isoformat()

        t = step("Data-quality assessment")
        dq = quality.run_quality_check(db, ds.id)
        done(t, f"score={dq.overall_score:.2%} status={dq.status}")

        t = step("Portfolios (weights estimated 2017–2021, evaluated out of sample afterwards)")
        created = []
        for spec in plan.portfolios(ds.id):
            existing = [p for p in portfolios.list_portfolios(db) if p.name == spec["name"]]
            p = existing[0] if existing else portfolios.create_portfolio(db, spec)
            created.append(p)
            print(f"    {p.name}: {len(p.positions)} positions")
        done(t)

        t = step("Portfolio analytics, risk and stress tests")
        for p in created:
            portfolios.analytics(db, p.id)
        portfolios.risk_analysis(db, created[0].id, [0.95, 0.99], 756, 1)
        st_ids = [stress.run(db, created[3].id, pr["name"], pr["scenario_type"], pr["parameters"])["id"] for pr in stress.PRESETS]
        done(t)

        results: dict[str, dict] = {}
        for kind, cfg in plan.backtests(ds.id, end):
            if db.scalars(select(Backtest).where(Backtest.name == cfg["name"])).first():
                continue
            t = step(f"{'Walk-forward' if kind == 'walk_forward' else 'Backtest'}: {cfg['name']}")
            fn = backtests.execute_walk_forward if kind == "walk_forward" else backtests.execute_backtest
            results[cfg["name"]] = fn(db, cfg)
            done(t, results[cfg["name"]]["experiment_code"])

        runners = {"volatility": ml.run_volatility, "regime": ml.run_regime, "anomaly": ml.run_anomaly}
        for kind, cfg in plan.ml_experiments(ds.id):
            if db.scalars(select(Experiment).where(Experiment.name == cfg["name"])).first():
                continue
            t = step(f"ML: {cfg['name']}")
            results[cfg["name"]] = runners[kind](db, cfg)
            done(t, results[cfg["name"]]["experiment_code"])

        mom = results.get("Momentum top-8, IS-selected lookback")
        if mom:
            t = step("Research report (PDF)")
            exp_ids = [r["experiment_id"] for r in results.values() if "experiment_id" in r and r is not mom]
            rep = reports.generate(
                db, plan.REPORT_TITLE, created[0].id, mom["backtest_id"], exp_ids[:4], st_ids[:3], plan.REPORT_NOTES
            )
            done(t, rep.file_name)
    finally:
        db.close()
    print(f"\nPublic instance ready (market data through {end}).")


if __name__ == "__main__":
    main()
