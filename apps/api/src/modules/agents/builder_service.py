"""AgentBuilderService — orchestrates a BuilderSession's conversation with
the LLM and applies the resulting patches to the agent's draft version.

Composes AgentService rather than duplicating its draft/version logic —
this service owns the conversation loop, AgentService still owns what a
"draft" is and how it's promoted.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.errors import BadGatewayError, ConflictError, NotFoundError, ServiceUnavailableError
from src.integrations.llm import LLMClient, LLMMessage, LLMNotConfiguredError, get_llm_client

from .builder import (
    BuilderLLMResponse,
    BuilderResponseParseError,
    build_system_prompt,
    parse_llm_response,
)
from .models import BuilderSession, BuilderSessionStatus
from .schemas import AgentVersionPatchIn
from .service import AgentService

logger = structlog.get_logger(__name__)


class AgentBuilderService:
    def __init__(self, session: AsyncSession, *, llm_client: LLMClient | None = None) -> None:
        self.session = session
        self.agents = AgentService(session)
        self.llm = llm_client or get_llm_client()

    async def get_builder_session(
        self, tenant_id: UUID, agent_id: UUID, session_id: UUID
    ) -> BuilderSession:
        stmt = select(BuilderSession).where(
            BuilderSession.tenant_id == tenant_id,
            BuilderSession.agent_id == agent_id,
            BuilderSession.id == session_id,
        )
        builder_session = (await self.session.execute(stmt)).scalar_one_or_none()
        if builder_session is None:
            raise NotFoundError("BuilderSession", str(session_id))
        return builder_session

    async def start_session(
        self, tenant_id: UUID, agent_id: UUID, *, actor_id: UUID | None = None
    ) -> BuilderSession:
        """Reuses the agent's current draft if one exists, else creates one
        (same rule AgentService already enforces: at most one draft at a
        time), and opens a fresh conversation against it."""
        draft = await self.agents.get_draft(tenant_id, agent_id)
        if draft is None:
            draft = await self.agents.create_draft(tenant_id, agent_id, actor_id=actor_id)

        builder_session = BuilderSession(
            tenant_id=tenant_id,
            agent_id=agent_id,
            draft_version_id=draft.id,
            status=BuilderSessionStatus.ACTIVE,
            messages=[],
            started_by=actor_id,
        )
        self.session.add(builder_session)
        await self.session.commit()
        return builder_session

    async def send_message(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        session_id: UUID,
        user_text: str,
        *,
        actor_id: UUID | None = None,
    ) -> BuilderLLMResponse:
        builder_session = await self.get_builder_session(tenant_id, agent_id, session_id)
        if builder_session.status != BuilderSessionStatus.ACTIVE:
            raise ConflictError(f"Session is {builder_session.status}, not active")

        agent = await self.agents.get_agent(tenant_id, agent_id)
        draft = await self.agents.get_version(tenant_id, agent_id, builder_session.draft_version_id)

        transcript: list[dict[str, Any]] = [
            *builder_session.messages,
            {"role": "user", "content": user_text},
        ]
        llm_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in transcript]
        system_prompt = build_system_prompt(agent.name, draft)

        try:
            raw = await self.llm.complete(llm_messages, system=system_prompt)
        except LLMNotConfiguredError as e:
            raise ServiceUnavailableError(str(e)) from e

        try:
            parsed = parse_llm_response(raw)
        except BuilderResponseParseError as e:
            logger.warning("builder_llm_response_unparseable", session_id=str(session_id), error=str(e))
            raise BadGatewayError(f"Agent builder got an unusable response, try again: {e}") from e

        transcript.append({"role": "assistant", "content": parsed.reply})
        builder_session.messages = transcript

        if parsed.draft_patch:
            await self.agents.update_draft(
                tenant_id,
                agent_id,
                AgentVersionPatchIn(**parsed.draft_patch),
                actor_id=actor_id,
            )

        await self.session.commit()
        return parsed
