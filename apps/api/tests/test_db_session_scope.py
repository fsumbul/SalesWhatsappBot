"""Regression tests for pooled PostgreSQL tenant context handling."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from src.core.db import reset_tenant_context, set_tenant_context


async def test_tenant_context_follows_logical_session_across_commits(
    db_engine: AsyncEngine,
) -> None:
    """A pooled connection reused by B must be restored to A for A's next tx."""

    try:
        async with db_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError):
        pytest.skip("test database not reachable — see tests/conftest.py")

    engine = create_async_engine(
        db_engine.url,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    tenant_a = uuid4()
    tenant_b = uuid4()

    try:
        async with sessions() as session_a, sessions() as session_b:
            await set_tenant_context(session_a, tenant_a)
            assert (
                await session_a.execute(text("SELECT current_setting('app.current_tenant', true)"))
            ).scalar_one() == str(tenant_a)
            await session_a.commit()

            # pool_size=1 guarantees B now uses the physical connection that A
            # released. This was the old implementation's deterministic race.
            await set_tenant_context(session_b, tenant_b)
            assert (
                await session_b.execute(text("SELECT current_setting('app.current_tenant', true)"))
            ).scalar_one() == str(tenant_b)
            await session_b.commit()

            # A starts a new transaction on that same connection. Its logical
            # tenant must be re-applied instead of inheriting B's value.
            assert (
                await session_a.execute(text("SELECT current_setting('app.current_tenant', true)"))
            ).scalar_one() == str(tenant_a)
            await reset_tenant_context(session_a)

        # SET LOCAL must leave no tenant value on the pooled connection after
        # the transaction ends.
        async with engine.connect() as connection:
            leaked = (
                await connection.execute(text("SELECT current_setting('app.current_tenant', true)"))
            ).scalar_one_or_none()
            assert leaked in {None, ""}
    finally:
        await engine.dispose()
