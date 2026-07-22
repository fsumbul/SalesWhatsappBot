"""Agent Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import AgentVersionStatus


class AgentIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    sector_id: UUID | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    sector_id: UUID | None
    name: str
    slug: str
    is_active: bool
    created_at: datetime


class AgentVersionPatchIn(BaseModel):
    """All fields optional — only supplied fields are changed. Only valid
    against a version currently in DRAFT status (see AgentService.update_draft)."""

    persona: str | None = None
    tone: str | None = Field(default=None, max_length=80)
    languages: list[str] | None = None
    product_knowledge: str | None = None
    qualification_questions: list[str] | None = None
    guardrails: dict[str, Any] | None = None
    reply_policies: dict[str, Any] | None = None


class AgentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    agent_id: UUID
    version: int
    status: AgentVersionStatus
    persona: str
    tone: str
    languages: list[str]
    product_knowledge: str
    qualification_questions: list[str]
    guardrails: dict[str, Any]
    reply_policies: dict[str, Any]
    created_by: UUID | None
    rolled_back_from_version: int | None
    created_at: datetime
    updated_at: datetime
