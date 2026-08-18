"""Durable audit/outbox state for customer-facing agent turns."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class AgentRuntimeJobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENDING = "sending"
    SENT = "sent"
    HANDOFF = "handoff"
    RESOLVED = "resolved"
    SKIPPED = "skipped"
    FAILED = "failed"


class AgentRuntimeJob(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    """One idempotent model/send attempt for one inbound WhatsApp message."""

    __tablename__ = "agent_runtime_jobs"
    __table_args__ = (Index("ix_agent_runtime_jobs_tenant_status", "tenant_id", "status"),)

    inbound_message_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_version_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24), default=AgentRuntimeJobStatus.PENDING.value, nullable=False, index=True
    )
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fact_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    used_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outbound_message_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    outbound_wa_message_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audit: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
