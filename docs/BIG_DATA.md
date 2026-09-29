# Large-dataset processing

`scripts/big_data_pipeline.py` exercises the data pipeline at a scale beyond the ~96k-row demo universe. Generated data is written under `data/large/` (git-ignored) and deleted after the run unless `--keep` is passed.

```bash
backend/.venv/Scripts/python scripts/big_data_pipeline.py --records 10000
backend/.venv/Scripts/python scripts/big_data_pipeline.py --records 100000
backend/.venv/Scripts/python scripts/big_data_pipeline.py --records 1000000
```

## Stages

1. **generate:** seeded one-factor GBM bars with 0.1% injected high/low swaps and 0.1% duplicates, written as Parquet partitioned by symbol bucket (16 buckets).
2. **ingest + validate:** each partition is streamed with `pyarrow.dataset` and checked with the same rejection rules the application applies (non-positive prices, low > high, open/close outside the range, negative volume, duplicate `symbol+date`), vectorised. Clean data is rewritten partitioned by year.
3. **transform:** per-symbol returns, 20-day rolling volatility and 60-day momentum. Symbols are processed in chunks so memory is bounded by the chunk, not the dataset.
4. **aggregate:** monthly compounded returns per symbol and daily cross-sectional statistics, first in pandas (vectorised as `expm1(Σ log1p r)`), then the same monthly aggregation in SQLite (chunked load + `GROUP BY`).
5. **incremental:** one new trading day is appended, touching only its year partition.

## Recorded results

Measured on the development machine: Windows 11, 16 logical CPUs, 15.7 GB RAM, Python 3.14, pandas 3.0.6, pyarrow 25.0.1. Peak RSS is sampled every 20 ms and includes the interpreter and libraries (~100 MB baseline). Your numbers will differ; the JSON files in `data/large/` hold the full detail.

| stage | 10k rows | 100k rows | 1.0M rows |
|---|---:|---:|---:|
| generate | 0.06 s | 0.22 s | 1.01 s |
| ingest + validate | 0.13 s | 0.48 s | 0.80 s (≈1.26M rows/s) |
| rolling features | 0.59 s | 3.65 s | 6.92 s |
| pandas aggregation | 0.05 s | 0.12 s | 0.53 s |
| SQLite load + aggregation | 0.07 s | 0.38 s | 19.05 s (load 14.9 s, query 4.2 s) |
| incremental append | 0.02 s | 0.05 s | 0.34 s |
| peak RSS (any stage) | 120 MB | 169 MB | 425 MB |

Two observations from these runs:

* Replacing a per-group Python `apply` in the monthly aggregation with a vectorised log-return sum cut that stage at 1M rows from **14.5 s to 0.53 s**. Most "big data" slowness at this scale is algorithmic, not a lack of cluster compute.
* Row-by-row `executemany` into SQLite dominates the SQL path. PostgreSQL `COPY` or a columnar engine would be the next step if SQL-side aggregation mattered.

## When Spark would be appropriate

PySpark is **not** a dependency of this project. The development environment has no JVM, and every workload here fits comfortably in one process: 1M bars use under 0.5 GB and finish in seconds. Adding Spark would add operational weight without a measurable benefit.

Spark (or a similar distributed engine) becomes the right tool when one or more of these hold:

* the working set no longer fits in memory on one machine (for example, tick or quote data across a full exchange universe: billions of rows, hundreds of GB);
* the data already lives in a distributed store (HDFS/S3 lakehouse tables) and moving it to one node would cost more than the computation;
* many independent heavy jobs (e.g. thousands of walk-forward folds or parameter sweeps) need to run in parallel on a cluster;
* the organisation already runs Spark and needs the pipeline to fit that platform.

The pipeline above is written in the same partition-oriented style, so it maps onto Spark directly:

| This pipeline | Spark equivalent |
|---|---|
| Parquet dataset partitioned by bucket/year | the same Parquet/Delta layout, `partitionBy("year")` |
| vectorised validation per partition | `DataFrame.filter` with column expressions, `dropDuplicates(["symbol","date"])` |
| per-symbol rolling features | `Window.partitionBy("symbol").orderBy("date").rowsBetween(-19, 0)` |
| monthly compounding via Σ log1p | `groupBy("symbol", month).agg(expm1(sum(log1p("ret"))))` |
| incremental append to one partition | `mode("append")` / Delta `MERGE` on the new date partition |

For the medium range (tens of GB on one machine), DuckDB or Polars over the same Parquet files would usually be the pragmatic choice before reaching for a cluster.
