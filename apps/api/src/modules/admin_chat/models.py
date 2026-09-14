"""A turn and its operation commit atomically; session row locks serialize turns."""

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class AdminChatSession(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "admin_chat_sessions"
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(120), default="Yeni yönetici sohbeti")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class AdminChatTurn(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "admin_chat_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "client_message_id", name="uq_admin_turn_client"),
        UniqueConstraint("session_id", "sequence", name="uq_admin_turn_sequence"),
    )
    session_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("admin_chat_sessions.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    client_message_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    sequence: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    audit: Mapped[dict[str, Any]] = mapped_column(JSONB)
