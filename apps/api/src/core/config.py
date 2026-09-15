"""Application settings loaded from environment variables.

All configuration goes through `Settings`. Do NOT read env vars directly elsewhere.
"""

import math
import re
from collections import Counter
from functools import lru_cache
from ipaddress import ip_network
from typing import Literal
from uuid import UUID

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_SECRET_PLACEHOLDER_MARKERS = (
    "changeme",
    "defaultsecret",
    "developmentsecret",
    "devsecret",
    "examplesecret",
    "insecure",
    "jwtsecret",
    "placeholder",
    "replaceme",
    "secretkey",
    "testsecret",
    "yoursecret",
)
_KNOWN_WEAK_APP_SECRETS = {
    "abcdefghijklmnopqrstuvwxyz0123456789",
    "0123456789abcdefghijklmnopqrstuvwxyz",
}


def _is_repeated_pattern(value: str) -> bool:
    """Return true when the entire value is a repeated shorter pattern."""

    return any(
        len(value) % width == 0 and value == value[:width] * (len(value) // width)
        for width in range(1, len(value) // 2 + 1)
    )


def _app_secret_key_security(value: str) -> dict[str, bool | int]:
    """Return a sanitized production-strength assessment, never the value."""

    canonical = re.sub(r"[^a-z0-9]", "", value.casefold())
    placeholder_detected = canonical in _KNOWN_WEAK_APP_SECRETS or any(
        marker in canonical for marker in _APP_SECRET_PLACEHOLDER_MARKERS
    )

    counts = Counter(value)
    entropy_per_character = 0.0
    if value:
        entropy_per_character = -sum(
            (count / len(value)) * math.log2(count / len(value)) for count in counts.values()
        )
    estimated_entropy_bits = entropy_per_character * len(value)
    low_entropy_detected = bool(
        len(value) < 32
        or value != value.strip()
        or len(counts) < 10
        or entropy_per_character < 3.0
        or estimated_entropy_bits < 128
        or _is_repeated_pattern(value)
    )
    return {
        "length": len(value),
        "placeholder_detected": placeholder_detected,
        "low_entropy_detected": low_entropy_detected,
        "production_acceptable": not placeholder_detected and not low_entropy_detected,
    }


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

    # Only an explicitly configured reverse proxy may supply the client IP.
    # This is deliberately empty by default: accepting X-Forwarded-For from a
    # direct client lets it choose another user's rate-limit bucket and corrupt
    # the audit IP recorded at login.
    trusted_proxy_cidrs: str = ""

    # Redis is already a required runtime dependency. Keep rate limiting off
    # for unit tests/local ad-hoc use, but require an explicit production
    # enablement (and set it in docker-compose) rather than silently shipping
    # unbounded costly chat/import endpoints.
    api_rate_limit_enabled: bool = False

    # --- Private campaign-import storage ---
    # The API/worker use this S3-compatible endpoint directly. No browser
    # presigned URL is issued in the first import slice, so the default bucket
    # remains private.
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "leadpulse-imports"
    minio_secure: bool = False

    # This is deliberately a concrete rollout guard for campaign-file
    # outreach, not a generic feature-flag platform. Keep it off until the
    # sandbox validation is complete; production canary config can name one
    # tenant, one manager, and a 100-recipient ceiling.
    campaign_imports_enabled: bool = False
    campaign_imports_canary_tenant_ids: str = ""
    campaign_imports_canary_manager_ids: str = ""
    # Parsing accepts up to 10,000 rows, but the first live rollout remains
    # deliberately capped at 100 recipients. Raising this requires an
    # explicit post-canary deployment change, not an accidental env default.
    campaign_imports_max_recipients: int = Field(default=100, ge=1, le=10_000)

    # --- External APIs (optional in early phases) ---
    whatsapp_app_secret: str = ""
    selection_rollout: Literal["disabled", "pilot", "all"] = "disabled"
    selection_pilot_conversation_ids: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_graph_api_version: str = "v20.0"
    # Private deployment binding from tenant UUID to logical Flow references.
    # Source defaults to empty so Flow delivery fails closed.
    whatsapp_tenant_capabilities_json: str = ""
    # Legacy compatibility setting. Runtime selection uses SenderProfile.agent_id.
    whatsapp_agent_slug: str = ""

    google_places_api_key: str = ""
    serpapi_key: str = ""
    bing_search_key: str = ""

    iys_api_url: str = ""
    iys_api_key: str = ""

    # --- Web crawler (Phase D) ---
    # Off by default: unlike the API connectors above, this one drives a real
    # headless browser against arbitrary third-party sites, so it should be
    # an explicit opt-in per deployment.
    web_crawl_enabled: bool = False
    # Comma-separated seed URLs, each containing a literal "{query}" that gets
    # replaced with the URL-encoded search query. No default seeds are
    # shipped: Kompass and Europages — the two directories named in the
    # original roadmap — turned out to disallow generic crawlers (or their
    # most valuable paths) in their own robots.txt, so hardcoding them would
    # either violate the "zero robots.txt violations" requirement or scrape
    # nothing useful. Operators must configure sites they've confirmed permit
    # crawling. See docs/architecture.md "Web crawler (Phase D)".
    web_crawl_seed_urls: str = ""
    # Comma-separated proxy URLs (e.g. http://user:pass@host:port), rotated
    # round-robin per crawl session. Empty = no proxy (direct connection).
    web_crawl_proxies: str = ""

    # --- LLM provider (Phase E2/E3) ---
    # Supported values are ``ollama`` and ``chat_compatible``. The latter
    # works with self-hosted servers exposing the standard Chat Completions
    # contract (vLLM, LocalAI, llama.cpp, and similar). It is not tied to an
    # OS or a particular model runtime. Empty keeps the application fail-closed.
    llm_provider: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # Optional Qwen/vLLM extension. None preserves other compatible APIs.
    llm_enable_thinking: bool | None = None
    # Ollama accepts either its root URL or a URL ending in /v1. For
    # chat_compatible uses the API base URL, normally ending in /v1.
    llm_base_url: str = "http://localhost:11434/v1"

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

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _validate_trusted_proxy_cidrs(cls, v: str) -> str:
        for raw in (item.strip() for item in v.split(",") if item.strip()):
            try:
                ip_network(raw, strict=False)
            except ValueError as exc:
                raise ValueError("TRUSTED_PROXY_CIDRS must contain IP addresses or CIDRs") from exc
        return v

    @field_validator("campaign_imports_canary_tenant_ids", "campaign_imports_canary_manager_ids")
    @classmethod
    def _validate_campaign_import_canary_ids(cls, v: str) -> str:
        for raw in (item.strip() for item in v.split(",") if item.strip()):
            try:
                UUID(raw)
            except ValueError as exc:
                raise ValueError("campaign import canary IDs must be UUIDs") from exc
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_proxy_cidrs_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_proxy_cidrs.split(",") if item.strip()]

    @property
    def campaign_imports_canary_tenant_ids_list(self) -> set[UUID]:
        return {
            UUID(item.strip())
            for item in self.campaign_imports_canary_tenant_ids.split(",")
            if item.strip()
        }

    @property
    def campaign_imports_canary_manager_ids_list(self) -> set[UUID]:
        return {
            UUID(item.strip())
            for item in self.campaign_imports_canary_manager_ids.split(",")
            if item.strip()
        }

    @property
    def web_crawl_seed_urls_list(self) -> list[str]:
        return [u.strip() for u in self.web_crawl_seed_urls.split(",") if u.strip()]

    @property
    def web_crawl_proxies_list(self) -> list[str]:
        return [p.strip() for p in self.web_crawl_proxies.split(",") if p.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def whatsapp_webhook_verify_token(self) -> str:
        """Alias for the token Meta echoes on webhook subscription."""
        return self.whatsapp_verify_token

    @property
    def app_secret_key_security(self) -> dict[str, bool | int]:
        """Sanitized JWT-signing-key checks suitable for preflight output."""

        return _app_secret_key_security(self.app_secret_key)

    def production_runtime_errors(self) -> list[str]:
        """Return only missing/invalid key names, never secret values."""

        if not self.is_production:
            return []
        errors: list[str] = []
        required = {
            "WHATSAPP_APP_SECRET": self.whatsapp_app_secret,
            "WHATSAPP_ACCESS_TOKEN": self.whatsapp_access_token,
            "WHATSAPP_VERIFY_TOKEN": self.whatsapp_verify_token,
            "WHATSAPP_PHONE_NUMBER_ID": self.whatsapp_phone_number_id,
            "WHATSAPP_BUSINESS_ACCOUNT_ID": self.whatsapp_business_account_id,
            "LLM_MODEL": self.llm_model,
            "LLM_BASE_URL": self.llm_base_url,
        }
        # Keep the existing private API deployable while this deliberately
        # disabled canary is not in use. Once file imports are enabled, object
        # storage is a hard production preflight requirement rather than a
        # runtime surprise on a manager upload.
        if self.campaign_imports_enabled:
            required.update(
                {
                    "MINIO_ENDPOINT": self.minio_endpoint,
                    "MINIO_ACCESS_KEY": self.minio_access_key,
                    "MINIO_SECRET_KEY": self.minio_secret_key,
                    "MINIO_BUCKET": self.minio_bucket,
                }
            )
        errors.extend(f"{key} is required" for key, value in required.items() if not value.strip())
        if not self.app_secret_key_security["production_acceptable"]:
            errors.append("APP_SECRET_KEY must be a strong randomly generated value")
        if self.app_debug:
            errors.append("APP_DEBUG must be false")
        if not self.api_rate_limit_enabled:
            errors.append("API_RATE_LIMIT_ENABLED must be true")
        if self.campaign_imports_enabled:
            tenants = self.campaign_imports_canary_tenant_ids_list
            managers = self.campaign_imports_canary_manager_ids_list
            if len(tenants) != 1:
                errors.append("CAMPAIGN_IMPORTS_CANARY_TENANT_IDS must name exactly one tenant")
            if len(managers) != 1:
                errors.append("CAMPAIGN_IMPORTS_CANARY_MANAGER_IDS must name exactly one manager")
            if self.campaign_imports_max_recipients > 100:
                errors.append("CAMPAIGN_IMPORTS_MAX_RECIPIENTS must be at most 100 during canary")
        if self.llm_provider not in {"ollama", "chat_compatible"}:
            errors.append("LLM_PROVIDER must be ollama or chat_compatible")
        version = self.whatsapp_graph_api_version
        if not (
            version.startswith("v")
            and version[1:].replace(".", "", 1).isdigit()
            and version.count(".") == 1
        ):
            errors.append("WHATSAPP_GRAPH_API_VERSION must look like v20.0")
        return errors


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor. Reset the cache in tests via `get_settings.cache_clear()`."""
    return Settings()
