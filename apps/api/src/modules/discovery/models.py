"""Campaign, lead, and lead-source models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
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
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    DISCOVERING = "discovering"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class LeadStatus(StrEnum):
    DISCOVERED = "discovered"
    ENRICHING = "enriching"
    ENRICHED = "enriched"
    DISCARDED = "discarded"
    QUALIFIED = "qualified"
    READY_TO_CONTACT = "ready_to_contact"
    BLOCKED_BY_COMPLIANCE = "blocked_by_compliance"
    CONTACTED = "contacted"
    REPLIED = "replied"
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    QUOTED = "quoted"
    WON = "won"
    LOST = "lost"
    BLACKLISTED = "blacklisted"


class LeadPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ContactType(StrEnum):
    PHONE = "phone"
    EMAIL = "email"


class ConsentStatus(StrEnum):
    UNKNOWN = "unknown"
    OPT_IN = "opt_in"
    OPT_OUT = "opt_out"


class Campaign(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "campaigns"

    sector_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        SAEnum(
            CampaignStatus, name="campaign_status", values_callable=lambda e: [x.value for x in e]
        ),
        default=CampaignStatus.DRAFT,
        nullable=False,
    )
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    daily_quota: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Lead(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_tenant_status", "tenant_id", "status"),
        Index("ix_leads_domain", "domain"),
    )

    sector_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sectors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    campaign_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus, name="lead_status", values_callable=lambda e: [x.value for x in e]),
        default=LeadStatus.DISCOVERED,
        nullable=False,
        index=True,
    )
    fit_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    priority: Mapped[LeadPriority] = mapped_column(
        SAEnum(LeadPriority, name="lead_priority", values_callable=lambda e: [x.value for x in e]),
        default=LeadPriority.LOW,
        nullable=False,
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    contacts: Mapped[list[LeadContact]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    sources: Mapped[list[LeadSource]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    enrichment: Mapped[LeadEnrichment | None] = relationship(
        back_populates="lead", cascade="all, delete-orphan", uselist=False
    )


class LeadContact(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "lead_contacts"
    __table_args__ = (
        UniqueConstraint("lead_id", "type", "normalized_value", name="uq_lead_contact"),
        Index("ix_lead_contacts_e164", "normalized_value"),
        Index(
            "uq_lead_contacts_tenant_phone",
            "tenant_id",
            "normalized_value",
            unique=True,
            postgresql_where=text("type = 'phone'"),
        ),
    )

    lead_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[ContactType] = mapped_column(
        SAEnum(ContactType, name="contact_type", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
    )
    raw_value: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_whatsapp: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    wa_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_status: Mapped[ConsentStatus] = mapped_column(
        SAEnum(
            ConsentStatus, name="consent_status", values_callable=lambda e: [x.value for x in e]
        ),
        default=ConsentStatus.UNKNOWN,
        nullable=False,
    )

    lead: Mapped[Lead] = relationship(back_populates="contacts")


class LeadSource(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "lead_sources"

    lead_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    lead: Mapped[Lead] = relationship(back_populates="sources")


class LeadEnrichment(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "lead_enrichment"

    lead_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    fit_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    employees_est: Mapped[int | None] = mapped_column(Integer, nullable=True)
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    contact_page_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped[Lead] = relationship(back_populates="enrichment")
