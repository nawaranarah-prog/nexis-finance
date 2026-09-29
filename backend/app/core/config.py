"""Application configuration loaded from environment variables.

All settings are prefixed with ``NEXIS_``. Nothing secret is hardcoded; the
defaults are safe for local development only.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
APP_VERSION = "1.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXIS_",
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = Field(default=f"sqlite:///{(PROJECT_DIR / 'data' / 'nexis.db').as_posix()}")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    reports_dir: Path = PROJECT_DIR / "data" / "reports"

    public_provider_enabled: bool = False
    public_provider_timeout_seconds: float = 15.0

    max_upload_mb: int = 20
    risk_free_rate: float = 0.02
    trading_days: int = 252

    job_workers: int = 2

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_path(cls, v: str) -> str:
        # Relative SQLite paths are resolved against the backend directory so the
        # app behaves the same regardless of the current working directory.
        prefix = "sqlite:///"
        if v.startswith(prefix) and not v.startswith("sqlite:///:memory:"):
            raw = v[len(prefix) :]
            p = Path(raw)
            if not p.is_absolute():
                p = (BACKEND_DIR / p).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            return prefix + p.as_posix()
        return v

    @field_validator("reports_dir")
    @classmethod
    def _resolve_reports_dir(cls, v: Path) -> Path:
        p = v if v.is_absolute() else (BACKEND_DIR / v).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
