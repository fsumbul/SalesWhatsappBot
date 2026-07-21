"""Integration test for ReportService.source_precision against a real
Postgres — the grouping/aggregation logic is worth verifying against real
SQL, not just trusting the query reads correctly.

Requires a migrated Postgres reachable at `LEADPULSE_TEST_DATABASE_URL`
(see conftest.py). Skips cleanly if that database isn't reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import set_tenant_context
from src.modules.discovery.models import Lead, LeadStatus
from src.modules.reports.service import ReportService


async def _db_reachable(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _make_lead(
    session: AsyncSession, tenant_id: UUID, *, source: str, status: LeadStatus
) -> None:
    session.add(
        Lead(
            tenant_id=tenant_id,
            company_name=f"Co {uuid4()}",
            normalized_name="co",
            source=source,
            status=status,
            discovered_at=datetime.now(UTC),
        )
    )


async def test_source_precision_groups_by_source_and_settles_status(
    db_session: AsyncSession,
) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)

    # google_places: 2 qualified-or-later, 1 still pending
    await _make_lead(db_session, tenant_id, source="google_places", status=LeadStatus.QUALIFIED)
    await _make_lead(db_session, tenant_id, source="google_places", status=LeadStatus.CONTACTED)
    await _make_lead(db_session, tenant_id, source="google_places", status=LeadStatus.ENRICHING)
    # web_crawl: 1 qualified, 1 discarded (not yet purged) -> settled 50%
    await _make_lead(db_session, tenant_id, source="web_crawl", status=LeadStatus.QUALIFIED)
    await _make_lead(db_session, tenant_id, source="web_crawl", status=LeadStatus.DISCARDED)
    await db_session.commit()

    await set_tenant_context(db_session, tenant_id)
    report = await ReportService(db_session).source_precision(tenant_id)

    by_source = {s["source"]: s for s in report["sources"]}

    gp = by_source["google_places"]
    assert gp["qualified_or_later"] == 2
    assert gp["pending"] == 1
    assert gp["discarded_currently_visible"] == 0
    assert gp["qualification_rate_of_settled"] == 1.0

    wc = by_source["web_crawl"]
    assert wc["qualified_or_later"] == 1
    assert wc["discarded_currently_visible"] == 1
    assert wc["qualification_rate_of_settled"] == 0.5

    assert "caveat" in report
    assert "hard-deleted" in report["caveat"]


async def test_source_with_only_pending_leads_has_no_rate(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    await _make_lead(db_session, tenant_id, source="bing", status=LeadStatus.DISCOVERED)
    await db_session.commit()

    await set_tenant_context(db_session, tenant_id)
    report = await ReportService(db_session).source_precision(tenant_id)

    bing = next(s for s in report["sources"] if s["source"] == "bing")
    assert bing["pending"] == 1
    assert bing["qualification_rate_of_settled"] is None


async def test_no_leads_yields_empty_sources_list(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    report = await ReportService(db_session).source_precision(tenant_id)
    assert report["sources"] == []
