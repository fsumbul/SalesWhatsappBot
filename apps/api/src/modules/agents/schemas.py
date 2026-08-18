"""Agent Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .company_config import CompanyAgentConfig
from .models import AgentVersionStatus, BuilderSessionStatus


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
    # Full replacement, deliberately not a loose partial JSON patch.  The
    # builder/preview protocol will later produce validated proposed patches
    # before assembling this complete configuration.
    company_config: CompanyAgentConfig | None = None


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
    company_config: CompanyAgentConfig
    created_by: UUID | None
    rolled_back_from_version: int | None
    created_at: datetime
    updated_at: datetime


class BuilderMessageOut(BaseModel):
    role: str
    content: str


class BuilderSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    agent_id: UUID
    draft_version_id: UUID
    status: BuilderSessionStatus
    messages: list[BuilderMessageOut]
    created_at: datetime


class BuilderMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class BuilderReplyOut(BaseModel):
    reply: str
    draft_patch: dict[str, Any]
    ready_to_promote: bool
