"""Agent ORM models — self-service WhatsApp agent builder (Phase E1).

`Agent` is the stable per-tenant identity. `AgentVersion` holds the actual
configuration and is immutable once created: editing a draft mutates that
draft's row, but promoting or rolling back never rewrites history — it
creates a new version and moves status flags around. This mirrors how most
config-versioning systems work (e.g. a rollback is a new deploy of old
content, not time travel) and keeps a full audit trail for free.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey

from .company_config import empty_company_agent_config


class AgentVersionStatus(StrEnum):
    DRAFT = "draft"
    TESTING = "testing"
    LIVE = "live"
    ARCHIVED = "archived"


class BuilderSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class Agent(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_agents_tenant_slug"),)

    sector_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    versions: Mapped[list[AgentVersion]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentVersion(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )

    agent_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AgentVersionStatus] = mapped_column(
        SAEnum(
            AgentVersionStatus, name="agent_version_status", values_callable=lambda e: [x.value for x in e]
        ),
        default=AgentVersionStatus.DRAFT,
        nullable=False,
        index=True,
    )

    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Configuration (roadmap E1: persona/tone, product knowledge,
    # languages, qualification questions, guardrails, reply policies) ---
    persona: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tone: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    languages: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    product_knowledge: Mapped[str] = mapped_column(Text, default="", nullable=False)
    qualification_questions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    # {"forbidden_topics": [...], "escalation_triggers": [...]}
    guardrails: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Freeform: e.g. {"max_response_length": 500, "tone_examples": [...]}
    reply_policies: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # The universal, versioned company blueprint.  The legacy columns above
    # remain for backward compatibility while the runtime is migrated to this
    # canonical structure in a follow-up phase.
    company_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=empty_company_agent_config, nullable=False
    )

    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    # Set when this version was created by rolling back to an earlier one's
    # content — points at the version whose content was cloned, not at
    # whatever was live at the time. Null for normal edits.
    rolled_back_from_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="versions")


class BuilderSession(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    """A tenant's in-progress conversation with the agent-builder bot
    (roadmap E2). Tied to one draft AgentVersion at a time — messages
    accumulate here and get turned into patches applied to that draft via
    AgentBuilderService, not stored as their own AgentVersion history."""

    __tablename__ = "agent_builder_sessions"

    agent_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    draft_version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_versions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[BuilderSessionStatus] = mapped_column(
        SAEnum(
            BuilderSessionStatus, name="builder_session_status", values_callable=lambda e: [x.value for x in e]
        ),
        default=BuilderSessionStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    # [{"role": "user"|"assistant", "content": str}, ...] — full transcript,
    # oldest first. Sent back to the LLM in full on every turn.
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    started_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
