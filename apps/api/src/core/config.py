"""Application settings loaded from environment variables.

All configuration goes through `Settings`. Do NOT read env vars directly elsewhere.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_debug: bool = False
    app_secret_key: str = Field(min_length=32)
    app_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:3000"

    # --- DB ---
    # `database_url` is what the running API/worker processes connect with —
    # must be a restricted, non-superuser role or Postgres Row-Level Security
    # is silently never enforced (Postgres never applies RLS to superusers,
    # not even with FORCE ROW LEVEL SECURITY). `migrations_database_url` is
    # the privileged role used only to run Alembic migrations (which need
    # DDL + role/grant management); it falls back to `database_url` if unset
    # so a single-role setup still works. See docs/architecture.md.
    database_url: PostgresDsn
    migrations_database_url: PostgresDsn | None = None

    # --- Redis / Celery ---
    redis_url: RedisDsn
    celery_broker_url: RedisDsn
    celery_result_backend: RedisDsn

    # --- JWT ---
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7

    # --- CORS ---
    cors_origins: str = "http://localhost:3000"

    # --- External APIs (optional in early phases) ---
    whatsapp_app_secret: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""

    google_places_api_key: str = ""
    serpapi_key: str = ""
    bing_search_key: str = ""

    iys_api_url: str = ""
    iys_api_key: str = ""

    # --- Observability ---
    sentry_dsn: str = ""
    log_level: str = "INFO"

    # --- SMTP ---
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@leadpulse.local"

    @field_validator("cors_origins")
    @classmethod
    def _split_origins(cls, v: str) -> str:
        # Kept as raw string; parsed via `cors_origins_list`
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def whatsapp_webhook_verify_token(self) -> str:
        """Alias for the token Meta echoes on webhook subscription."""
        return self.whatsapp_verify_token


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor. Reset the cache in tests via `get_settings.cache_clear()`."""
    return Settings()  # type: ignore[call-arg]
