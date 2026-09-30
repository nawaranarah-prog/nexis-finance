"""FastAPI application entry point."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.api.routes import connectivity, data, markets, portfolios, public_v1, reports, research, social, system
from app.core.config import APP_VERSION, get_settings
from app.core.errors import NexisError
from app.core.logging import configure_logging, get_logger, log_event, request_metrics

# Importing the services registers experiment runners used by "Reproduce".
from app.services import backtests as _bt  # noqa: F401
from app.services import ml as _ml  # noqa: F401

settings = get_settings()
configure_logging(settings.log_level, settings.log_json)
log = get_logger("nexis.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from app.db.init_db import init_db

    init_db()
    from app.db import autoseed

    autoseed.maybe_start()
    log_event(
        log,
        "Nexis Finance API started",
        version=APP_VERSION,
        env=settings.env,
        database="sqlite" if settings.is_sqlite else "postgresql",
    )
    yield


app = FastAPI(
    title="Nexis Finance API",
    version=APP_VERSION,
    description=(
        "Quantitative research platform: market-data ingestion and validation, portfolio analytics, risk "
        "(VaR/CVaR, stress tests), strategy backtesting with walk-forward evaluation, financial machine learning "
        "and reproducible experiment tracking. Research software — not investment advice."
    ),
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def timing_middleware(request: Request, call_next: Any) -> Any:
    start = time.perf_counter()
    # Vercel passes a short-lived OIDC token with each function request; the AI Gateway accepts it.
    from app.services import llm

    llm.set_request_token(request.headers.get("x-vercel-oidc-token"))
    response = await call_next(request)
    dur = (time.perf_counter() - start) * 1000
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    if path.startswith("/api"):
        request_metrics.record(request.method, path, response.status_code, dur)
        if dur > 1000:
            log_event(log, "slow request", method=request.method, path=path, status=response.status_code, duration_ms=round(dur))
    response.headers["X-Response-Time-ms"] = f"{dur:.1f}"
    return response


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details}})


@app.exception_handler(NexisError)
async def nexis_error_handler(_: Request, exc: NexisError) -> JSONResponse:
    return _error(exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"]} for e in exc.errors()]
    return _error(422, "validation_failed", "request validation failed", details)


@app.exception_handler(OperationalError)
async def db_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    log.exception("database error on %s", request.url.path)
    return _error(503, "database_unavailable", "the database is unavailable or busy; please retry")


@app.exception_handler(SQLAlchemyError)
async def sa_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    log.exception("database error on %s", request.url.path)
    return _error(500, "database_error", "a database error occurred")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return _error(500, "internal_error", "an unexpected error occurred; details were logged on the server")


for r in (
    system.router,
    data.router,
    portfolios.router,
    research.router,
    reports.router,
    connectivity.router,
    public_v1.router,
    markets.router,
    social.router,
):
    app.include_router(r, prefix="/api")
