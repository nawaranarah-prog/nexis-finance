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

    # Nexis Pulse market debate. The AI narrative uses the AI client above (no extra service); these limits keep
    # usage inside free tiers. One asset's narrative costs one model call; scores never need a model.
    pulse_model: str = "openai/gpt-4.1-mini,openai/gpt-4.1"
    # A page view recomputes an asset's analysis (cheap, cached feeds) when it is older than this.
    pulse_fresh_minutes: int = 30
    # The AI narrative for one asset is rewritten at most this often, and only when its evidence changed.
    pulse_ai_refresh_minutes: int = 180
    pulse_ai_daily_limit: int = 60
    # Background refresh (cron / page nudges) runs at most this often.
    pulse_tick_minutes: int = 40
    # Email delivery for digests (not connected; digests are prepared and kept until a provider is added).
    email_provider: str | None = None

    # Abuse protection for public endpoints (per client IP, per hour).
    advisor_requests_per_hour: int = 20
    posts_per_hour: int = 12
    # Anonymous Pulse participation, per account.
    pulse_discussions_per_day: int = 8
    pulse_comments_per_hour: int = 30
    pulse_reactions_per_hour: int = 200
    pulse_reports_per_day: int = 30
    # Public discussions collected from other sites (usernames removed, short linked excerpts). Each can be switched off.
    # Reddit collection needs Reddit's written permission (User Agreement; Data API Terms for commercial use). Off until then.
    pulse_public_reddit: bool = False
    pulse_public_hn: bool = True  # Hacker News (public Algolia search API)
    pulse_public_stocktwits: bool = True  # StockTwits public symbol streams
    # Nexis editorial discussions the background engine may publish or update per run.
    pulse_editorial_per_tick: int = 3
    # ---- Nexis Pro billing (Stripe Checkout + Billing). Unset keys mean billing is off: Pro can't be bought and
    # the site says so instead of showing prices. The secret key never reaches the browser.
    stripe_secret_key: str | None = Field(default=None, validation_alias=AliasChoices("NEXIS_STRIPE_SECRET_KEY", "STRIPE_SECRET_KEY"))
    stripe_webhook_secret: str | None = Field(default=None, validation_alias=AliasChoices("NEXIS_STRIPE_WEBHOOK_SECRET", "STRIPE_WEBHOOK_SECRET"))
    # The two plans on sale (monthly, AED). New names on purpose: the earlier USD price ids can't be picked up by mistake.
    stripe_plus_price_id: str | None = Field(
        default=None, validation_alias=AliasChoices("NEXIS_STRIPE_PLUS_MONTHLY_AED_PRICE_ID", "STRIPE_PLUS_MONTHLY_AED_PRICE_ID")
    )
    stripe_pro_price_id: str | None = Field(
        default=None, validation_alias=AliasChoices("NEXIS_STRIPE_PRO_MONTHLY_AED_PRICE_ID", "STRIPE_PRO_MONTHLY_AED_PRICE_ID")
    )
    # Earlier Nexis Pro prices (USD monthly/yearly). No longer sold; subscriptions on them are recognised as Nexis Pro.
    stripe_pro_monthly_price_id: str | None = Field(
        default=None, validation_alias=AliasChoices("NEXIS_STRIPE_PRO_MONTHLY_PRICE_ID", "STRIPE_PRO_MONTHLY_PRICE_ID")
    )
    stripe_pro_yearly_price_id: str | None = Field(
        default=None, validation_alias=AliasChoices("NEXIS_STRIPE_PRO_YEARLY_PRICE_ID", "STRIPE_PRO_YEARLY_PRICE_ID")
    )
    # Wallets shown on the pricing page (comma-separated, e.g. "Apple Pay,Google Pay"). Leave empty until they're
    # enabled in the Stripe Dashboard and verified at checkout; cards are always shown.
    billing_wallets: str = ""
    # While production uses Stripe TEST keys, only these accounts (comma-separated emails) may check out — anyone else
    # could otherwise get Pro with Stripe's public test card. Ignored once live keys (sk_live_...) are set.
    billing_test_emails: str = ""
    # Plan prices and limits live in app/core/plans.py (one source), not here.
    # The public site, used for canonical URLs, sitemaps and the cross-site request check.
    public_site_url: str = "https://nexis-finance-five.vercel.app"
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
