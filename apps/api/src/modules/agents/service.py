"""Agent service — per-tenant agent identity + versioned configuration.

Versioning rules (roadmap E1: "draft -> testing -> live with rollback"):
  - Editing only ever mutates the current DRAFT version's row.
  - Promotion (draft -> testing -> live) and rollback never rewrite
    history: promoting moves a status flag; rollback clones an old
    version's content into a brand new version and promotes *that*. The
    full version history stays queryable and immutable.
  - At most one LIVE and one DRAFT version per agent, enforced here at
    the application layer (not a DB constraint — see note in models.py's
    migration if this ever needs hardening).
  - Every state-changing action writes an AuditLog row, reusing the
    existing compliance.models.AuditLog table rather than building a
    parallel audit mechanism.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.errors import ConflictError, NotFoundError
from src.modules.compliance.models import AuditLog

from .company_config import CompanyAgentConfig, empty_company_agent_config
from .models import Agent, AgentVersion, AgentVersionStatus
from .schemas import AgentIn, AgentVersionPatchIn


class AgentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_agent(self, tenant_id: UUID, agent_id: UUID) -> None:
        result = await self.session.execute(select(Agent).where(
            Agent.tenant_id == tenant_id, Agent.id == agent_id).with_for_update())
        if result.scalar_one_or_none() is None:
            raise NotFoundError("Agent")

    # --- Agents ---

    async def list_agents(self, tenant_id: UUID) -> list[Agent]:
        stmt = (
            select(Agent).where(Agent.tenant_id == tenant_id).order_by(Agent.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_agent(self, tenant_id: UUID, agent_id: UUID) -> Agent:
        stmt = select(Agent).where(Agent.tenant_id == tenant_id, Agent.id == agent_id)
        agent = (await self.session.execute(stmt)).scalar_one_or_none()
        if agent is None:
            raise NotFoundError("Agent", str(agent_id))
        return agent

    async def create_agent(
        self, tenant_id: UUID, data: AgentIn, *, actor_id: UUID | None = None
    ) -> Agent:
        if data.sector_id is not None:
            from src.modules.sectors.models import Sector
            sector = await self.session.scalar(select(Sector).where(
                Sector.id == data.sector_id, Sector.tenant_id == tenant_id))
            if sector is None:
                raise NotFoundError("Sector")
        agent = Agent(
            tenant_id=tenant_id,
            sector_id=data.sector_id,
            name=data.name,
            slug=data.slug,
        )
        self.session.add(agent)
        try:
            await self.session.flush()
        except IntegrityError as e:
            await self.session.rollback()
            raise ConflictError(f"An agent with slug '{data.slug}' already exists") from e

        # Every agent starts life with an empty draft — an agent with zero
        # versions is not a state the rest of the API needs to handle.
        draft = AgentVersion(
            tenant_id=tenant_id,
            agent_id=agent.id,
            version=1,
            status=AgentVersionStatus.DRAFT,
            created_by=actor_id,
        )
        self.session.add(draft)
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="create_agent",
                entity="agent",
                entity_id=str(agent.id),
                meta={"name": data.name, "slug": data.slug},
            )
        )
        await self.session.commit()
        return agent

    # --- Versions ---

    async def list_versions(self, tenant_id: UUID, agent_id: UUID) -> list[AgentVersion]:
        await self.get_agent(tenant_id, agent_id)  # 404s if agent doesn't exist / wrong tenant
        stmt = (
            select(AgentVersion)
            .where(AgentVersion.tenant_id == tenant_id, AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.version.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_version(
        self, tenant_id: UUID, agent_id: UUID, version_id: UUID
    ) -> AgentVersion:
        stmt = select(AgentVersion).where(
            AgentVersion.tenant_id == tenant_id,
            AgentVersion.agent_id == agent_id,
            AgentVersion.id == version_id,
        )
        version = (await self.session.execute(stmt)).scalar_one_or_none()
        if version is None:
            raise NotFoundError("AgentVersion", str(version_id))
        return version

    async def _get_by_status(
        self, tenant_id: UUID, agent_id: UUID, status: AgentVersionStatus
    ) -> AgentVersion | None:
        stmt = select(AgentVersion).where(
            AgentVersion.tenant_id == tenant_id,
            AgentVersion.agent_id == agent_id,
            AgentVersion.status == status,
        )
        return (await self.session.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()

    async def get_draft(self, tenant_id: UUID, agent_id: UUID) -> AgentVersion | None:
        return await self._get_by_status(tenant_id, agent_id, AgentVersionStatus.DRAFT)

    async def get_live(self, tenant_id: UUID, agent_id: UUID) -> AgentVersion | None:
        return await self._get_by_status(tenant_id, agent_id, AgentVersionStatus.LIVE)

    @staticmethod
    def _require_publishable_company_config(version: AgentVersion) -> None:
        """Prevent a partial builder draft from becoming a customer-facing bot."""

        try:
            config = CompanyAgentConfig.model_validate({**version.company_config, "lifecycle": "approved"})
        except ValidationError as exc:
            raise ConflictError("Company configuration is invalid and cannot be published") from exc

        errors = config.publishability_errors()
        if errors:
            raise ConflictError("Company configuration cannot be published: " + "; ".join(errors))

    async def create_draft(
        self, tenant_id: UUID, agent_id: UUID, *, actor_id: UUID | None = None
    ) -> AgentVersion:
        """Start a new draft by cloning the current live version's content
        (or the latest version of any status, if nothing is live yet)."""
        await self.lock_agent(tenant_id, agent_id)
        if await self.get_draft(tenant_id, agent_id) is not None:
            raise ConflictError("A draft already exists for this agent")

        base = await self.get_live(tenant_id, agent_id)
        if base is None:
            versions = await self.list_versions(tenant_id, agent_id)
            base = versions[0] if versions else None

        versions = await self.list_versions(tenant_id, agent_id)
        next_version = versions[0].version + 1 if versions else 1
        draft = AgentVersion(
            tenant_id=tenant_id,
            agent_id=agent_id,
            version=next_version,
            status=AgentVersionStatus.DRAFT,
            persona=base.persona if base else "",
            tone=base.tone if base else "",
            languages=list(base.languages) if base else [],
            product_knowledge=base.product_knowledge if base else "",
            qualification_questions=list(base.qualification_questions) if base else [],
            guardrails=dict(base.guardrails) if base else {},
            reply_policies=dict(base.reply_policies) if base else {},
            company_config=deepcopy(base.company_config) if base else empty_company_agent_config(),
            created_by=actor_id,
        )
        self.session.add(draft)
        await self.session.flush()
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="create_draft",
                entity="agent_version",
                entity_id=str(draft.id),
                meta={"agent_id": str(agent_id), "version": next_version, "cloned_from": base.version if base else None},
            )
        )
        await self.session.commit()
        return draft

    async def update_draft(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        data: AgentVersionPatchIn,
        *,
        actor_id: UUID | None = None,
    ) -> AgentVersion:
        await self.lock_agent(tenant_id, agent_id)
        draft = await self.get_draft(tenant_id, agent_id)
        if draft is None:
            raise ConflictError("No draft to edit — create one first")

        # mode="json" turns nested Pydantic models (notably
        # CompanyAgentConfig) into JSONB-safe dictionaries before assignment.
        if data.expected_revision is not None and data.expected_revision != draft.revision:
            raise ConflictError("Draft changed; reload before saving")
        changes = data.model_dump(exclude_none=True, mode="json", exclude={"expected_revision"})
        draft.revision += 1
        for field, value in changes.items():
            setattr(draft, field, value)

        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="update_draft",
                entity="agent_version",
                entity_id=str(draft.id),
                meta={"agent_id": str(agent_id), "fields_changed": list(changes.keys())},
            )
        )
        await self.session.commit()
        # `updated_at` has `onupdate=func.now()` — a server-computed value
        # SQLAlchemy expires on this row after an UPDATE flush regardless of
        # `expire_on_commit`. Reload it explicitly (async-safe) so returning
        # `draft` straight into `AgentVersionOut.model_validate()` doesn't
        # trigger a synchronous lazy-load (`MissingGreenlet`) on that column.
        await self.session.refresh(draft)
        return draft

    async def promote_to_testing(
        self, tenant_id: UUID, agent_id: UUID, version_id: UUID, *, actor_id: UUID | None = None
    ) -> AgentVersion:
        await self.lock_agent(tenant_id, agent_id)
        version = await self.get_version(tenant_id, agent_id, version_id)
        if version.status != AgentVersionStatus.DRAFT:
            raise ConflictError(f"Only a draft can be promoted to testing (current: {version.status})")
        version.status = AgentVersionStatus.TESTING
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="promote_to_testing",
                entity="agent_version",
                entity_id=str(version.id),
                meta={"agent_id": str(agent_id), "version": version.version},
            )
        )
        await self.session.commit()
        await self.session.refresh(version)  # see update_draft's comment on why
        return version

    async def promote_to_live(
        self, tenant_id: UUID, agent_id: UUID, version_id: UUID, *, actor_id: UUID | None = None
    ) -> AgentVersion:
        await self.lock_agent(tenant_id, agent_id)
        version = await self.get_version(tenant_id, agent_id, version_id)
        if version.status not in (AgentVersionStatus.DRAFT, AgentVersionStatus.TESTING):
            raise ConflictError(
                f"Only a draft or testing version can be promoted to live (current: {version.status})"
            )

        self._require_publishable_company_config(version)
        version.company_config = {**version.company_config, "lifecycle": "approved"}

        previous_live = await self.get_live(tenant_id, agent_id)
        if previous_live is not None:
            previous_live.status = AgentVersionStatus.ARCHIVED

        version.status = AgentVersionStatus.LIVE
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="promote_to_live",
                entity="agent_version",
                entity_id=str(version.id),
                meta={
                    "agent_id": str(agent_id),
                    "version": version.version,
                    "archived_version": previous_live.version if previous_live else None,
                },
            )
        )
        await self.session.commit()
        await self.session.refresh(version)  # see update_draft's comment on why
        return version

    async def rollback_to(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        target_version_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> AgentVersion:
        """Roll back by cloning `target`'s content into a brand new version
        and promoting that — never rewrites or resurrects the old row, so
        the full history (including the fact that a rollback happened) is
        still there afterward."""
        await self.lock_agent(tenant_id, agent_id)
        target = await self.get_version(tenant_id, agent_id, target_version_id)
        self._require_publishable_company_config(target)
        versions = await self.list_versions(tenant_id, agent_id)
        next_version = versions[0].version + 1 if versions else 1

        previous_live = await self.get_live(tenant_id, agent_id)
        if previous_live is not None:
            previous_live.status = AgentVersionStatus.ARCHIVED

        new_version = AgentVersion(
            tenant_id=tenant_id,
            agent_id=agent_id,
            version=next_version,
            status=AgentVersionStatus.LIVE,
            persona=target.persona,
            tone=target.tone,
            languages=list(target.languages),
            product_knowledge=target.product_knowledge,
            qualification_questions=list(target.qualification_questions),
            guardrails=dict(target.guardrails),
            reply_policies=dict(target.reply_policies),
            company_config=deepcopy(target.company_config),
            created_by=actor_id,
            rolled_back_from_version=target.version,
        )
        self.session.add(new_version)
        await self.session.flush()
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="rollback",
                entity="agent_version",
                entity_id=str(new_version.id),
                meta={
                    "agent_id": str(agent_id),
                    "new_version": next_version,
                    "rolled_back_from_version": target.version,
                    "archived_version": previous_live.version if previous_live else None,
                },
            )
        )
        await self.session.commit()
        return new_version
