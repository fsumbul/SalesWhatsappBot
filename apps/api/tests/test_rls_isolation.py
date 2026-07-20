"""Regression test for Postgres Row-Level Security tenant isolation.

This is the single most important test in the suite: every tenant-scoped
table relies on RLS (`FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy)
to keep tenants apart, even for the DB owner role. If this test starts
failing, tenant data is leaking across tenants — treat it as a P0.

Requires a migrated Postgres reachable at `LEADPULSE_TEST_DATABASE_URL`
(see conftest.py). Skips cleanly if that database isn't reachable, so the
pure-logic suite keeps working without Docker.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import set_tenant_context
from src.modules.compliance.models import OptOut, OptOutSource


async def _db_reachable(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _add_opt_out(session: AsyncSession, tenant_id: UUID, phone: str) -> None:
    await set_tenant_context(session, tenant_id)
    session.add(
        OptOut(tenant_id=tenant_id, phone_e164=phone, source=OptOutSource.MANUAL, reason="test")
    )
    await session.commit()


async def test_tenant_cannot_see_another_tenants_opt_outs(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_a, tenant_b = uuid4(), uuid4()
    await _add_opt_out(db_session, tenant_a, "+905551110000")
    await _add_opt_out(db_session, tenant_b, "+905552220000")

    # Tenant A's context must only ever see tenant A's row.
    await set_tenant_context(db_session, tenant_a)
    rows = (await db_session.execute(select(OptOut))).scalars().all()
    assert {r.tenant_id for r in rows} == {tenant_a}
    assert {r.phone_e164 for r in rows} == {"+905551110000"}

    # Tenant B's context must only ever see tenant B's row.
    await set_tenant_context(db_session, tenant_b)
    rows = (await db_session.execute(select(OptOut))).scalars().all()
    assert {r.tenant_id for r in rows} == {tenant_b}


async def test_no_tenant_context_denies_all_rows(db_session: AsyncSession) -> None:
    """With no `app.current_tenant` GUC set, RLS must default to deny-all,
    not accidentally expose every tenant's rows."""
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_a = uuid4()
    await _add_opt_out(db_session, tenant_a, "+905553330000")

    await set_tenant_context(db_session, None)
    rows = (await db_session.execute(select(OptOut))).scalars().all()
    assert rows == []


async def test_cross_tenant_insert_is_rejected(db_session: AsyncSession) -> None:
    """The RLS WITH CHECK clause must reject inserting a row tagged with a
    different tenant than the active session context."""
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_a, tenant_b = uuid4(), uuid4()
    await set_tenant_context(db_session, tenant_a)
    db_session.add(
        OptOut(tenant_id=tenant_b, phone_e164="+905554440000", source=OptOutSource.MANUAL)
    )
    with pytest.raises(Exception):  # noqa: B017 - asyncpg raises a generic DB error
        await db_session.commit()
    await db_session.rollback()
