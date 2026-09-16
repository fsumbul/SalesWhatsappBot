"""Application settings loaded from environment variables.

All configuration goes through `Settings`. Do NOT read env vars directly elsewhere.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

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


ModelEndpointKind = Literal[
    "llm", "embedding", "rerank", "classify", "ocr", "layout", "vision", "asr", "translate"
]


@dataclass(frozen=True)
class ModelEndpoint:
    """One model service that receives customer or tenant data.

    ``name`` is the environment variable that configures it, ``role`` a stable
    key used by preflight output and audit trails, ``nim`` whether the service
    is a self-hosted NVIDIA NIM container (which exposes ``/v1/health/ready``).
    """

    name: str
    role: str
    url: str
    kind: ModelEndpointKind
    nim: bool = False

    @property
    def host(self) -> str:
        return (urlparse(self.url).hostname or "").lower()


def host_is_denied(host: str, denylist: list[str]) -> bool:
    """Exact or parent-domain match against the public-endpoint denylist."""

    host = host.lower().rstrip(".")
    return any(host == denied or host.endswith("." + denied) for denied in denylist if denied)


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
    # Ollama accepts either its root URL or a URL ending in /v1. For
    # chat_compatible uses the API base URL, normally ending in /v1.
    llm_base_url: str = "http://localhost:11434/v1"
    # Context window requested from Ollama. Hybrid generation with several
    # evidence chunks needs 8192; strict decisions fit comfortably in 4096.
    llm_num_ctx: int = 4096
    # Kill switch for model-written descriptive answers (ADR-003). Off means
    # every hybrid tenant behaves exactly like strict.
    hybrid_generation_enabled: bool = True

    # --- NVIDIA NIM harness agents (self-hosted) ---
    # Guardrail, OCR, vision, embedding, rerank, ASR and translation adapters
    # talk to NIM containers the operator runs on their own GPU host. The
    # public build.nvidia.com trial endpoints log their inputs, so every
    # endpoint that receives customer or tenant data is refused when it points
    # at one of the hosts below (``model_endpoint_boundary_errors``). The
    # shared key is optional; per-feature ``*_API_KEY`` fields take precedence.
    # See docs/nvidia-nim-harness-agents-plan-2026-09-16.md.
    nim_api_key: str = ""
    nim_timeout_seconds: float = 10.0
    nim_public_host_denylist: str = "integrate.api.nvidia.com,ai.api.nvidia.com,api.nvcf.nvidia.com"

    # --- Guardrail gate (plan WP1) ---
    # Customer messages are classified before any model call, document chunks
    # before extraction. ``closed`` means an unreachable classifier yields the
    # approved safe turn instead of calling the model (mandatory in
    # production). Topic control only flags by default: Turkish is not an
    # officially supported language of the NemoGuard models and a wrongly
    # blocked sales question costs more than a tolerated off-topic one.
    guardrail_enabled: bool = False
    guardrail_fail_mode: Literal["closed", "open"] = "closed"
    guardrail_timeout_seconds: float = 2.5
    guardrail_checks: str = "jailbreak,content_safety,topic_control"
    guardrail_ingest_enabled: bool = True
    guardrail_jailbreak_base_url: str = ""
    guardrail_jailbreak_api_key: str = ""
    # Signed score in [-1, 1]; positive means jailbreak. Raise to demand margin.
    guardrail_jailbreak_threshold: float = 0.0
    guardrail_content_safety_base_url: str = ""
    guardrail_content_safety_model: str = "llama-3.1-nemoguard-8b-content-safety"
    guardrail_content_safety_api_key: str = ""
    # Aegis categories that block on their own; S9 (PII), S12 (profanity),
    # S13/S14 and the political/advice categories only flag.
    guardrail_block_categories: str = "S1,S2,S3,S4,S5,S6,S7,S8,S10,S11,S15,S16,S17,S22"
    guardrail_topic_control_base_url: str = ""
    guardrail_topic_control_model: str = "llama-3.1-nemoguard-8b-topic-control"
    guardrail_topic_control_api_key: str = ""
    guardrail_topic_control_mode: Literal["flag", "block"] = "flag"
    guardrail_topic_min_tokens: int = 3

    # --- OCR / layout chain for scanned PDFs (plan WP2) ---
    # Pages whose text layer is shorter than ``min_text_chars`` are rasterized
    # locally (pypdfium2) and sent to the operator's NeMo Retriever containers:
    # page-elements → OCR for text regions, table-structure + OCR for tables.
    # Layout is optional (whole-page OCR without it); OCR is required.
    knowledge_ocr_enabled: bool = False
    knowledge_ocr_base_url: str = ""
    knowledge_ocr_api_key: str = ""
    knowledge_ocr_model: str = "nemotron-ocr-v2"
    # Route of the OCR container (pin it from GET /v1/openapi.json).
    knowledge_ocr_path: str = "/v1/infer"
    knowledge_layout_base_url: str = ""
    knowledge_layout_api_key: str = ""
    knowledge_ocr_min_text_chars: int = 40
    knowledge_ocr_dpi: int = 150
    knowledge_ocr_max_pages_per_document: int = 60
    knowledge_ocr_min_confidence: float = 0.3

    # --- Knowledge retrieval / GraphRAG (ADR-002) ---
    # ``lexical`` keeps the in-process keyword retriever inside
    # ``company_runtime``. ``falkordb`` turns on the hybrid graph + vector +
    # full-text retriever (BGE-M3 embeddings, optional cross-encoder rerank)
    # with the lexical retriever as an automatic fallback. Retrieval only
    # proposes approved fact candidates; it never renders customer text.
    knowledge_backend: Literal["lexical", "falkordb"] = "lexical"
    falkordb_host: str = "localhost"
    falkordb_port: int = 6381
    falkordb_username: str = ""
    falkordb_password: str = ""
    # Embedding profile. Only ``ollama`` is implemented; empty keeps embeddings
    # fail-closed (the graph retriever then reports itself unavailable).
    embedding_provider: Literal["", "ollama"] = ""
    embedding_model: str = "bge-m3"
    # Empty derives the Ollama root from ``llm_base_url``.
    embedding_base_url: str = ""
    embedding_dimension: int = 1024
    # Cross-encoder rerank of the fused candidate pool (sentence-transformers).
    reranker_enabled: bool = False
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_device: str = "auto"
    retrieval_max_candidates: int = 12
    retrieval_rerank_pool: int = 24
    retrieval_timeout_seconds: float = 4.0
    # Background per-customer conversation memory graph (decision input only).
    memory_enrichment_enabled: bool = True
    # --- Self-service knowledge sources (ADR-003) ---
    # Auto-publish candidates from the tenant's own documents/website above
    # the threshold; protected topics (price/stock/delivery/warranty/...) are
    # never auto-published. Everything stays revocable from chat/panel.
    knowledge_auto_publish: bool = True
    knowledge_auto_publish_threshold: float = 0.75
    knowledge_publish_target: Literal["draft", "live"] = "live"
    knowledge_document_max_bytes: int = 20 * 1024 * 1024
    knowledge_crawl_max_pages: int = 60
    knowledge_crawl_max_depth: int = 3
    knowledge_crawl_user_agent: str = "LeadPulseBot/1.0"
    knowledge_media_min_pixels: int = 300
    knowledge_media_max_per_subject: int = 3
    # Public origin Meta fetches product images from; empty derives app_base_url.
    knowledge_public_media_base_url: str = ""

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

    @property
    def nim_public_host_denylist_list(self) -> list[str]:
        return [h.strip().lower() for h in self.nim_public_host_denylist.split(",") if h.strip()]

    @property
    def guardrail_checks_list(self) -> list[str]:
        return [c.strip().lower() for c in self.guardrail_checks.split(",") if c.strip()]

    @property
    def guardrail_block_categories_list(self) -> list[str]:
        return [c.strip().upper() for c in self.guardrail_block_categories.split(",") if c.strip()]

    def model_endpoints(self) -> list[ModelEndpoint]:
        """Every configured model service that may see customer or tenant data.

        Feature packages append their endpoints here so the boundary gate and
        the preflight probe never fall out of sync with the settings.
        """

        endpoints: list[ModelEndpoint] = []
        if self.llm_provider in {"ollama", "chat_compatible"}:
            endpoints.append(
                ModelEndpoint(
                    name="LLM_BASE_URL", role="llm.customer", url=self.llm_base_url, kind="llm"
                )
            )
        if self.embedding_provider:
            endpoints.append(
                ModelEndpoint(
                    name="EMBEDDING_BASE_URL",
                    role="embedding",
                    url=self.embedding_base_url or self.llm_base_url,
                    kind="embedding",
                )
            )
        if self.guardrail_enabled:
            checks = self.guardrail_checks_list
            if "jailbreak" in checks:
                endpoints.append(
                    ModelEndpoint(
                        name="GUARDRAIL_JAILBREAK_BASE_URL",
                        role="guardrail.jailbreak",
                        url=self.guardrail_jailbreak_base_url,
                        kind="classify",
                        nim=True,
                    )
                )
            if "content_safety" in checks:
                endpoints.append(
                    ModelEndpoint(
                        name="GUARDRAIL_CONTENT_SAFETY_BASE_URL",
                        role="guardrail.content_safety",
                        url=self.guardrail_content_safety_base_url,
                        kind="llm",
                        nim=True,
                    )
                )
            if "topic_control" in checks:
                endpoints.append(
                    ModelEndpoint(
                        name="GUARDRAIL_TOPIC_CONTROL_BASE_URL",
                        role="guardrail.topic_control",
                        url=self.guardrail_topic_control_base_url,
                        kind="llm",
                        nim=True,
                    )
                )
        if self.knowledge_ocr_enabled:
            endpoints.append(
                ModelEndpoint(
                    name="KNOWLEDGE_OCR_BASE_URL",
                    role="knowledge.ocr",
                    url=self.knowledge_ocr_base_url,
                    kind="ocr",
                    nim=True,
                )
            )
            if self.knowledge_layout_base_url.strip():
                endpoints.append(
                    ModelEndpoint(
                        name="KNOWLEDGE_LAYOUT_BASE_URL",
                        role="knowledge.layout",
                        url=self.knowledge_layout_base_url,
                        kind="layout",
                        nim=True,
                    )
                )
        return endpoints

    def nim_endpoints(self) -> list[ModelEndpoint]:
        return [endpoint for endpoint in self.model_endpoints() if endpoint.nim]

    def model_endpoint_boundary_errors(self) -> list[str]:
        """Names of endpoints that are missing or point at a public trial host."""

        errors: list[str] = []
        denylist = self.nim_public_host_denylist_list
        for endpoint in self.model_endpoints():
            if not endpoint.url.strip():
                errors.append(f"{endpoint.name} is required")
                continue
            if host_is_denied(endpoint.host, denylist):
                errors.append(
                    f"{endpoint.name} must not point at a public model endpoint "
                    "(customer and tenant data stay on self-hosted services)"
                )
        return errors

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
        errors.extend(f"{key} is required" for key, value in required.items() if not value.strip())
        if not self.app_secret_key_security["production_acceptable"]:
            errors.append("APP_SECRET_KEY must be a strong randomly generated value")
        if self.app_debug:
            errors.append("APP_DEBUG must be false")
        if self.llm_provider not in {"ollama", "chat_compatible"}:
            errors.append("LLM_PROVIDER must be ollama or chat_compatible")
        if self.knowledge_backend == "falkordb" and self.embedding_provider != "ollama":
            errors.append("EMBEDDING_PROVIDER must be ollama when KNOWLEDGE_BACKEND=falkordb")
        errors.extend(self.model_endpoint_boundary_errors())
        if self.guardrail_enabled and self.guardrail_fail_mode != "closed":
            errors.append("GUARDRAIL_FAIL_MODE must be closed in production")
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
