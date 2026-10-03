"""Application configuration loaded from environment variables.

All settings are prefixed with ``NEXIS_``. Nothing secret is hardcoded; the
defaults are safe for local development only.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
APP_VERSION = "1.0.0"
# Serverless hosts (Vercel) only allow writes under /tmp.
ON_SERVERLESS = bool(os.environ.get("VERCEL"))


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

    database_url: str = Field(
        default=f"sqlite:///{(PROJECT_DIR / 'data' / 'nexis.db').as_posix()}",
        # Managed Postgres integrations (Neon, Vercel) expose DATABASE_URL / POSTGRES_URL.
        validation_alias=AliasChoices("NEXIS_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL"),
    )
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    reports_dir: Path = Path("/tmp/nexis-reports") if ON_SERVERLESS else PROJECT_DIR / "data" / "reports"

    public_provider_enabled: bool = True
    public_provider_timeout_seconds: float = 15.0
    # Keyless public sources (US Treasury, World Bank, SEC EDGAR) and keyed ones (FRED, Alpaca).
    external_data_enabled: bool = True
    # SEC fair-access policy requires a descriptive User-Agent with contact details.
    sec_user_agent: str = "NexisFinance research contact@example.com"

    # Fernet key (urlsafe base64, 32 bytes) used to encrypt connection credentials at rest.
    # Development falls back to a generated key in backend/.nexis_secret (git-ignored).
    secret_key: str | None = None

    max_upload_mb: int = 20
    risk_free_rate: float = 0.02
    trading_days: int = 252

    job_workers: int = 2
    # Run background jobs inside the request (serverless platforms freeze threads after the response).
    jobs_inline: bool = ON_SERVERLESS
    # Public shared deployment: every visitor sees the same workspace, so stored credentials are refused.
    public_instance: bool = False

    # Language model for the AI advisor and report narratives (see app/services/llm.py for credential order).
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    # Comma-separated, strongest first; the Vercel AI Gateway free tier serves these OpenAI models.
    llm_model: str = "openai/gpt-5.1,openai/gpt-5,openai/gpt-4.1"
    # Vercel Cron sends Authorization: Bearer <CRON_SECRET> when this is set.
    cron_secret: str | None = Field(default=None, validation_alias=AliasChoices("NEXIS_CRON_SECRET", "CRON_SECRET"))
    # Public origin of the web app (used for OAuth redirect URIs), e.g. https://nexis-finance-five.vercel.app
    public_url: str | None = None
    # Sign in with Google (enabled when both are set; see app/services/oauth.py).
    google_client_id: str | None = None
    google_client_secret: str | None = None
    # Reddit app (reddit.com/prefs/apps, type "web app"): optional sign-in / account linking only.
    # Nexis Pulse does not read Reddit. Redirect URI: {public_url}/api/auth/oauth/reddit/callback
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    # X app (developer.x.com, OAuth 2.0 confidential client): signs people in / links accounts.
    # Redirect URI: {public_url}/api/auth/oauth/x/callback
    x_client_id: str | None = None
    x_client_secret: str | None = None
    # SMS one-time codes for phone sign-up/sign-in via Twilio Verify (optional; without them phone accounts use a password).
    twilio_account_sid: str | None = Field(
        default=None, validation_alias=AliasChoices("NEXIS_TWILIO_ACCOUNT_SID", "TWILIO_ACCOUNT_SID")
    )
    twilio_auth_token: str | None = Field(
        default=None, validation_alias=AliasChoices("NEXIS_TWILIO_AUTH_TOKEN", "TWILIO_AUTH_TOKEN")
    )
    twilio_verify_sid: str | None = Field(
        default=None, validation_alias=AliasChoices("NEXIS_TWILIO_VERIFY_SID", "TWILIO_VERIFY_SERVICE_SID")
    )

    # Abuse protection for public endpoints (per client IP, per hour).
    advisor_requests_per_hour: int = 20
    posts_per_hour: int = 12
    # Seed the demo database in the background when it is empty (hosted demo deployments).
    auto_seed: bool = False

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_path(cls, v: str) -> str:
        # Relative SQLite paths are resolved against the backend directory so the
        # app behaves the same regardless of the current working directory.
        for scheme in ("postgres://", "postgresql://"):
            if v.startswith(scheme):
                return "postgresql+psycopg://" + v[len(scheme) :]
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
