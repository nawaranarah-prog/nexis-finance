"""Large-dataset pipeline and benchmark (batch ETL at a scale beyond the demo universe).

Stages, each timed with peak resident memory recorded:

1. generate    – seeded synthetic daily bars for N records, written as a Parquet dataset partitioned
                 by symbol bucket (never committed to git; regenerate locally).
2. ingest      – stream the raw dataset in record batches (pyarrow.dataset), apply the same vectorised
                 validity rules as the application (OHLC consistency, positive prices, duplicates),
                 and write clean batches to a year-partitioned Parquet dataset.
3. transform   – per-symbol returns, 20-day rolling volatility and 60-day momentum, processed one
                 partition at a time so memory stays bounded by partition size, not dataset size.
4. aggregate   – monthly per-symbol returns and daily cross-sectional statistics with pandas, and the
                 same monthly aggregation in SQL (SQLite, chunked load + GROUP BY) for comparison.
5. incremental – append one new trading day and re-run only the affected partition.

Usage:
    backend/.venv/Scripts/python scripts/big_data_pipeline.py --records 1000000
    backend/.venv/Scripts/python scripts/big_data_pipeline.py --records 100000 --symbols 200

Results are written to data/large/benchmark-<records>.json and printed as a table. Numbers depend
on the machine; see docs/BIG_DATA.md for a recorded run and when Spark would be warranted instead.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "large"


class PeakMemory:
    """Samples process RSS in a background thread to capture the peak during a stage."""

    def __init__(self, interval: float = 0.02) -> None:
        self.proc = psutil.Process(os.getpid())
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, self.proc.memory_info().rss)
            time.sleep(self.interval)

    def __enter__(self) -> PeakMemory:
        self.start_rss = self.proc.memory_info().rss
        self.peak = self.start_rss
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._t.join()
        self.peak = max(self.peak, self.proc.memory_info().rss)


RESULTS: list[dict[str, Any]] = []


@contextmanager
def stage(name: str, records: int) -> Iterator[dict[str, Any]]:
    info: dict[str, Any] = {}
    with PeakMemory() as mem:
        t0 = time.perf_counter()
        yield info
        dt = time.perf_counter() - t0
    row = {
        "stage": name,
        "seconds": round(dt, 3),
        "records": records,
        "records_per_second": round(records / dt) if dt else None,
        "peak_rss_mb": round(mem.peak / 2**20, 1),
        "rss_increase_mb": round((mem.peak - mem.start_rss) / 2**20, 1),
        **info,
    }
    RESULTS.append(row)
    print(f"  {name:<22} {dt:8.2f}s  {row['records_per_second'] or 0:>12,} rec/s  peak RSS {row['peak_rss_mb']:>8.1f} MB  {info}")


def generate(n_records: int, n_symbols: int, seed: int, out: Path) -> int:
    """Correlated one-factor GBM with GARCH-free vol clusters; written in symbol-bucket partitions."""
    rng = np.random.default_rng(seed)
    n_days = max(20, n_records // n_symbols)
    dates = pd.bdate_range("2000-01-03", periods=n_days)
    market = rng.normal(0.0003, 0.011, n_days)
    written = 0
    buckets = 16
    for b in range(buckets):
        syms = [s for s in range(n_symbols) if s % buckets == b]
        if not syms:
            continue
        frames = []
        for s in syms:
            beta = rng.uniform(0.5, 1.5)
            vol = rng.uniform(0.008, 0.025)
            r = beta * market + rng.normal(0, vol, n_days)
            close = rng.uniform(10, 300) * np.exp(np.cumsum(np.log1p(r)))
            open_ = close * np.exp(rng.normal(0, vol * 0.3, n_days))
            high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, vol * 0.5, n_days)))
            low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, vol * 0.5, n_days)))
            frames.append(
                pd.DataFrame(
                    {
                        "symbol": f"S{s:05d}",
                        "date": dates,
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": rng.lognormal(12, 0.6, n_days).astype(np.int64),
                    }
                )
            )
        df = pd.concat(frames, ignore_index=True)
        # Inject a small, known share of defects so validation has work to do (0.1% each).
        k = max(1, len(df) // 1000)
        bad = rng.choice(len(df), size=k, replace=False)
        df.loc[bad, ["high", "low"]] = df.loc[bad, ["low", "high"]].to_numpy()
        dup = df.sample(n=k, random_state=seed + b)
        df = pd.concat([df, dup], ignore_index=True)
        df["bucket"] = b
        pq.write_to_dataset(pa.Table.from_pandas(df, preserve_index=False), out, partition_cols=["bucket"])
        written += len(df)
    return written


def validate_batch(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Vectorised version of the application's rejection rules (issue rows are counted, not listed)."""
    counts: dict[str, int] = {}
    price_bad = (df[["open", "high", "low", "close"]] <= 0).any(axis=1) | df["close"].isna()
    lh = df["low"] > df["high"]
    rng_bad = (
        (df["close"] > df["high"] * (1 + 1e-9))
        | (df["close"] < df["low"] * (1 - 1e-9))
        | (df["open"] > df["high"] * (1 + 1e-9))
        | (df["open"] < df["low"] * (1 - 1e-9))
    )
    vol_bad = df["volume"] < 0
    reject = price_bad | lh | rng_bad | vol_bad
    counts.update(
        non_positive_or_missing=int(price_bad.sum()),
        low_above_high=int(lh.sum()),
        ohlc_out_of_range=int((rng_bad & ~lh).sum()),
        negative_volume=int(vol_bad.sum()),
    )
    df = df[~reject]
    dup = df.duplicated(["symbol", "date"], keep="first")
    counts["duplicates"] = int(dup.sum())
    return df[~dup], counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", type=int, default=1_000_000)
    ap.add_argument("--symbols", type=int, default=None, help="default: records / 2500 (≈10 years per symbol)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--keep", action="store_true", help="keep generated files after the run")
    args = ap.parse_args()
    n_sym = args.symbols or max(4, args.records // 2500)
    work = BASE / f"run-{args.records}"
    raw, clean, feats = work / "raw", work / "clean", work / "features"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    print(f"Nexis big-data pipeline: target {args.records:,} records, {n_sym:,} symbols, seed {args.seed}")

    with stage("1_generate", args.records) as info:
        info["rows_written"] = generate(args.records, n_sym, args.seed, raw)
        info["raw_mb"] = round(sum(f.stat().st_size for f in raw.rglob("*.parquet")) / 2**20, 1)
    total = RESULTS[-1]["rows_written"]

    rejected: dict[str, int] = {}
    with stage("2_ingest_validate", total) as info:
        dset = ds.dataset(raw, format="parquet", partitioning="hive")
        accepted = 0
        # Duplicates are resolved per symbol bucket, so each bucket is validated as a unit.
        for frag in dset.get_fragments():
            df = frag.to_table().to_pandas()
            good, c = validate_batch(df)
            for k, v in c.items():
                rejected[k] = rejected.get(k, 0) + v
            good = good.assign(year=pd.to_datetime(good["date"]).dt.year).drop(columns=["bucket"], errors="ignore")
            pq.write_to_dataset(pa.Table.from_pandas(good, preserve_index=False), clean, partition_cols=["year"])
            accepted += len(good)
        info.update(accepted=accepted, **rejected)

    with stage("3_transform_features", accepted) as info:
        # Rolling windows need contiguous history per symbol, so process per symbol bucket of the clean
        # dataset (read by symbol filter) rather than per year.
        cdset = ds.dataset(clean, format="parquet", partitioning="hive")
        symbols = sorted(set(cdset.to_table(columns=["symbol"]).column("symbol").to_pylist()))
        chunk = max(1, len(symbols) // 8)
        rows = 0
        for i in range(0, len(symbols), chunk):
            part = symbols[i : i + chunk]
            df = cdset.to_table(filter=ds.field("symbol").isin(part), columns=["symbol", "date", "close"]).to_pandas()
            df = df.sort_values(["symbol", "date"])
            g = df.groupby("symbol", sort=False)["close"]
            df["ret"] = g.pct_change(fill_method=None)
            lr = np.log1p(df["ret"])
            df["vol_20"] = lr.groupby(df["symbol"]).rolling(20).std().reset_index(level=0, drop=True) * np.sqrt(252)
            df["mom_60"] = df["close"] / g.shift(60) - 1
            pq.write_to_dataset(
                pa.Table.from_pandas(df.assign(part=i // chunk), preserve_index=False), feats, partition_cols=["part"]
            )
            rows += len(df)
        info["rows"] = rows

    with stage("4a_aggregate_pandas", accepted) as info:
        f = (
            ds.dataset(feats, format="parquet", partitioning="hive")
            .to_table(columns=["symbol", "date", "ret", "vol_20"])
            .to_pandas()
        )
        f["month"] = pd.to_datetime(f["date"]).dt.to_period("M")
        # Vectorised compounding: expm1 of summed log returns per (symbol, month) — no Python-level apply.
        monthly = np.expm1(np.log1p(f["ret"]).groupby([f["symbol"], f["month"]]).sum())
        xs = f.groupby("date").agg(mean_ret=("ret", "mean"), dispersion=("ret", "std"), median_vol=("vol_20", "median"))
        info.update(monthly_rows=len(monthly), cross_section_rows=len(xs))
        del f

    with stage("4b_aggregate_sql_sqlite", accepted) as info:
        db = work / "bench.sqlite"
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("CREATE TABLE bars (symbol TEXT, date TEXT, ret REAL)")
        t_load = time.perf_counter()
        for batch in ds.dataset(feats, format="parquet", partitioning="hive").to_batches(
            columns=["symbol", "date", "ret"], batch_size=200_000
        ):
            b = batch.to_pandas()
            con.executemany(
                "INSERT INTO bars VALUES (?,?,?)",
                zip(b["symbol"], pd.to_datetime(b["date"]).dt.strftime("%Y-%m-%d"), b["ret"].astype(float), strict=True),
            )
        con.execute("CREATE INDEX ix_bars ON bars(symbol, date)")
        con.commit()
        load_s = time.perf_counter() - t_load
        t_q = time.perf_counter()
        # Monthly compounded return via sum of log returns (SQLite has no product aggregate).
        con.create_function("ln1p", 1, lambda x: None if x is None or x <= -1 else float(np.log1p(x)))
        n = con.execute("""SELECT COUNT(*) FROM (SELECT symbol, substr(date,1,7) AS m, exp(SUM(ln1p(ret))) - 1 AS r
                           FROM bars WHERE ret IS NOT NULL GROUP BY symbol, m)""").fetchone()[0]
        info.update(load_seconds=round(load_s, 2), query_seconds=round(time.perf_counter() - t_q, 2), monthly_rows=n)
        con.close()

    with stage("5_incremental_append", n_sym) as info:
        cdset = ds.dataset(clean, format="parquet", partitioning="hive")
        last = cdset.to_table(columns=["date"]).column("date").to_pandas().max()
        new_day = pd.bdate_range(pd.Timestamp(last) + pd.Timedelta(days=1), periods=1)[0]
        rng = np.random.default_rng(args.seed + 1)
        last_close = cdset.to_table(
            filter=ds.field("date") == pa.scalar(pd.Timestamp(last)), columns=["symbol", "close"]
        ).to_pandas()
        c = last_close["close"].to_numpy() * np.exp(rng.normal(0, 0.01, len(last_close)))
        new = pd.DataFrame(
            {
                "symbol": last_close["symbol"],
                "date": new_day,
                "open": c,
                "high": c * 1.01,
                "low": c * 0.99,
                "close": c,
                "volume": 1_000_000,
            }
        )
        good, _ = validate_batch(new)
        pq.write_to_dataset(
            pa.Table.from_pandas(good.assign(year=new_day.year), preserve_index=False), clean, partition_cols=["year"]
        )
        info.update(appended=len(good), partition_touched=f"year={new_day.year}")

    report = {
        "records_target": args.records,
        "rows_generated": total,
        "symbols": n_sym,
        "seed": args.seed,
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        },
        "library_versions": {"pandas": pd.__version__, "numpy": np.__version__, "pyarrow": pa.__version__},
        "stages": RESULTS,
    }
    out = BASE / f"benchmark-{args.records}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {out.relative_to(ROOT)}")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
