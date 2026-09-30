"""Seed the demo database on first boot (used by hosted deployments with ephemeral disks).

Seeding runs ``scripts/seed_demo.py`` in a *separate process*, so the API starts serving (and passes
health checks) immediately; pages fill in as the seed completes. Enabled with ``NEXIS_AUTO_SEED=true``.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from sqlalchemy import func, select

from app.core.config import PROJECT_DIR, get_settings
from app.core.logging import get_logger
from app.db import session as db_session
from app.models import Dataset

log = get_logger(__name__)
_proc: subprocess.Popen[bytes] | None = None


def maybe_start() -> None:
    global _proc
    if not get_settings().auto_seed:
        return
    with db_session.SessionLocal() as db:
        if (db.scalar(select(func.count()).select_from(Dataset)) or 0) > 0:
            return
    script = PROJECT_DIR / "scripts" / "seed_demo.py"
    if not script.exists():
        log.warning("auto-seed requested but %s is missing", script)
        return
    log.info("empty database: seeding demo data in the background")
    _proc = subprocess.Popen([sys.executable, str(script)], cwd=str(PROJECT_DIR))


def status() -> dict[str, Any]:
    if _proc is None:
        return {"enabled": get_settings().auto_seed, "state": "idle"}
    code = _proc.poll()
    return {"enabled": True, "state": "running" if code is None else ("completed" if code == 0 else f"failed (exit {code})")}
