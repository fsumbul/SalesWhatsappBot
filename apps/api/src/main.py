"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import models_registry  # noqa: F401 - populates Base.metadata
from src.core.config import get_settings
from src.core.db import dispose_engine
from src.core.errors import DomainError
from src.core.logging import configure_logging
from src.core.security import JWTError, decode_token
from src.integrations.llm import LLMMessage, LLMNotConfiguredError, get_llm_client
from src.modules.agents.router import router as agents_router
from src.modules.auth.router import router as auth_router
from src.modules.auth.router import users_router
from src.modules.compliance.router import opt_outs_router
from src.modules.compliance.router import router as compliance_router
from src.modules.discovery.router import campaigns_router, leads_router
from src.modules.legal.router import router as legal_router
from src.modules.outreach.router import (
    conversations_router,
    outreach_router,
    senders_router,
    templates_router,
)
from src.modules.outreach.webhooks import router as whatsapp_webhook_router
from src.modules.reports.router import router as reports_router
from src.modules.sectors.router import router as sectors_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    configure_logging()
    logger = structlog.get_logger("app")
    settings = get_settings()
    logger.info("app_startup", env=settings.app_env, debug=settings.app_debug)
    configuration_errors = settings.production_runtime_errors()
    if configuration_errors:
        raise RuntimeError(
            "Production runtime configuration is invalid: " + "; ".join(configuration_errors)
        )
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("app_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="LeadPulse API",
        version="0.1.0",
        description="Multi-tenant B2B lead generation & WhatsApp outreach platform",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Meta retrieves session-message media from a public HTTPS URL. Keep the
    # files deployment-owned and immutable so a customer reply never depends
    # on scraping or hot-linking a mutable product page at send time.
    media_directory = Path(__file__).resolve().parents[1] / "public" / "media"
    app.mount("/media", StaticFiles(directory=media_directory), name="media")

    # DBSessionDep opens its session (and sets the RLS `app.current_tenant`
    # GUC) from `request.state.tenant_id`. FastAPI resolves sibling
    # dependencies in parameter order, and most routes declare `db` before
    # `claims` — so relying on ClaimsDep to populate `request.state.tenant_id`
    # left the DB session's tenant context unset for the entire request on
    # those routes (silently empty reads, RLS violations on writes). Decoding
    # the token here, before routing, makes tenant_id available regardless of
    # parameter order. Errors are swallowed: this is best-effort context for
    # RLS, not authentication — ClaimsDep still enforces and rejects bad/
    # missing tokens with a proper 401.
    @app.middleware("http")
    async def _tenant_context_middleware(request: Request, call_next: Any) -> Any:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                claims = decode_token(auth[7:])
                if claims.get("type") == "access" and claims.get("tid"):
                    request.state.tenant_id = UUID(claims["tid"])
            except (JWTError, ValueError):
                pass
        return await call_next(request)

    # --- Global domain-error handler ---
    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        status_map = {
            "NotFoundError": 404,
            "ConflictError": 409,
            "ForbiddenError": 403,
            "UnauthorizedError": 401,
            "ValidationError": 422,
            "ComplianceBlockError": 451,
        }
        status_code = status_map.get(exc.__class__.__name__, 400)
        return JSONResponse(
            status_code=status_code,
            content={"error": exc.__class__.__name__, "detail": str(exc)},
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "HTTPException", "detail": exc.detail},
        )

    # --- Meta ---
    @app.get("/healthz", tags=["meta"])
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": app.version})

    @app.get("/", tags=["meta"])
    async def root() -> JSONResponse:
        return JSONResponse({"name": "LeadPulse API", "version": app.version, "docs": "/docs"})

    # --- Unauthenticated raw LLM test chat ---
    # No tenant/agent/session, no JSON-envelope contract — just plain text
    # in, plain text out. Exists so the LLM itself (model choice, prompt,
    # latency) can be tried directly without the multi-tenant auth flow or
    # the agent-builder's structured-output requirements getting in the way.
    class ChatTestMessage(BaseModel):
        role: Literal["user", "assistant"]
        content: str

    class ChatTestRequest(BaseModel):
        messages: list[ChatTestMessage]

    class ChatTestResponse(BaseModel):
        reply: str

    @app.post("/api/v1/chat/test", tags=["meta"])
    async def chat_test(body: ChatTestRequest) -> ChatTestResponse:
        if settings.is_production:
            raise HTTPException(status_code=404, detail="not found")
        llm_messages = [LLMMessage(role=m.role, content=m.content) for m in body.messages]
        try:
            reply = await get_llm_client().complete(
                llm_messages,
                system=("Sen bir WhatsApp satış asistanısın. Türkçe, kısa ve doğal cevaplar ver."),  # noqa: RUF001
            )
        except LLMNotConfiguredError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        return ChatTestResponse(reply=reply)

    # --- API v1 routers ---
    api_prefix = "/api/v1"
    app.include_router(auth_router, prefix=api_prefix)
    app.include_router(agents_router, prefix=api_prefix)
    app.include_router(users_router, prefix=api_prefix)
    app.include_router(sectors_router, prefix=api_prefix)
    app.include_router(campaigns_router, prefix=api_prefix)
    app.include_router(leads_router, prefix=api_prefix)
    app.include_router(opt_outs_router, prefix=api_prefix)
    app.include_router(compliance_router, prefix=api_prefix)
    app.include_router(templates_router, prefix=api_prefix)
    app.include_router(senders_router, prefix=api_prefix)
    app.include_router(outreach_router, prefix=api_prefix)
    app.include_router(conversations_router, prefix=api_prefix)
    app.include_router(reports_router, prefix=api_prefix)

    # Public webhook (no prefix; Meta needs a fixed URL)
    app.include_router(whatsapp_webhook_router)
    app.include_router(legal_router)

    return app


app = create_app()
