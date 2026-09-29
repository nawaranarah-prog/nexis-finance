# Architecture

## Layers and dependency direction

```text
api/routes  →  services  →  analytics · risk · strategies · backtesting · ml · data   (pure, no DB)
                  │
                  └──→  models (SQLAlchemy)  →  db (engine/session)
```

* **Routes** parse and validate requests (Pydantic schemas in `app/schemas`), call a service, and return JSON. They contain no business logic.
* **Services** orchestrate: they load data, call the pure analytics, persist results, register experiments, emit notifications and manage jobs.
* **Pure modules** (`analytics`, `risk`, `strategies`, `backtesting`, `ml`, `data`) take pandas/numpy objects and return plain results. They never import the ORM, which is why they can be unit-tested with tiny hand-built datasets.
* **Domain errors** (`app/core/errors.py`) are raised anywhere and mapped once, in `app/main.py`, to `{"error": {code, message, details}}` responses with the right status codes.

## Request lifecycle for a long-running research run

```text
POST /api/backtests ──► BacktestRequest (Pydantic) ──► validate_config()   (fail fast: 422)
          │
          ▼
 jobs.submit() ─► Job row (queued) ─► ThreadPoolExecutor ─► execute_backtest(session, cfg, progress)
          │                                                     │
   202 {job}                                                    ├─ start_experiment()  → experiments (running)
          │                                                     ├─ grid_search on in-sample window (optional)
 GET /api/jobs/{id}  (UI polls every 0.7 s,                     ├─ run_backtest()  → trades, equity, costs
 shows progress)                                                ├─ summarize / segments / chart series
                                                                ├─ Backtest + BacktestTrade rows (bulk insert)
                                                                ├─ experiment_metrics rows, status=completed
                                                                └─ Notification ("Backtest BT-2026-004 completed")
```

Reproduction (`POST /api/experiments/{id}/reproduce`) looks up the runner registered for the experiment type, re-runs it with the stored config (which includes the seed), and stores a `reproducibility` record: metrics compared, maximum absolute difference, and whether the dataset hash has changed.

## Data model (21 tables)

| Group | Tables | Notes |
|---|---|---|
| Market | `datasets`, `assets`, `market_data`, `ingestion_runs` | `market_data` has a unique composite index on `(asset_id, date)` plus an index on `date`. A dataset's `version` increments and its `content_hash` (SHA-256 of all bars) is recomputed on every ingestion that changes data. |
| Quality | `data_quality_runs`, `data_quality_issues` | Issues link either to the ingestion run that rejected or flagged them, or to the quality run that found them. There is an index on `(dataset_id, check)`. |
| Portfolio | `portfolios`, `portfolio_positions`, `portfolio_returns`, `risk_metrics`, `stress_tests` | `portfolio_returns` is a materialised series, refreshed whenever analytics are computed. |
| Research | `strategies`, `experiments`, `experiment_metrics`, `backtests`, `backtest_trades`, `ml_predictions`, `anomalies` | `experiment_metrics` stores one row per (split, model, metric), so experiments can be compared in plain SQL. Chart-ready diagnostics live in JSON columns to avoid a table per chart. |
| Ops | `jobs`, `notifications`, `reports` | Report files are stored on disk with their SHA-256. Downloads are path-traversal guarded. |

Indexes were added only where a query path uses them: `(asset_id, date)` for price loads, `(experiment_id, …)` for per-experiment fetches, `(dataset_id, check)` for data-quality summaries, `(status)` for jobs, and `(is_read, created_at)` for notifications.

Schema changes go through Alembic (`backend/alembic/versions`). The application runs `alembic upgrade head` at startup. `render_as_batch` keeps migrations SQLite-compatible, and the same migration renders valid PostgreSQL DDL.

## Caching and performance

* **Price panels**: `load_panel` caches the wide `date × symbol` frames per `(dataset_id, version)`. A new ingestion bumps the version and evicts older entries.
* **Portfolio analytics**: an LRU cache keyed on `(portfolio, updated_at, dataset version, date range, risk-free rate, window)`, so changing an unrelated UI control never recomputes a multi-year simulation.
* **Factor returns**: cached per dataset version.
* **Frontend**: TanStack Query with a 30 s stale time (∞ for immutable results such as a finished backtest), no refetch on window focus, and no retries on 4xx.
* **Backtest engine**: prices are converted to NumPy once. Strategies see only a warm-up-length window, so a daily-signal backtest over roughly 2,000 days runs in about 1.5 s. Transport uses gzip.

## Observability

* Structured logging (key=value or JSON with `NEXIS_LOG_JSON=true`), including ingestion, job and slow-request events.
* Request-timing middleware records p50/p95/max per route template, adds an `X-Response-Time-ms` header, and exposes the data on System Health.
* System Health aggregates database status and latency, record counts per table, dataset freshness, recent ingestions, failed jobs and experiment/ingestion durations.

## Security posture

* Configuration comes from environment variables only. There are no credentials in code, and `.env` is git-ignored.
* Every request body is validated by Pydantic with `extra="forbid"`, bounded numeric ranges and symbol syntax checks.
* All SQL goes through the ORM or bound parameters.
* CSV uploads are extension-checked and size-limited; parse failures return a 422.
* Report downloads resolve paths inside the reports directory only.
* CORS is restricted to configured origins with a minimal method and header list.
* Unhandled exceptions return a generic 500. Details are logged, never sent to the client.
* There is no authentication. The app is intended as a local single-user research tool; see the README's limitations.

## Visual design

The UI uses dense tables, hairline borders, tabular numerals and neutral surfaces, with separate dark and light themes. Chart colours follow a validated categorical palette assigned in fixed order: sectors always keep the same hue. Correlation and return heatmaps use a blue ↔ red diverging scale around a neutral midpoint, and magnitude uses a single-hue blue ramp. Status colours are reserved for status and always paired with an icon or label. There are no dual-axis charts: price and volume, and price and indicator, are separate aligned charts.
