"""Async SQLAlchemy engine, session factory, and Base model.

Tenant isolation notes:
- Every tenant-scoped table MUST include a `tenant_id UUID NOT NULL` column.
- Row-Level Security (RLS) policies are applied in migrations.
- `set_tenant_context(session, tenant_id)` sets `app.current_tenant` for the session.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata_naming_convention: ClassVar[dict[str, str]] = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_TENANT_CONTEXT_INFO_KEY = "app.current_tenant"
_SET_LOCAL_TENANT_CONTEXT = text("SELECT set_config('app.current_tenant', :tenant_id, true)")


@event.listens_for(Session, "after_begin")
def _apply_tenant_context_at_transaction_begin(
    session: Session,
    _transaction: Any,
    connection: Any,
) -> None:
    """Apply the logical session's tenant to every PostgreSQL transaction.

    ``AsyncSession.commit()`` releases its physical connection back to the
    pool. A later statement from the same logical session can therefore run
    on a different connection. Keeping the tenant only as a session-scoped
    PostgreSQL GUC is unsafe: another logical session may have changed that
    connection's value in between. Store the tenant on SQLAlchemy's logical
    session and re-apply it transaction-locally whenever a new transaction
    checks out a connection.
    """

    if connection.dialect.name != "postgresql":
        return
    tenant_id = session.info.get(_TENANT_CONTEXT_INFO_KEY)
    connection.execute(
        _SET_LOCAL_TENANT_CONTEXT,
        {"tenant_id": str(tenant_id) if tenant_id is not None else ""},
    )


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

    The value is stored on the logical SQLAlchemy session. The ``after_begin``
    listener above applies it with transaction-local scope on every physical
    connection the session checks out, including after a commit. Applying it
    once here also changes the context immediately when callers switch tenant
    inside an already-active transaction.
    """

    session.info[_TENANT_CONTEXT_INFO_KEY] = tenant_id
    await session.execute(
        _SET_LOCAL_TENANT_CONTEXT,
        {"tenant_id": str(tenant_id) if tenant_id is not None else ""},
    )


async def reset_tenant_context(session: AsyncSession) -> None:
    """End the transaction and remove tenant state from the logical session."""

    await session.rollback()
    session.info.pop(_TENANT_CONTEXT_INFO_KEY, None)


@asynccontextmanager
async def session_scope(tenant_id: UUID | None = None) -> AsyncIterator[AsyncSession]:
    """Yield a DB session whose tenant context is always safely reset."""

    async with get_sessionmaker()() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
        finally:
            await reset_tenant_context(session)


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
    "reset_tenant_context",
    "session_scope",
    "set_tenant_context",
]


def _shut_up_any_static_analyzer() -> Any:  # pragma: no cover
    return None
