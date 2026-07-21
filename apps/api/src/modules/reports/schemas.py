"""Report schemas."""

from __future__ import annotations

from pydantic import BaseModel


class FunnelReportOut(BaseModel):
    discovered: int
    enriched: int
    qualified: int
    contacted: int
    replied: int
    interested: int
    won: int
    lost: int


class SenderHealthOut(BaseModel):
    sender_id: str
    display_name: str
    tier: str
    health_status: str
    daily_sent: int
    daily_cap: int


class SenderHealthReportOut(BaseModel):
    senders: list[SenderHealthOut]


class SalesPerformanceOut(BaseModel):
    total_leads: int
    contacted: int
    reply_rate: float
    conversion_rate: float


class SourcePrecisionEntry(BaseModel):
    source: str
    qualified_or_later: int
    pending: int
    discarded_currently_visible: int
    qualification_rate_of_settled: float | None


class SourcePrecisionOut(BaseModel):
    sources: list[SourcePrecisionEntry]
    caveat: str
