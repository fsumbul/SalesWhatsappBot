"""Durable private workflows; actions share the session serialization lock."""

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class Workflow(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "chat_workflows"
    session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("admin_chat_sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    anchor_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    kind: Mapped[str] = mapped_column(String(60))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    step: Mapped[str] = mapped_column(String(60), default="details")
    status: Mapped[str] = mapped_column(String(30), default="awaiting_input")
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class WorkflowAction(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "chat_workflow_actions"
    __table_args__ = (
        UniqueConstraint("session_id", "client_operation_id", name="uq_workflow_client_operation"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("admin_chat_sessions.id", ondelete="CASCADE")
    )
    workflow_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("chat_workflows.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    client_operation_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
