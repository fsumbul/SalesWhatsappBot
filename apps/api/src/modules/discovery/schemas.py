"""Discovery Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    CampaignStatus,
    ConsentStatus,
    ContactType,
    LeadPriority,
    LeadStatus,
)


class CampaignIn(BaseModel):
    sector_id: UUID
    name: str = Field(min_length=2, max_length=160)
    filters: dict[str, Any] = Field(default_factory=dict)
    daily_quota: int = Field(default=200, ge=1, le=100000)


class DiscoverIn(BaseModel):
    """Optional scan tuning: restrict discovery to specific ISO country codes."""

    countries: list[str] | None = None


class CampaignPatchIn(BaseModel):
    name: str | None = None
    filters: dict[str, Any] | None = None
    daily_quota: int | None = Field(default=None, ge=1, le=100000)
    status: CampaignStatus | None = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    sector_id: UUID
    name: str
    status: CampaignStatus
    filters: dict[str, Any]
    daily_quota: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class LeadContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    type: ContactType
    raw_value: str
    normalized_value: str
    country_code: str | None
    is_valid: bool
    is_whatsapp: bool | None
    consent_status: ConsentStatus


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    sector_id: UUID | None
    campaign_id: UUID | None
    company_name: str
    website: str | None
    domain: str | None
    country: str | None
    city: str | None
    source: str
    source_url: str | None
    status: LeadStatus
    fit_score: int
    priority: LeadPriority
    discovered_at: datetime
    enriched_at: datetime | None
    contacted_at: datetime | None
    contacts: list[LeadContactOut] = Field(default_factory=list)


class LeadDetailOut(LeadOut):
    pass


class LeadFilters(BaseModel):
    campaign_id: UUID | None = None
    sector_id: UUID | None = None
    status: LeadStatus | None = None
    country: str | None = None
    min_fit_score: int | None = None
    has_whatsapp: bool | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class DiscoveryStatusOut(BaseModel):
    campaign_id: UUID
    status: CampaignStatus
    total_leads: int
    discovered: int
    scanned: int = 0
    enriched: int
    qualified: int
