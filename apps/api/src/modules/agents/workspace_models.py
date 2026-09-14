"""Tenant-scoped, durable editor proposals and isolated preview conversations."""

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class ConfigProposal(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "agent_config_proposals"
    agent_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_versions.id"), nullable=False
    )
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    company_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class AgentTestSession(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "agent_test_sessions"
    agent_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    version_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_versions.id"), nullable=False
    )
    version_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
