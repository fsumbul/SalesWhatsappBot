"""Outreach ORM models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class TemplateStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAUSED = "paused"


class TemplateCategory(StrEnum):
    MARKETING = "marketing"
    UTILITY = "utility"
    AUTHENTICATION = "authentication"


class OutreachJobStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    CANCELED = "canceled"


class SenderTier(StrEnum):
    T1 = "T1"  # 1K unique / 24h
    T2 = "T2"  # 10K
    T3 = "T3"  # 100K
    T4 = "T4"  # unlimited


class SenderHealthStatus(StrEnum):
    HEALTHY = "healthy"
    WARNING = "warning"
    FLAGGED = "flagged"
    BANNED = "banned"


class ConversationStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class MessageDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class MessageType(StrEnum):
    TEXT = "text"
    TEMPLATE = "template"
    IMAGE = "image"
    DOCUMENT = "document"
    SYSTEM = "system"


class MessageTemplate(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "message_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "language", name="uq_templates_tenant_name_lang"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    category: Mapped[TemplateCategory] = mapped_column(
        SAEnum(TemplateCategory, name="template_category", values_callable=lambda e: [x.value for x in e]),
        default=TemplateCategory.MARKETING,
        nullable=False,
    )
    status: Mapped[TemplateStatus] = mapped_column(
        SAEnum(TemplateStatus, name="template_status", values_callable=lambda e: [x.value for x in e]),
        default=TemplateStatus.DRAFT,
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    wa_template_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sector_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sectors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class SenderProfile(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "sender_profiles"

    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_number_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    business_account_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tier: Mapped[SenderTier] = mapped_column(
        SAEnum(SenderTier, name="sender_tier", values_callable=lambda e: [x.value for x in e]), default=SenderTier.T1, nullable=False
    )
    quality_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)
    health_status: Mapped[SenderHealthStatus] = mapped_column(
        SAEnum(SenderHealthStatus, name="sender_health_status", values_callable=lambda e: [x.value for x in e]),
        default=SenderHealthStatus.HEALTHY,
        nullable=False,
    )
    daily_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_cap: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class OutreachJob(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "outreach_jobs"
    __table_args__ = (
        Index("ix_outreach_jobs_tenant_status", "tenant_id", "status"),
        Index("ix_outreach_jobs_scheduled_for", "scheduled_for"),
    )

    campaign_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    lead_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("lead_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    template_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("message_templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sender_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sender_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    variables: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[OutreachJobStatus] = mapped_column(
        SAEnum(OutreachJobStatus, name="outreach_job_status", values_callable=lambda e: [x.value for x in e]),
        default=OutreachJobStatus.PENDING,
        nullable=False,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    wa_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Conversation(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contact_id", name="uq_conversations_tenant_contact"),
    )

    lead_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("lead_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assigned_to: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus, name="conversation_status", values_callable=lambda e: [x.value for x in e]),
        default=ConversationStatus.OPEN,
        nullable=False,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[MessageDirection] = mapped_column(
        SAEnum(MessageDirection, name="message_direction", values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    message_type: Mapped[MessageType] = mapped_column(
        SAEnum(MessageType, name="message_type", values_callable=lambda e: [x.value for x in e]), default=MessageType.TEXT, nullable=False
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    wa_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    outreach_job_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("outreach_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
