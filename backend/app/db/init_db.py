"""Database initialisation: Alembic migrations + reference data."""

from __future__ import annotations

from alembic.config import Config
from sqlalchemy import select

from alembic import command
from app.core.config import BACKEND_DIR, get_settings
from app.db import session as db_session
from app.models import Strategy
from app.strategies.registry import STRATEGIES


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url or get_settings().database_url)
    cfg.attributes["skip_logging"] = True  # keep the application logging configuration
    return cfg


def run_migrations(url: str | None = None) -> None:
    command.upgrade(alembic_config(url), "head")


def seed_reference_data() -> None:
    with db_session.session_scope() as db:
        existing = {s.key: s for s in db.scalars(select(Strategy))}
        for key, cls in STRATEGIES.items():
            d = cls.describe()
            defaults = {p["name"]: p["default"] for p in d["params"]}
            if key in existing:
                s = existing[key]
                s.name, s.description, s.default_params = cls.name, cls.description, defaults
            else:
                db.add(Strategy(key=key, name=cls.name, description=cls.description, default_params=defaults))


def init_db(url: str | None = None) -> None:
    run_migrations(url)
    seed_reference_data()
