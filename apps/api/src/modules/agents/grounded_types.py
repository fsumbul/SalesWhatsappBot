"""Shared types for hybrid (grounded) answer generation — no heavy imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class ContentBlockType(StrEnum):
    """Subset of the ontology's ContentPlan blocks the model may realize."""

    DIRECT_ANSWER = "DIRECT_ANSWER"
    EVIDENCE_CLAUSE = "EVIDENCE_CLAUSE"
    CONTEXT_BRIDGE = "CONTEXT_BRIDGE"
    LIMITATION_NOTICE = "LIMITATION_NOTICE"
    NEXT_STEP_OFFER = "NEXT_STEP_OFFER"
    SOCIAL_CONNECTION = "SOCIAL_CONNECTION"
    CLOSING = "CLOSING"


EVIDENTIARY_BLOCKS = frozenset({ContentBlockType.DIRECT_ANSWER, ContentBlockType.EVIDENCE_CLAUSE})
NON_FACTUAL_BLOCKS = frozenset(ContentBlockType) - EVIDENTIARY_BLOCKS


class ContentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ContentBlockType
    request_index: int = Field(ge=0, le=3)
    claim_refs: list[str] = Field(default_factory=list, max_length=4)
    text: str = Field(min_length=1, max_length=400)


class ContentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[ContentBlock] = Field(min_length=1, max_length=6)


@dataclass(frozen=True)
class EvidenceItem:
    ref: str  # "fact:<id>" | "chunk:<id>"
    kind: str  # fact | chunk
    text: str
    subject_id: str
    category: str | None = None
    source: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True)
class GeneratedAnswer:
    request_index: int
    text: str
    verified: bool
    evidence_refs: tuple[str, ...] = ()
    fact_ids: tuple[str, ...] = ()
    blocks: tuple[dict[str, object], ...] = ()
    dropped_blocks: tuple[dict[str, object], ...] = field(default_factory=tuple)
    fallback_reason: str | None = None


class EntailmentVerifier(Protocol):
    """Cheap semantic gate behind the deterministic audit (cross-encoder proxy)."""

    @property
    def backend(self) -> str: ...

    @property
    def available(self) -> bool: ...

    async def score(self, claim: str, evidence_texts: list[str]) -> float: ...
