"""Integration tests for AgentBuilderService against a real Postgres, using
a stub LLMClient (implementing the same Protocol a real provider would) so
the conversation orchestration is exercised end-to-end without needing a
live LLM. Only the *quality* of a real model's replies is untested here —
the session lifecycle, patch application, and error handling are real.

Requires a migrated Postgres reachable at `LEADPULSE_TEST_DATABASE_URL`
(see conftest.py). Skips cleanly if that database isn't reachable.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import set_tenant_context
from src.core.errors import BadGatewayError, ConflictError, ServiceUnavailableError
from src.integrations.llm import LLMMessage, LLMNotConfiguredError, NullLLMClient
from src.modules.agents.builder_service import AgentBuilderService
from src.modules.agents.models import BuilderSessionStatus
from src.modules.agents.schemas import AgentIn
from src.modules.agents.service import AgentService


async def _db_reachable(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


class _StubLLMClient:
    """A scripted LLMClient: returns each entry in `responses` in order,
    one per call to complete()."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[LLMMessage]] = []

    async def complete(
        self, messages: list[LLMMessage], *, system: str = "", max_tokens: int = 1024
    ) -> str:
        self.calls.append(messages)
        return self._responses.pop(0)


class _AlwaysRaisesLLMClient:
    async def complete(
        self, messages: list[LLMMessage], *, system: str = "", max_tokens: int = 1024
    ) -> str:
        raise LLMNotConfiguredError("stub: not configured")


class _GarbageLLMClient:
    async def complete(
        self, messages: list[LLMMessage], *, system: str = "", max_tokens: int = 1024
    ) -> str:
        return "Sure, I'd be happy to help! (not JSON)"


async def test_start_session_creates_session_against_existing_draft(
    db_session: AsyncSession,
) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    agent = await AgentService(db_session).create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    builder = AgentBuilderService(db_session, llm_client=NullLLMClient())
    builder_session = await builder.start_session(tenant_id, agent.id)

    assert builder_session.status == BuilderSessionStatus.ACTIVE
    assert builder_session.messages == []

    await set_tenant_context(db_session, tenant_id)
    draft = await AgentService(db_session).get_draft(tenant_id, agent.id)
    assert draft is not None
    assert builder_session.draft_version_id == draft.id


async def test_send_message_applies_patch_and_records_transcript(
    db_session: AsyncSession,
) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    agent = await AgentService(db_session).create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    stub = _StubLLMClient(
        [
            '{"reply": "What tone should I use?", '
            '"draft_patch": {"persona": "Warm and helpful"}, "ready_to_promote": false}'
        ]
    )
    builder = AgentBuilderService(db_session, llm_client=stub)
    builder_session = await builder.start_session(tenant_id, agent.id)

    await set_tenant_context(db_session, tenant_id)
    reply = await builder.send_message(
        tenant_id, agent.id, builder_session.id, "It's a WhatsApp sales agent for elevators."
    )

    assert reply.reply == "What tone should I use?"
    assert reply.draft_patch == {"persona": "Warm and helpful"}
    assert reply.ready_to_promote is False

    await set_tenant_context(db_session, tenant_id)
    draft = await AgentService(db_session).get_draft(tenant_id, agent.id)
    assert draft is not None
    assert draft.persona == "Warm and helpful"

    await set_tenant_context(db_session, tenant_id)
    refreshed = await builder.get_builder_session(tenant_id, agent.id, builder_session.id)
    assert len(refreshed.messages) == 2
    assert refreshed.messages[0]["role"] == "user"
    assert refreshed.messages[1]["role"] == "assistant"
    assert refreshed.messages[1]["content"] == "What tone should I use?"


async def test_second_message_includes_full_transcript(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    agent = await AgentService(db_session).create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    stub = _StubLLMClient(
        [
            '{"reply": "What tone?", "draft_patch": null, "ready_to_promote": false}',
            '{"reply": "Great, noted.", "draft_patch": {"tone": "friendly"}, "ready_to_promote": false}',
        ]
    )
    builder = AgentBuilderService(db_session, llm_client=stub)
    builder_session = await builder.start_session(tenant_id, agent.id)

    await set_tenant_context(db_session, tenant_id)
    await builder.send_message(tenant_id, agent.id, builder_session.id, "It's for elevator sales.")
    await set_tenant_context(db_session, tenant_id)
    await builder.send_message(tenant_id, agent.id, builder_session.id, "Friendly, please.")

    # The second LLM call should have seen all 3 prior turns (2 user + 1 assistant).
    assert len(stub.calls) == 2
    assert len(stub.calls[1]) == 3


async def test_message_on_inactive_session_raises_conflict(db_session: AsyncSession) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    agent = await AgentService(db_session).create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    builder = AgentBuilderService(db_session, llm_client=NullLLMClient())
    builder_session = await builder.start_session(tenant_id, agent.id)

    await set_tenant_context(db_session, tenant_id)
    builder_session.status = BuilderSessionStatus.COMPLETED
    await db_session.commit()

    await set_tenant_context(db_session, tenant_id)
    with pytest.raises(ConflictError):
        await builder.send_message(tenant_id, agent.id, builder_session.id, "hello?")


async def test_unconfigured_llm_raises_service_unavailable_not_crash(
    db_session: AsyncSession,
) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    agent = await AgentService(db_session).create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    builder = AgentBuilderService(db_session, llm_client=_AlwaysRaisesLLMClient())
    builder_session = await builder.start_session(tenant_id, agent.id)

    await set_tenant_context(db_session, tenant_id)
    with pytest.raises(ServiceUnavailableError):
        await builder.send_message(tenant_id, agent.id, builder_session.id, "hi")


async def test_malformed_llm_output_raises_bad_gateway_not_crash(
    db_session: AsyncSession,
) -> None:
    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    tenant_id = uuid4()
    await set_tenant_context(db_session, tenant_id)
    agent = await AgentService(db_session).create_agent(tenant_id, AgentIn(name="Bot", slug="bot"))

    await set_tenant_context(db_session, tenant_id)
    builder = AgentBuilderService(db_session, llm_client=_GarbageLLMClient())
    builder_session = await builder.start_session(tenant_id, agent.id)

    await set_tenant_context(db_session, tenant_id)
    with pytest.raises(BadGatewayError):
        await builder.send_message(tenant_id, agent.id, builder_session.id, "hi")
