"""Private requests, immutable confirmations, files, and idempotent input events."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class SelectionRequest(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "selection_requests"
    __table_args__ = (
        Index(
            "uq_selection_active_conversation",
            "tenant_id",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    confirmed_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_to: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    internal_notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class SelectionFile(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "selection_files"
    request_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("selection_requests.id", ondelete="CASCADE"), index=True
    )
    inbound_message_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    filename: Mapped[str] = mapped_column(String(180))
    mime_type: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    size_bytes: Mapped[int] = mapped_column(Integer)


class SelectionEvent(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "selection_events"
    request_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("selection_requests.id", ondelete="CASCADE"), index=True
    )
    inbound_message_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), unique=True
    )
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    kind: Mapped[str] = mapped_column(String(24), default="answer")


class SelectionFormAction(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "selection_form_actions"
    __table_args__ = (Index("uq_selection_form_operation", "request_id", "operation_id", unique=True),)
    request_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("selection_requests.id", ondelete="CASCADE"))
    operation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
