# Nexis Finance — backend

FastAPI + SQLAlchemy research API. See the [root README](../README.md) for the full picture.

```bash
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python ../scripts/seed_demo.py                       # migrations + synthetic demo data + research runs
uvicorn app.main:app --reload --port 8000            # http://127.0.0.1:8000/docs
pytest                                               # 114 tests
ruff check app tests && ruff format --check app tests
```

| Package | Responsibility |
|---|---|
| `app/api/routes` | HTTP layer: request validation and delegation only |
| `app/services` | orchestration, persistence, experiment registry, jobs, reports, search, health |
| `app/analytics`, `app/risk` | pure financial math: metrics, rolling stats, simulation, optimisation, VaR, stress, factors |
| `app/strategies`, `app/backtesting` | strategy interface and implementations; event-driven engine; segments; walk-forward |
| `app/ml` | point-in-time features, purged splits, volatility / regime / anomaly experiments |
| `app/data` | providers, synthetic generator, validation, data-quality scoring |
| `app/models`, `alembic/` | ORM and migrations |

Configuration: `NEXIS_*` environment variables or `backend/.env` (see `../.env.example`). The default database is SQLite at `../data/nexis.db`. Set `NEXIS_DATABASE_URL=postgresql+psycopg://…` to use PostgreSQL.
