"""Base types for external integrations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class RawLead:
    company_name: str
    source: str  # "google_places" | "serpapi" | "bing" | ...
    source_url: str | None = None
    website: str | None = None
    country: str | None = None
    city: str | None = None
    address: str | None = None
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class LeadConnector(Protocol):
    """Abstract connector protocol."""

    name: str

    async def search(self, query: str, country: str, language: str) -> AsyncIterator[RawLead]: ...
