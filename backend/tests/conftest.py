"""Shared fixtures. The database URL is redirected to a temporary file *before* any app import."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_TMP = Path(tempfile.mkdtemp(prefix="nexis-test-"))
os.environ["NEXIS_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["NEXIS_REPORTS_DIR"] = str(_TMP / "reports")
os.environ["NEXIS_LOG_LEVEL"] = "WARNING"
# No network in tests: external providers are exercised through mocked HTTP transports instead.
os.environ["NEXIS_PUBLIC_PROVIDER_ENABLED"] = "false"
os.environ["NEXIS_EXTERNAL_DATA_ENABLED"] = "false"
os.environ["NEXIS_SECRET_KEY"] = "test-only-secret-key"

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.data.synthetic import SyntheticConfig, generate_universe  # noqa: E402
from app.data.validation import validate_bars  # noqa: E402

# A shorter synthetic sample keeps the suite fast while exercising every code path.
TEST_CONFIG = SyntheticConfig(seed=7, start="2019-01-01", end="2022-12-30")


@pytest.fixture(scope="session")
def universe():  # type: ignore[no-untyped-def]
    return generate_universe(TEST_CONFIG)


@pytest.fixture(scope="session")
def panel(universe) -> dict[str, pd.DataFrame]:  # type: ignore[no-untyped-def]
    clean = validate_bars(universe.bars).clean
    return {
        k: clean.pivot(index="date", columns="symbol", values=k).astype(float) for k in ("open", "high", "low", "close", "volume")
    }


@pytest.fixture(scope="session")
def client() -> Iterator:  # type: ignore[type-arg]
    from fastapi.testclient import TestClient

    from app.data.providers import SyntheticMarketDataProvider
    from app.db import session as db_session
    from app.main import app
    from app.services import ingestion, jobs, quality

    jobs.INLINE = True  # run background jobs synchronously inside the request
    with TestClient(app) as c:  # lifespan runs migrations on the temp database
        provider = SyntheticMarketDataProvider(TEST_CONFIG)
        with db_session.SessionLocal() as db:
            run = ingestion.ingest(
                db,
                provider,
                "TEST-SYN",
                "Test synthetic universe",
                None,
                None,
                None,
                "full",
                "test dataset",
                {"generator": TEST_CONFIG.to_dict(), "manifest": provider.manifest},
            )
            quality.run_quality_check(db, run.dataset_id)
        yield c


def make_prices(values: dict[str, list[float]], start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(next(iter(values.values()))))
    return pd.DataFrame(values, index=idx, dtype=float)


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(123)


@pytest.fixture(autouse=True)
def _fresh_rate_limits(request):  # type: ignore[no-untyped-def]
    """Rate limits are per client IP, and every test client shares one IP: start each API test with a clean slate."""
    if "client" in request.fixturenames:
        from app.db import session as db_session
        from app.models import RateEvent

        with db_session.SessionLocal() as db:
            db.query(RateEvent).delete()
            db.commit()
    yield
