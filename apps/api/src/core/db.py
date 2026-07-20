"""Async SQLAlchemy engine, session factory, and Base model.

Tenant isolation notes:
- Every tenant-scoped table MUST include a `tenant_id UUID NOT NULL` column.
- Row-Level Security (RLS) policies are applied in migrations.
- `set_tenant_context(session, tenant_id)` sets `app.current_tenant` for the session.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata_naming_convention: dict[str, str] = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            str(settings.database_url),
            echo=settings.app_debug,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield an AsyncSession per request."""
    async with get_sessionmaker()() as session:
        yield session


async def set_tenant_context(session: AsyncSession, tenant_id: UUID | None) -> None:
    """Set the `app.current_tenant` GUC for Row-Level Security.

    Pass `None` to clear (superuser / cross-tenant queries).
    """
    if tenant_id is None:
        await session.execute(text("SELECT set_config('app.current_tenant', '', true)"))
    else:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": str(tenant_id)},
        )


async def dispose_engine() -> None:
    """Dispose the engine (call on app shutdown)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


# Convenience alias for typing in DI-heavy code
DBSession = AsyncSession
__all__: list[str] = [
    "Base",
    "DBSession",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "set_tenant_context",
]


def _shut_up_any_static_analyzer() -> Any:  # pragma: no cover
    return None
