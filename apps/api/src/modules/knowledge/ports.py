"""Ports between the trusted runtime and any retrieval backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


class RetrievalUnavailableError(RuntimeError):
    """The backend cannot serve this request; callers fall back to lexical."""


@dataclass(frozen=True)
class RetrievalRequest:
    """One tenant- and version-bound candidate search.

    Tenant and version are resolved server-side by the caller (webhook/DB);
    nothing here may come from model output.
    """

    tenant_id: UUID
    agent_version_id: UUID
    query: str
    # Trusted subjects: explicit product names and prior runtime fact anchors.
    anchor_subject_ids: tuple[str, ...] = ()
    # Weaker hints from the customer's own memory graph.
    memory_subject_ids: tuple[str, ...] = ()
    # Optional caller scope (for example a request's subject lineage).
    allowed_fact_ids: frozenset[str] | None = None
    max_candidates: int = 12


@dataclass(frozen=True)
class FactCandidate:
    fact_id: str
    score: float
    channels: tuple[str, ...]
    exact_code: bool = False


@dataclass(frozen=True)
class RetrievalResult:
    candidates: list[FactCandidate]
    backend: str
    timings_ms: dict[str, float] = field(default_factory=dict)
    query_codes: tuple[str, ...] = ()
    degraded: bool = False
    reranked: bool = False

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(candidate.fact_id for candidate in self.candidates)

    def audit(self) -> dict[str, object]:
        """Compact, customer-text-free trace for ``AgentRuntimeJob.audit``."""

        return {
            "backend": self.backend,
            "candidates": [
                {
                    "id": c.fact_id,
                    "score": round(c.score, 4),
                    "channels": list(c.channels),
                    "exact": c.exact_code,
                }
                for c in self.candidates
            ],
            "codes": list(self.query_codes),
            "timings_ms": {k: round(v, 1) for k, v in self.timings_ms.items()},
            "degraded": self.degraded,
            "reranked": self.reranked,
        }


class FactRetriever(Protocol):
    """Backend port (FalkorDB today, anything tomorrow)."""

    @property
    def backend(self) -> str: ...

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class KnowledgeRetriever(Protocol):
    """Runtime-facing port: already bound to one tenant and LIVE version.

    ``CompanyAgentRuntime`` only sees this shape, so it never learns tenant
    ids, graph names or backend details.
    """

    @property
    def backend(self) -> str: ...

    async def retrieve(
        self,
        query: str,
        *,
        anchor_subject_ids: tuple[str, ...] = (),
        memory_subject_ids: tuple[str, ...] = (),
        allowed_fact_ids: frozenset[str] | None = None,
        max_candidates: int = 12,
    ) -> RetrievalResult: ...


@dataclass(frozen=True)
class EvidencePassage:
    """A retrieved document/website passage offered as grounded-answer evidence."""

    id: str
    text: str
    locator: str
    subject_ids: tuple[str, ...] = ()
    score: float = 0.0


class EvidenceSearch(Protocol):
    """Runtime-facing passage search bound to one tenant (ADR-003 hybrid mode)."""

    @property
    def backend(self) -> str: ...

    async def retrieve(
        self,
        query: str,
        *,
        subject_ids: tuple[str, ...] = (),
        k: int = 4,
    ) -> list[EvidencePassage]: ...
