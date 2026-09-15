"""API schemas for tenant knowledge sources, candidates and media."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SourceCreateIn(BaseModel):
    agent_id: UUID
    url: str = Field(min_length=8, max_length=1000)
    display_name: str | None = Field(default=None, max_length=180)
    sync_policy: Literal["manual", "daily", "weekly"] = "manual"
    auto_publish: bool = True
    max_pages: int | None = Field(default=None, ge=1, le=500)
    sync_now: bool = True


class SourceUpdateIn(BaseModel):
    enabled: bool | None = None
    sync_policy: Literal["manual", "daily", "weekly"] | None = None
    auto_publish: bool | None = None
    display_name: str | None = Field(default=None, max_length=180)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    kind: str
    display_name: str
    canonical_uri: str | None
    enabled: bool
    sync_policy: str
    auto_publish: bool
    status: str
    last_sync_at: datetime | None
    last_error: str | None
    stats: dict[str, Any]
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    filename: str
    mime_type: str
    sha256: str
    size_bytes: int
    status: str
    page_count: int | None
    error: str | None
    created_at: datetime


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    kind: str
    subject_id: str | None
    category: str | None
    payload: dict[str, Any]
    evidence: dict[str, Any]
    confidence: float
    review_status: str
    protected: bool
    published_ref: str | None
    published_version_id: UUID | None
    error: str | None
    created_at: datetime


class CandidateDecisionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=240)
    customer_text: str | None = Field(default=None, min_length=8, max_length=600)
    subject_id: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=32)
    customer_visible: bool | None = None


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    origin_url: str
    sha256: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    subject_id: str | None
    alt_text: str | None
    score: float
    status: str
    asset_id: str | None
    public_url: str | None = None
    created_at: datetime


class MediaDecisionIn(BaseModel):
    subject_id: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=240)


class SummaryOut(BaseModel):
    agent_id: UUID
    sources: int
    documents: int
    candidates: dict[str, int]
    media: dict[str, int]
    live_version: int | None
    draft_version: int | None
    auto_publish: bool
    threshold: float
