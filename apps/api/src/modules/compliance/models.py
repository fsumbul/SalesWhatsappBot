"""Compliance ORM models: opt_outs, compliance_checks, audit_logs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class OptOutSource(StrEnum):
    USER_REPLY = "user_reply"
    MANUAL = "manual"
    IYS = "iys"
    GDPR = "gdpr"
    IMPORT = "import"


class ComplianceResult(StrEnum):
    PASS = "pass"  # noqa: S105 - compliance-check outcome, not a secret
    BLOCK = "block"
    DEFER = "defer"


class OptOut(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "opt_outs"
    __table_args__ = (UniqueConstraint("tenant_id", "phone_e164", name="uq_optouts_tenant_phone"),)

    phone_e164: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[OptOutSource] = mapped_column(
        SAEnum(OptOutSource, name="optout_source", values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ComplianceCheck(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "compliance_checks"

    lead_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("lead_contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    check_type: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[ComplianceResult] = mapped_column(
        SAEnum(ComplianceResult, name="compliance_result", values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    next_allowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "audit_logs"

    actor_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
