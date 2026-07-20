"""Integration tests for ComplianceService.check_contact against a real
Postgres (RLS + JSONB behavior can't be faithfully exercised with a mock).

Requires a migrated Postgres reachable at `LEADPULSE_TEST_DATABASE_URL`
(see conftest.py). Skips cleanly if that database isn't reachable.

Quiet-hours assertions are computed from the actual wall clock rather than
frozen, so the test proves the service agrees with an independently computed
expectation for "right now" instead of depending on time-mocking machinery.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import set_tenant_context
from src.modules.compliance.models import ComplianceResult, OptOutSource
from src.modules.compliance.schemas import OptOutIn
from src.modules.compliance.service import ComplianceService
from src.modules.discovery.models import ConsentStatus, ContactType, Lead, LeadContact, LeadStatus
from src.modules.outreach.models import (
    MessageTemplate,
    OutreachJob,
    OutreachJobStatus,
    TemplateCategory,
    TemplateStatus,
)

_QUIET_START = time(9, 0)
_QUIET_END = time(18, 0)


async def _db_reachable(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _make_lead_with_contact(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    country: str | None = None,
    consent_status: ConsentStatus = ConsentStatus.UNKNOWN,
    phone: str = "+905321234567",
) -> LeadContact:
    await set_tenant_context(session, tenant_id)
    lead = Lead(
        tenant_id=tenant_id,
        company_name="Test Co",
        normalized_name="test co",
        country=country,
        source="test",
        status=LeadStatus.QUALIFIED,
        discovered_at=datetime.now(UTC),
    )
    session.add(lead)
    await session.flush()
    contact = LeadContact(
        tenant_id=tenant_id,
        lead_id=lead.id,
        type=ContactType.PHONE,
        raw_value=phone,
        normalized_value=phone,
        consent_status=consent_status,
    )
    session.add(contact)
    await session.commit()
    # No refresh: the tenant GUC was `SET LOCAL` to the now-finished
    # transaction, so a post-commit SELECT would be correctly blocked by RLS.
    return contact


async def _make_outreach_job(
    session: AsyncSession, tenant_id: UUID, contact: LeadContact, sent_at: datetime
) -> None:
    await set_tenant_context(session, tenant_id)
    template = MessageTemplate(
        tenant_id=tenant_id,
        name="tmpl",
        language="en",
        category=TemplateCategory.UTILITY,
        status=TemplateStatus.APPROVED,
        body="Hello {{name}}",
        variables=["name"],
    )
    session.add(template)
    await session.flush()
    session.add(
        OutreachJob(
            tenant_id=tenant_id,
            lead_id=contact.lead_id,
            contact_id=contact.id,
            template_id=template.id,
            status=OutreachJobStatus.SENT,
            sent_at=sent_at,
        )
    )
    await session.commit()


def _is_quiet_hours_now(country: str) -> bool:
    tz_name = {"TR": "Europe/Istanbul", "DE": "Europe/Berlin"}[country]
    now_local = datetime.now(ZoneInfo(tz_name))
    return not (_QUIET_START <= now_local.time() <= _QUIET_END)


async def test_opt_out_list_blocks(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    contact = await _make_lead_with_contact(db_session, tenant_id, phone="+905550001111")
    await set_tenant_context(db_session, tenant_id)
    svc = ComplianceService(db_session)
    await svc.add_opt_out(tenant_id, OptOutIn(phone_e164="+905550001111", source=OptOutSource.MANUAL))

    await set_tenant_context(db_session, tenant_id)
    decision = await svc.check_contact(tenant_id, contact, country=None)
    assert decision.decision == ComplianceResult.BLOCK
    assert decision.reason == "opt_out_present"


async def test_contact_consent_opt_out_blocks(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    contact = await _make_lead_with_contact(
        db_session, tenant_id, consent_status=ConsentStatus.OPT_OUT
    )
    await set_tenant_context(db_session, tenant_id)
    decision = await ComplianceService(db_session).check_contact(tenant_id, contact, country=None)
    assert decision.decision == ComplianceResult.BLOCK
    assert decision.reason == "contact_opted_out"


async def test_cooldown_blocks_within_30_days(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    contact = await _make_lead_with_contact(db_session, tenant_id)
    sent_at = datetime.now(UTC) - timedelta(days=10)
    await _make_outreach_job(db_session, tenant_id, contact, sent_at)

    await set_tenant_context(db_session, tenant_id)
    decision = await ComplianceService(db_session).check_contact(tenant_id, contact, country=None)
    assert decision.decision == ComplianceResult.BLOCK
    assert decision.reason == "cooldown_active"
    assert decision.next_allowed_at is not None
    assert abs((decision.next_allowed_at - (sent_at + timedelta(days=30))).total_seconds()) < 2


async def test_cooldown_expired_falls_through_to_quiet_hours_or_pass(
    db_session: AsyncSession,
) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    contact = await _make_lead_with_contact(db_session, tenant_id, country="DE")
    sent_at = datetime.now(UTC) - timedelta(days=40)
    await _make_outreach_job(db_session, tenant_id, contact, sent_at)

    await set_tenant_context(db_session, tenant_id)
    decision = await ComplianceService(db_session).check_contact(tenant_id, contact, country="DE")

    expected_defer = _is_quiet_hours_now("DE")
    if expected_defer:
        assert decision.decision == ComplianceResult.DEFER
        assert decision.reason == "quiet_hours"
    else:
        assert decision.decision == ComplianceResult.PASS
        assert decision.reason == "allowed"


async def test_no_blockers_matches_quiet_hours_computation(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    contact = await _make_lead_with_contact(db_session, tenant_id, country="TR")

    await set_tenant_context(db_session, tenant_id)
    decision = await ComplianceService(db_session).check_contact(tenant_id, contact, country="TR")

    if _is_quiet_hours_now("TR"):
        assert decision.decision == ComplianceResult.DEFER
        assert decision.reason == "quiet_hours"
    else:
        # IYS is unconfigured in tests -> UNKNOWN -> never blocks -> PASS.
        assert decision.decision == ComplianceResult.PASS
        assert decision.reason == "allowed"


async def test_add_opt_out_is_idempotent(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    svc = ComplianceService(db_session)
    data = OptOutIn(phone_e164="+905559998888", source=OptOutSource.USER_REPLY)

    first = await svc.add_opt_out(tenant_id, data)
    await set_tenant_context(db_session, tenant_id)
    second = await svc.add_opt_out(tenant_id, data)
    assert first.id == second.id

    await set_tenant_context(db_session, tenant_id)
    all_opt_outs = await svc.list_opt_outs(tenant_id)
    assert len([o for o in all_opt_outs if o.phone_e164 == "+905559998888"]) == 1
