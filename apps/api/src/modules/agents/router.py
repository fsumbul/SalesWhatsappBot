"""Agent HTTP router."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from src.core.deps import ClaimsDep, DBSessionDep
from src.core.rbac import RequireManager

from .schemas import AgentIn, AgentOut, AgentVersionOut, AgentVersionPatchIn
from .service import AgentService

router = APIRouter(prefix="/agents", tags=["agents"])


def _tid(claims: dict[str, Any]) -> UUID:
    return UUID(claims["tid"])


def _uid(claims: dict[str, Any]) -> UUID | None:
    sub = claims.get("sub")
    return UUID(sub) if sub else None


@router.get("", response_model=list[AgentOut])
async def list_agents(db: DBSessionDep, claims: ClaimsDep) -> list[AgentOut]:
    items = await AgentService(db).list_agents(_tid(claims))
    return [AgentOut.model_validate(a) for a in items]


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentIn, db: DBSessionDep, claims: RequireManager) -> AgentOut:
    agent = await AgentService(db).create_agent(_tid(claims), payload, actor_id=_uid(claims))
    return AgentOut.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: UUID, db: DBSessionDep, claims: ClaimsDep) -> AgentOut:
    agent = await AgentService(db).get_agent(_tid(claims), agent_id)
    return AgentOut.model_validate(agent)


@router.get("/{agent_id}/versions", response_model=list[AgentVersionOut])
async def list_versions(
    agent_id: UUID, db: DBSessionDep, claims: ClaimsDep
) -> list[AgentVersionOut]:
    items = await AgentService(db).list_versions(_tid(claims), agent_id)
    return [AgentVersionOut.model_validate(v) for v in items]


@router.get("/{agent_id}/versions/{version_id}", response_model=AgentVersionOut)
async def get_version(
    agent_id: UUID, version_id: UUID, db: DBSessionDep, claims: ClaimsDep
) -> AgentVersionOut:
    version = await AgentService(db).get_version(_tid(claims), agent_id, version_id)
    return AgentVersionOut.model_validate(version)


@router.post(
    "/{agent_id}/versions", response_model=AgentVersionOut, status_code=status.HTTP_201_CREATED
)
async def create_draft(agent_id: UUID, db: DBSessionDep, claims: RequireManager) -> AgentVersionOut:
    """Start a new draft, cloned from the current live version (or the
    latest version, if nothing is live yet)."""
    draft = await AgentService(db).create_draft(_tid(claims), agent_id, actor_id=_uid(claims))
    return AgentVersionOut.model_validate(draft)


@router.patch("/{agent_id}/draft", response_model=AgentVersionOut)
async def update_draft(
    agent_id: UUID,
    payload: AgentVersionPatchIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> AgentVersionOut:
    draft = await AgentService(db).update_draft(
        _tid(claims), agent_id, payload, actor_id=_uid(claims)
    )
    return AgentVersionOut.model_validate(draft)


@router.post(
    "/{agent_id}/versions/{version_id}/promote-to-testing", response_model=AgentVersionOut
)
async def promote_to_testing(
    agent_id: UUID, version_id: UUID, db: DBSessionDep, claims: RequireManager
) -> AgentVersionOut:
    version = await AgentService(db).promote_to_testing(
        _tid(claims), agent_id, version_id, actor_id=_uid(claims)
    )
    return AgentVersionOut.model_validate(version)


@router.post("/{agent_id}/versions/{version_id}/promote-to-live", response_model=AgentVersionOut)
async def promote_to_live(
    agent_id: UUID, version_id: UUID, db: DBSessionDep, claims: RequireManager
) -> AgentVersionOut:
    version = await AgentService(db).promote_to_live(
        _tid(claims), agent_id, version_id, actor_id=_uid(claims)
    )
    return AgentVersionOut.model_validate(version)


@router.post("/{agent_id}/versions/{version_id}/rollback", response_model=AgentVersionOut)
async def rollback(
    agent_id: UUID, version_id: UUID, db: DBSessionDep, claims: RequireManager
) -> AgentVersionOut:
    """Roll back to `version_id`'s content — creates a new live version
    that's a copy of the target, it doesn't resurrect the old row."""
    version = await AgentService(db).rollback_to(
        _tid(claims), agent_id, version_id, actor_id=_uid(claims)
    )
    return AgentVersionOut.model_validate(version)
