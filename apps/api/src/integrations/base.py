"""Base types for external integrations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


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
    raw: dict[str, Any] = field(default_factory=dict)


class LeadConnector(Protocol):
    """Abstract connector protocol."""

    name: str

    # Not `async def`: real implementations are async generators (contain
    # `yield`), which are called synchronously to produce the iterator —
    # `async def ... -> AsyncIterator[...]` would instead describe a
    # coroutine that must be awaited *before* it can be iterated.
    def search(self, query: str, country: str, language: str) -> AsyncIterator[RawLead]: ...
