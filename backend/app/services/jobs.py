"""Minimal background job runner for long-running research operations.

Jobs are persisted in the ``jobs`` table so progress survives page reloads and failures are
visible on the System Health page. Work runs on a small thread pool; each job gets its own
database session. For production scale this would be replaced by a proper queue (e.g. Celery or
RQ); the interface (submit → poll) would stay the same.
"""

from __future__ import annotations

import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError, NotFoundError
from app.core.logging import get_logger, log_event
from app.db import session as db_session
from app.db.base import utcnow
from app.models import Job

log = get_logger(__name__)
JobFn = Callable[[Session, dict[str, Any], Callable[[float, str | None], None]], dict[str, Any]]

_executor: ThreadPoolExecutor | None = None
INLINE = get_settings().jobs_inline  # tests may also set this to run synchronously


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=get_settings().job_workers, thread_name_prefix="nexis-job")
    return _executor


def submit(db: Session, job_type: str, params: dict[str, Any], fn: JobFn) -> Job:
    job = Job(id=str(uuid.uuid4()), job_type=job_type, status="queued", progress=0.0, params=params, message="queued")
    db.add(job)
    db.commit()
    if INLINE:
        _run(job.id, fn, params)
        db.refresh(job)
    else:
        _pool().submit(_run, job.id, fn, params)
    return job


def _run(job_id: str, fn: JobFn, params: dict[str, Any]) -> None:
    t0 = time.perf_counter()
    db = db_session.SessionLocal()
    last_update = [0.0]
    try:
        job = db.get(Job, job_id)
        assert job is not None
        job.status, job.started_at, job.message = "running", utcnow(), "running"
        db.commit()

        def progress(p: float, message: str | None = None) -> None:
            now = time.perf_counter()
            if now - last_update[0] < 0.4 and p < 1.0:
                return
            last_update[0] = now
            with db_session.SessionLocal() as s:
                j = s.get(Job, job_id)
                if j is not None:
                    j.progress = float(max(0.0, min(1.0, p)))
                    if message:
                        j.message = message[:300]
                    s.commit()

        result = fn(db, params, progress)
        job = db.get(Job, job_id)
        assert job is not None
        job.status, job.progress, job.result = "succeeded", 1.0, result
        job.message = "completed"
        job.finished_at = utcnow()
        db.commit()
        log_event(
            log, "job succeeded", job_id=job_id, job_type=job.job_type, duration_ms=round((time.perf_counter() - t0) * 1000)
        )
    except Exception as exc:
        db.rollback()
        msg = exc.message if isinstance(exc, NexisError) else f"internal error ({exc.__class__.__name__})"
        if not isinstance(exc, NexisError):
            log.error("job %s failed\n%s", job_id, traceback.format_exc())
        job = db.get(Job, job_id)
        if job is not None:
            job.status, job.error, job.message, job.finished_at = "failed", msg, "failed", utcnow()
            db.commit()
        log_event(log, "job failed", job_id=job_id, error=msg)
    finally:
        db.close()


def get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    return job


def list_jobs(db: Session, limit: int = 50, status: str | None = None) -> list[Job]:
    q = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status:
        q = q.where(Job.status == status)
    return list(db.scalars(q))


def serialize(j: Job) -> dict[str, Any]:
    return {
        "id": j.id,
        "job_type": j.job_type,
        "status": j.status,
        "progress": j.progress,
        "message": j.message,
        "result": j.result,
        "error": j.error,
        "created_at": j.created_at.isoformat(),
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }
