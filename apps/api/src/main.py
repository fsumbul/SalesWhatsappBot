"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src import models_registry  # noqa: F401 - populates Base.metadata
from src.core.config import get_settings
from src.core.db import dispose_engine
from src.core.errors import DomainError
from src.core.logging import configure_logging
from src.modules.agents.router import router as agents_router
from src.modules.auth.router import router as auth_router
from src.modules.auth.router import users_router
from src.modules.compliance.router import opt_outs_router
from src.modules.compliance.router import router as compliance_router
from src.modules.discovery.router import campaigns_router, leads_router
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
        return JSONResponse(
            {"name": "LeadPulse API", "version": app.version, "docs": "/docs"}
        )

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

    return app


app = create_app()
