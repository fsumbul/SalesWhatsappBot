"""Outreach schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    ConversationStatus,
    MessageDirection,
    MessageType,
    OutreachJobStatus,
    SenderHealthStatus,
    SenderTier,
    TemplateCategory,
    TemplateStatus,
)


# --- Templates ---


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    language: str = Field(min_length=2, max_length=10)
    category: TemplateCategory = TemplateCategory.MARKETING
    body: str = Field(min_length=5)
    variables: list[str] = Field(default_factory=list)
    sector_id: UUID | None = None


class TemplatePatchIn(BaseModel):
    body: str | None = None
    variables: list[str] | None = None
    status: TemplateStatus | None = None
    wa_template_id: str | None = None


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    name: str
    language: str
    category: TemplateCategory
    status: TemplateStatus
    body: str
    variables: list[str]
    wa_template_id: str | None
    sector_id: UUID | None
    created_at: datetime


# --- Senders ---


class SenderIn(BaseModel):
    display_name: str
    phone_number_id: str
    business_account_id: str | None = None
    tier: SenderTier = SenderTier.T1
    daily_cap: int = Field(default=1000, ge=1, le=1_000_000)


class SenderPatchIn(BaseModel):
    display_name: str | None = None
    tier: SenderTier | None = None
    daily_cap: int | None = Field(default=None, ge=1, le=1_000_000)
    is_active: bool | None = None
    health_status: SenderHealthStatus | None = None


class SenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    display_name: str
    phone_number_id: str
    tier: SenderTier
    quality_rating: str | None
    health_status: SenderHealthStatus
    daily_sent: int
    daily_cap: int
    is_active: bool


# --- Outreach jobs ---


class OutreachEnqueueIn(BaseModel):
    campaign_id: UUID | None = None
    lead_ids: list[UUID] = Field(min_length=1)
    template_id: UUID
    sender_id: UUID | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    scheduled_for: datetime | None = None


class OutreachJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    campaign_id: UUID | None
    lead_id: UUID
    contact_id: UUID
    template_id: UUID
    sender_id: UUID | None
    status: OutreachJobStatus
    scheduled_for: datetime | None
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None
    error: str | None
    attempts: int


# --- Conversations & messages ---


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    lead_id: UUID
    contact_id: UUID
    assigned_to: UUID | None
    status: ConversationStatus
    last_message_at: datetime | None
    unread_count: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    direction: MessageDirection
    message_type: MessageType
    body: str | None
    media_url: str | None
    created_at: datetime


class MessageSendIn(BaseModel):
    body: str = Field(min_length=1, max_length=4096)
