"""Structured logging configuration."""

from __future__ import annotations

import json
import logging
import sys
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()}"
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict) and extra:
            base += " | " + " ".join(f"{k}={v}" for k, v in extra.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", as_json: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else KeyValueFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
    logger.log(level, msg, extra={"ctx": ctx})


@contextmanager
def timed(logger: logging.Logger, operation: str, **ctx: Any) -> Iterator[dict[str, Any]]:
    """Log the wall-clock duration of an operation. Yields a dict the caller may enrich."""
    info: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        yield info
    finally:
        elapsed = time.perf_counter() - start
        info["duration_seconds"] = elapsed
        log_event(
            logger,
            f"{operation} finished",
            operation=operation,
            duration_ms=round(elapsed * 1000, 1),
            **ctx,
            **{k: v for k, v in info.items() if k != "duration_seconds"},
        )


class RequestMetrics:
    """In-memory ring buffer of recent request timings for the System Health page."""

    def __init__(self, maxlen: int = 2000) -> None:
        self._items: deque[tuple[float, str, str, int, float]] = deque(maxlen=maxlen)
        self._lock = Lock()
        self.started_at = time.time()

    def record(self, method: str, route: str, status: int, duration_ms: float) -> None:
        with self._lock:
            self._items.append((time.time(), method, route, status, duration_ms))

    def summary(self) -> dict[str, Any]:
        with self._lock:
            items = list(self._items)
        by_route: dict[str, list[float]] = {}
        errors = 0
        for _, method, route, status, dur in items:
            by_route.setdefault(f"{method} {route}", []).append(dur)
            if status >= 500:
                errors += 1
        routes = []
        for key, durs in by_route.items():
            durs_sorted = sorted(durs)
            n = len(durs_sorted)
            routes.append(
                {
                    "route": key,
                    "count": n,
                    "p50_ms": round(durs_sorted[n // 2], 1),
                    "p95_ms": round(durs_sorted[min(n - 1, int(n * 0.95))], 1),
                    "max_ms": round(durs_sorted[-1], 1),
                }
            )
        routes.sort(key=lambda r: r["p95_ms"], reverse=True)
        return {
            "requests_recorded": len(items),
            "server_errors": errors,
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "routes": routes[:25],
        }


request_metrics = RequestMetrics()
