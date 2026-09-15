"""Compliance schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import ComplianceResult, OptOutSource


class OptOutIn(BaseModel):
    phone_e164: str = Field(min_length=6, max_length=32)
    source: OptOutSource = OptOutSource.MANUAL
    reason: str | None = None

    @field_validator("phone_e164")
    @classmethod
    def require_e164(cls, value: str) -> str:
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
            raise ValueError("Telefon +ülke koduyla E.164 biçiminde olmalı.")
        return value


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
    checks: list[dict[str, Any]]


class ComplianceReportOut(BaseModel):
    total_opt_outs: int
    by_source: dict[str, int]
    blocked_last_30d: int
