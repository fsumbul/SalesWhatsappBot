"""Integration tests for AgentService's versioning lifecycle against a real
Postgres — the draft/testing/live/rollback state machine and its audit
trail are exactly the kind of thing worth proving against real DB
transactions, not just trusting the code reads correctly.

Requires a migrated Postgres reachable at `LEADPULSE_TEST_DATABASE_URL`
(see conftest.py). Skips cleanly if that database isn't reachable.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import set_tenant_context
from src.core.errors import ConflictError, NotFoundError
from src.modules.agents.models import AgentVersionStatus
from src.modules.agents.schemas import AgentIn, AgentVersionOut, AgentVersionPatchIn
from src.modules.agents.service import AgentService
from src.modules.compliance.models import AuditLog


async def _db_reachable(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _audit_actions(session: AsyncSession, tenant_id: UUID) -> list[str]:
    stmt = (
        select(AuditLog.action)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.entity.in_(["agent", "agent_version"]))
        .order_by(AuditLog.created_at.asc())
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


def _publishable_company_config() -> dict[str, object]:
    return {
        "schema_version": "company-agent-config/1.0",
        "lifecycle": "approved",
        "organization": {"id": "company", "display_names": {"tr-TR": "Örnek Şirket"}},
        "agent": {
            "purposes": ["sales"],
            "supported_locales": ["tr-TR"],
            "default_locale": "tr-TR",
        },
    }


async def _configure_for_live(svc: AgentService, tenant_id: UUID, agent_id: UUID) -> None:
    await svc.update_draft(
        tenant_id,
        agent_id,
        AgentVersionPatchIn(company_config=_publishable_company_config()),
    )


async def test_create_agent_seeds_a_draft_version(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    actor_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    svc = AgentService(db_session)

    agent = await svc.create_agent(
        tenant_id, AgentIn(name="Sales Bot", slug="sales-bot"), actor_id=actor_id
    )
    assert agent.name == "Sales Bot"

    await set_tenant_context(db_session, tenant_id)
    versions = await svc.list_versions(tenant_id, agent.id)
    assert len(versions) == 1
    assert versions[0].status == AgentVersionStatus.DRAFT
    assert versions[0].version == 1
    assert versions[0].created_by == actor_id

    await set_tenant_context(db_session, tenant_id)
    assert await _audit_actions(db_session, tenant_id) == ["create_agent"]


async def test_update_draft_only_changes_supplied_fields(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    svc = AgentService(db_session)
    agent = await svc.create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    draft = await svc.update_draft(
        tenant_id, agent.id, AgentVersionPatchIn(persona="Friendly and concise", tone="friendly")
    )
    assert draft.persona == "Friendly and concise"
    assert draft.tone == "friendly"
    assert draft.languages == []  # untouched field stays at its default

    await set_tenant_context(db_session, tenant_id)
    draft2 = await svc.update_draft(
        tenant_id, agent.id, AgentVersionPatchIn(languages=["tr", "en"])
    )
    assert draft2.persona == "Friendly and concise"  # still there
    assert draft2.languages == ["tr", "en"]


async def test_company_config_is_versioned_with_the_draft(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    svc = AgentService(db_session)
    agent = await svc.create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    config = _publishable_company_config()
    await set_tenant_context(db_session, tenant_id)
    updated = await svc.update_draft(
        tenant_id, agent.id, AgentVersionPatchIn(company_config=config)
    )
    assert updated.company_config["organization"]["display_names"]["tr-TR"] == "Örnek Şirket"
    assert AgentVersionOut.model_validate(updated).company_config.organization is not None

    await set_tenant_context(db_session, tenant_id)
    await svc.promote_to_live(tenant_id, agent.id, updated.id)
    await set_tenant_context(db_session, tenant_id)
    cloned = await svc.create_draft(tenant_id, agent.id)
    assert cloned.company_config == updated.company_config


async def test_cannot_update_draft_when_none_exists(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    svc = AgentService(db_session)
    agent = await svc.create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    draft = await svc.get_draft(tenant_id, agent.id)
    assert draft is not None
    await set_tenant_context(db_session, tenant_id)
    await _configure_for_live(svc, tenant_id, agent.id)
    await set_tenant_context(db_session, tenant_id)
    await svc.promote_to_testing(tenant_id, agent.id, draft.id)
    await set_tenant_context(db_session, tenant_id)
    await svc.promote_to_live(tenant_id, agent.id, draft.id)

    await set_tenant_context(db_session, tenant_id)
    with pytest.raises(ConflictError):
        await svc.update_draft(tenant_id, agent.id, AgentVersionPatchIn(persona="x"))


async def test_promote_to_live_archives_previous_live(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    svc = AgentService(db_session)
    agent = await svc.create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    v1 = await svc.get_draft(tenant_id, agent.id)
    assert v1 is not None
    await set_tenant_context(db_session, tenant_id)
    await _configure_for_live(svc, tenant_id, agent.id)
    await set_tenant_context(db_session, tenant_id)
    await svc.promote_to_testing(tenant_id, agent.id, v1.id)
    await set_tenant_context(db_session, tenant_id)
    v1_live = await svc.promote_to_live(tenant_id, agent.id, v1.id)
    assert v1_live.status == AgentVersionStatus.LIVE

    await set_tenant_context(db_session, tenant_id)
    v2 = await svc.create_draft(tenant_id, agent.id)
    assert v2.version == 2
    await set_tenant_context(db_session, tenant_id)
    await svc.update_draft(tenant_id, agent.id, AgentVersionPatchIn(persona="v2 persona"))
    await set_tenant_context(db_session, tenant_id)
    v2_live = await svc.promote_to_live(tenant_id, agent.id, v2.id)
    assert v2_live.status == AgentVersionStatus.LIVE

    await set_tenant_context(db_session, tenant_id)
    v1_after = await svc.get_version(tenant_id, agent.id, v1.id)
    assert v1_after.status == AgentVersionStatus.ARCHIVED

    await set_tenant_context(db_session, tenant_id)
    current_live = await svc.get_live(tenant_id, agent.id)
    assert current_live is not None
    assert current_live.id == v2.id


async def test_only_one_draft_at_a_time(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    svc = AgentService(db_session)
    agent = await svc.create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    with pytest.raises(ConflictError):
        await svc.create_draft(tenant_id, agent.id)  # the initial draft already exists


async def test_rollback_clones_content_and_preserves_history(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    svc = AgentService(db_session)
    agent = await svc.create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    v1 = await svc.get_draft(tenant_id, agent.id)
    assert v1 is not None
    await set_tenant_context(db_session, tenant_id)
    await _configure_for_live(svc, tenant_id, agent.id)
    await set_tenant_context(db_session, tenant_id)
    await svc.update_draft(tenant_id, agent.id, AgentVersionPatchIn(persona="Original persona"))
    await set_tenant_context(db_session, tenant_id)
    await svc.promote_to_testing(tenant_id, agent.id, v1.id)
    await set_tenant_context(db_session, tenant_id)
    await svc.promote_to_live(tenant_id, agent.id, v1.id)

    await set_tenant_context(db_session, tenant_id)
    v2 = await svc.create_draft(tenant_id, agent.id)
    await set_tenant_context(db_session, tenant_id)
    await svc.update_draft(tenant_id, agent.id, AgentVersionPatchIn(persona="Bad new persona"))
    await set_tenant_context(db_session, tenant_id)
    await svc.promote_to_testing(tenant_id, agent.id, v2.id)
    await set_tenant_context(db_session, tenant_id)
    await svc.promote_to_live(tenant_id, agent.id, v2.id)

    # Roll back to v1's content.
    await set_tenant_context(db_session, tenant_id)
    v3 = await svc.rollback_to(tenant_id, agent.id, v1.id)

    assert v3.version == 3  # a new version, not a resurrection of v1
    assert v3.status == AgentVersionStatus.LIVE
    assert v3.persona == "Original persona"
    assert v3.rolled_back_from_version == 1

    await set_tenant_context(db_session, tenant_id)
    all_versions = await svc.list_versions(tenant_id, agent.id)
    assert len(all_versions) == 3  # nothing deleted
    statuses = {v.version: v.status for v in all_versions}
    assert statuses[1] == AgentVersionStatus.ARCHIVED
    assert statuses[2] == AgentVersionStatus.ARCHIVED
    assert statuses[3] == AgentVersionStatus.LIVE

    actions = await _audit_actions(db_session, tenant_id)
    assert "rollback" in actions


async def test_get_agent_wrong_tenant_raises_not_found(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_a, tenant_b = uuid4(), uuid4()
    await set_tenant_context(db_session, tenant_a)
    svc = AgentService(db_session)
    agent = await svc.create_agent(tenant_a, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_b)
    with pytest.raises(NotFoundError):
        await svc.get_agent(tenant_b, agent.id)
