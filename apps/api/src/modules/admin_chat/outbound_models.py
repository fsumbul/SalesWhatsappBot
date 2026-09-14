"""Tenant-scoped outbox. Only queued recipients can cross the Meta boundary once."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class OutboundBatch(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "chat_outbound_batches"
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("users.id"))
    session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("admin_chat_sessions.id")
    )
    sender_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("sender_profiles.id"))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    template: Mapped[dict[str, Any]] = mapped_column(JSONB)
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    consent_evidence: Mapped[str | None] = mapped_column(Text)


class OutboundRecipient(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "chat_outbound_recipients"
    __table_args__ = (UniqueConstraint("batch_id", "phone", name="uq_chat_outbound_phone"),)
    batch_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("chat_outbound_batches.id")
    )
    phone: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    wa_message_id: Mapped[str | None] = mapped_column(String(120), index=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
