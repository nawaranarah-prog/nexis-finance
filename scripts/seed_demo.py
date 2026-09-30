"""Create the demo database: synthetic dataset, data-quality run, portfolios and research runs.

Usage (from the repository root):
    backend/.venv/Scripts/python scripts/seed_demo.py            # full demo (~1–2 minutes)
    backend/.venv/Scripts/python scripts/seed_demo.py --minimal  # data + portfolios only

Every result is produced by the real pipelines; nothing is hardcoded. The synthetic dataset is
ingested in two steps (full load to 2026-03-31, then an incremental update to 2026-06-30) so the
incremental-ingestion path is exercised and visible in the ingestion history.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.logging import configure_logging
from app.data.providers import SyntheticMarketDataProvider
from app.data.synthetic import SyntheticConfig
from app.db import session as db_session
from app.db.init_db import init_db
from app.models import Dataset
from app.services import (
    backtests,
    connections,
    imports,
    ingestion,
    ml,
    portfolios,
    quality,
    reports,
    stress,
)
from sqlalchemy import select

DATASET_CODE = "SYN-MULTI-DEMO"


def step(msg: str) -> float:
    print(f"\n==> {msg}", flush=True)
    return time.perf_counter()


def done(t0: float, extra: str = "") -> None:
    print(f"    done in {time.perf_counter() - t0:.1f}s {extra}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--minimal",
        action="store_true",
        help="only load data, run data quality and create portfolios",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--offline", action="store_true", help="skip real external data (Yahoo, SEC, Treasury, World Bank)")
    args = ap.parse_args()
    configure_logging("WARNING")

    t = step("Initialising database (Alembic migrations)")
    init_db()
    done(t)

    cfg = SyntheticConfig(seed=args.seed)
    provider = SyntheticMarketDataProvider(cfg)
    ds_config = {"generator": cfg.to_dict(), "manifest": provider.manifest}
    manifest_path = ROOT / "data" / "demo" / "manifest.json"
    manifest_path.write_text(json.dumps(provider.manifest, indent=2))

    db = db_session.SessionLocal()
    try:
        t = step("Ingesting synthetic dataset — initial load to 2026-03-31")
        run = ingestion.ingest(
            db,
            provider,
            DATASET_CODE,
            "Nexis Synthetic Multi-Sector Universe",
            None,
            None,
            date(2026, 3, 31),
            "full",
            "Synthetic multi-sector universe (DEMO / SYNTHETIC DATA MODE). See manifest for generation details.",
            ds_config,
        )
        done(
            t,
            f"inserted={run.records_inserted:,} rejected={run.records_rejected} duplicates={run.duplicates}",
        )

        t = step("Incremental update to 2026-06-30 (only new bars are requested)")
        run = ingestion.ingest(
            db,
            provider,
            DATASET_CODE,
            "Nexis Synthetic Multi-Sector Universe",
            None,
            None,
            date(2026, 6, 30),
            "incremental",
            None,
            ds_config,
        )
        done(
            t,
            f"received={run.records_received:,} inserted={run.records_inserted:,} version={run.dataset_version}",
        )
        ds_id = run.dataset_id

        t = step("Data-quality assessment")
        dq = quality.run_quality_check(db, ds_id)
        done(t, f"score={dq.overall_score:.2%} status={dq.status}")

        t = step("Creating demo portfolios")
        common = {
            "dataset_id": ds_id,
            "benchmark_symbol": "NXMKT",
            "initial_capital": 1_000_000,
            "rebalance_frequency": "monthly",
            "estimation_start": "2018-01-02",
            "estimation_end": "2021-12-31",
        }
        diversified = [
            "TCH1",
            "TCH3",
            "FIN1",
            "FIN3",
            "HLT2",
            "HLT4",
            "NRG1",
            "IND2",
            "IND4",
            "STP1",
            "STP3",
            "DSC2",
            "UTL1",
            "UTL3",
            "CMD1",
            "NXBND",
        ]
        specs = [
            {
                "name": "Diversified Equal Weight",
                "symbols": diversified,
                "allocation_method": "equal_weight",
                "notes": "Equal-weight research portfolio across sectors, a commodity and the bond index.",
            },
            {
                "name": "Diversified Minimum Variance",
                "symbols": diversified,
                "allocation_method": "min_variance",
                "max_weight": 0.25,
                "notes": "Long-only minimum variance, Ledoit-Wolf covariance, estimated 2018-2021.",
            },
            {
                "name": "Diversified Risk Parity",
                "symbols": diversified,
                "allocation_method": "risk_parity",
                "notes": "Equal risk contribution weights estimated 2018-2021.",
            },
            {
                "name": "Growth Tilt (custom)",
                "symbols": ["TCH1", "TCH2", "TCH3", "DSC1", "DSC3", "FIN2"],
                "allocation_method": "custom",
                "weights": {
                    "TCH1": 0.25,
                    "TCH2": 0.2,
                    "TCH3": 0.15,
                    "DSC1": 0.15,
                    "DSC3": 0.15,
                    "FIN2": 0.10,
                },
                "notes": "Concentrated custom-weight portfolio for concentration and stress analysis.",
            },
        ]
        created = []
        for s in specs:
            existing = [p for p in portfolios.list_portfolios(db) if p.name == s["name"]]
            p = existing[0] if existing else portfolios.create_portfolio(db, {**common, **s})
            created.append(p)
            print(f"    {p.name}: {len(p.positions)} positions")
        done(t)

        if args.minimal:
            return

        t = step("Portfolio analytics, risk analysis and stress tests")
        for p in created:
            portfolios.analytics(db, p.id)
        portfolios.risk_analysis(db, created[0].id, [0.95, 0.99], 756, 1)
        st_ids = []
        for preset in stress.PRESETS:
            r = stress.run(
                db,
                created[3].id,
                preset["name"],
                preset["scenario_type"],
                preset["parameters"],
            )
            st_ids.append(r["id"])
        stress.run(
            db,
            created[3].id,
            "Technology sector shock −15%",
            "sector_shock",
            {"sector": "Technology", "shock": -0.15},
        )
        done(t)

        base_bt = {
            "dataset_id": ds_id,
            "benchmark_symbol": "NXMKT",
            "start_date": "2019-01-02",
            "end_date": "2026-06-30",
            "initial_capital": 1_000_000,
            "commission_bps": 5,
            "slippage_bps": 5,
            "execution": "next_open",
            "risk_free_rate": 0.02,
        }
        t = step("Backtest: momentum with in-sample parameter selection, validation and out-of-sample test")
        r_mom = backtests.execute_backtest(
            db,
            {
                **base_bt,
                "name": "Momentum top-8, IS-selected lookback",
                "strategy": "momentum",
                "params": {"top_n": 8, "rebalance": "monthly"},
                "param_grid": {"lookback": [63, 126, 252], "skip": [0, 21]},
                "train_end": "2022-12-30",
                "validation_end": "2024-06-28",
                "notes": "Lookback/skip chosen by in-sample Sharpe only.",
            },
        )
        done(t, r_mom["experiment_code"])

        t = step("Backtest: z-score mean reversion")
        r_mr = backtests.execute_backtest(
            db,
            {
                **base_bt,
                "name": "Mean reversion z=2.0/0.25, 20d",
                "strategy": "mean_reversion",
                "params": {
                    "window": 20,
                    "entry_z": 2.0,
                    "exit_z": 0.25,
                    "max_positions": 10,
                },
                "train_end": "2022-12-30",
                "validation_end": "2024-06-28",
            },
        )
        done(t, r_mr["experiment_code"])

        t = step("Backtest: equal-weight baseline")
        r_eq = backtests.execute_backtest(
            db,
            {
                **base_bt,
                "name": "Equal-weight monthly baseline",
                "strategy": "equal_weight",
                "params": {"rebalance": "monthly"},
            },
        )
        done(t, r_eq["experiment_code"])

        t = step("Walk-forward analysis: momentum")
        r_wf = backtests.execute_walk_forward(
            db,
            {
                **base_bt,
                "name": "Momentum walk-forward (2y train / 6m test)",
                "strategy": "momentum",
                "params": {"top_n": 8},
                "param_grid": {"lookback": [63, 126, 252]},
                "train_days": 504,
                "test_days": 126,
            },
        )
        done(t, r_wf["experiment_code"])

        t = step("ML: volatility forecasting (10-day horizon)")
        r_vol = ml.run_volatility(
            db,
            {
                "dataset_id": ds_id,
                "symbols": ["TCH1", "FIN2", "HLT3", "NRG3", "IND2", "STP2", "UTL1"],
                "benchmark_symbol": "NXMKT",
                "horizon": 10,
                "train_end": "2022-12-30",
                "validation_end": "2024-06-28",
                "seed": 42,
                "name": "10-day realised volatility, 7 assets pooled",
            },
        )
        done(t, r_vol["experiment_code"])

        t = step("ML: market regime classification")
        r_reg = ml.run_regime(
            db,
            {
                "dataset_id": ds_id,
                "benchmark_symbol": "NXMKT",
                "n_regimes": 4,
                "method": "gmm",
                "train_end": "2022-12-30",
                "seed": 42,
                "name": "GMM 4-regime market model",
            },
        )
        done(t, r_reg["experiment_code"])

        t = step("ML: anomaly detection")
        r_an = ml.run_anomaly(
            db,
            {
                "dataset_id": ds_id,
                "contamination": 0.002,
                "z_threshold": 6.0,
                "seed": 42,
                "name": "Universe anomaly scan",
            },
        )
        done(t, r_an["experiment_code"])

        t = step("Research report (PDF)")
        rep = reports.generate(
            db,
            "Nexis Demo Research Report",
            created[0].id,
            r_mom["backtest_id"],
            [
                r_vol["experiment_id"],
                r_reg["experiment_id"],
                r_an["experiment_id"],
                r_wf["experiment_id"],
            ],
            st_ids[:3],
            "Generated by scripts/seed_demo.py on the synthetic demo dataset.",
        )
        done(t, rep.file_name)

        t = step("Connectivity: synthetic source + sample brokerage statements (fictional quantities)")
        connections.connect(db, "synthetic_market", None, None, None)
        samples = ROOT / "data" / "samples"
        for f, label, kind, opts in [
            ("brokerage_a_holdings.csv", "Sample Brokerage A", "holdings", {"as_of": "2026-09-25"}),
            ("brokerage_a_transactions.csv", "Sample Brokerage A", "transactions", {}),
            ("brokerage_b_positions.xlsx", "Sample Brokerage B", "holdings", {"as_of": "2026-09-26"}),
            (
                "retirement_account.json",
                "Sample Retirement Plan",
                "holdings",
                {"account": "IRA-001", "account_name": "Retirement (IRA)", "as_of": "2026-09-24"},
            ),
        ]:
            content = (samples / f).read_bytes()
            prop = imports.preview(content, f, kind)["proposal"]
            b = imports.import_file(db, content, f, label, kind, prop["mapping"], {"institution": label, **opts}, is_sample=True)
            print(f"    {f}: {b['rows_imported']} imported, {b['rows_rejected']} rejected")
        done(t)

        if not args.offline:
            t = step("Real external data: Yahoo prices, SEC EDGAR, US Treasury, World Bank")
            for key in ("yahoo_market", "sec_edgar", "us_treasury", "world_bank"):
                c = connections.connect(db, key, None, None, None)
                if c.status != "connected":
                    print(f"    {key}: {c.status} ({c.last_error})")
                    continue
                r = connections.sync(db, c.id)
                print(f"    {key}: sync {r['status']}, {r['records_added']} added")
            live = db.scalars(select(Dataset).where(Dataset.code == "LIVE-MARKET")).first()
            if live:
                ml.run_regime(
                    db,
                    {
                        "dataset_id": live.id,
                        "benchmark_symbol": "SPY",
                        "n_regimes": 3,
                        "method": "gmm",
                        "train_end": "2025-06-30",
                        "seed": 42,
                        "name": "Regime model on live SPY",
                    },
                )
            done(t)
    finally:
        db.close()
    print("\nDemo database ready.")


if __name__ == "__main__":
    main()
