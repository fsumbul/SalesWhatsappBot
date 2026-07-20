"""Pytest configuration.

Pure-logic tests (dedup, scoring, health) need no database. Tests that exercise
real query/RLS behavior (compliance engine, tenant isolation) use the
`db_session` / `db_engine` fixtures below, which point at a disposable
Postgres reachable via `LEADPULSE_TEST_DATABASE_URL` (defaults to the
docker-compose dev DB port). Schema must already be migrated (`alembic
upgrade head`) against that database before running DB-backed tests.
"""

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Populates Base.metadata with every ORM module, so SQLAlchemy's mapper
# registry can resolve cross-module foreign keys (e.g. leads.sector_id ->
# sectors.id) for any DB-backed test, regardless of which modules that test
# imports directly. Same registry alembic/env.py and main.py use.
from src import models_registry  # noqa: F401 - populates Base.metadata

# Ensure a valid secret key exists before Settings loads
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-with-enough-length-1234567890")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://leadpulse:leadpulse_dev@localhost:5432/leadpulse_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/10")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/11")
os.environ.setdefault("APP_ENV", "test")

# Deliberately independent of `src.core.db.get_engine()` / global Settings, so
# DB-backed tests never risk touching whatever DATABASE_URL happens to be
# configured in the environment (e.g. a developer's real dev DB).
#
# Defaults to the restricted `leadpulse_app` role, not the Postgres
# superuser: RLS is never enforced for a superuser connection, so testing
# against one would let tenant-isolation bugs pass silently. Run migrations
# (as the superuser) against this database first — see
# tests/test_rls_isolation.py and docs/architecture.md.
TEST_DATABASE_URL = os.environ.get(
    "LEADPULSE_TEST_DATABASE_URL",
    "postgresql+asyncpg://leadpulse_app:leadpulse_app_dev@localhost:5433/leadpulse_test",
)


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    # Function-scoped, not session-scoped: pytest-asyncio gives each test its
    # own event loop, and an AsyncEngine's connection pool is bound to the
    # loop that created it — sharing one engine across tests intermittently
    # breaks with cross-loop errors.
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)
    async with sessionmaker() as session:
        yield session


@pytest_asyncio.fixture
def new_tenant_id() -> UUID:
    """A fresh tenant id per test — cheaper than truncating tables between tests."""
    return uuid4()
