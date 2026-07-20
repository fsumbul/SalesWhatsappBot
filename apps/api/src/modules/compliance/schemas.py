"""Compliance schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import ComplianceResult, OptOutSource


class OptOutIn(BaseModel):
    phone_e164: str = Field(min_length=6, max_length=32)
    source: OptOutSource = OptOutSource.MANUAL
    reason: str | None = None


class OptOutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    phone_e164: str
    source: OptOutSource
    reason: str | None
    created_at: datetime


class ComplianceDecisionOut(BaseModel):
    decision: ComplianceResult
    reason: str
    next_allowed_at: datetime | None = None
    checks: list[dict]


class ComplianceReportOut(BaseModel):
    total_opt_outs: int
    by_source: dict[str, int]
    blocked_last_30d: int
