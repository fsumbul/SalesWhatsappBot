# ruff: noqa: RUF001
"""Deterministic + cross-encoder audit of model-written content blocks.

Every generated sentence must be attributable: evidentiary blocks cite
evidence whose text contains every number, code, unit and material the block
mentions; non-factual blocks may not carry values at all; no block may touch
protected commercial topics, contain injection markers or foreign links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from src.core.config import get_settings
from src.integrations.reranker import Reranker, get_reranker
from src.modules.knowledge.compiler import extract_codes, normalize_text, query_tokens

from .company_runtime import has_protected_intent
from .grounded_types import (
    EVIDENTIARY_BLOCKS,
    ContentBlock,
    ContentBlockType,
    ContentPlan,
    EntailmentVerifier,
    EvidenceItem,
)

_CURRENCY_RE = re.compile(
    r"(\d[\d.,]*\s*(tl|₺|try|usd|eur|€|\$)\b)|\b(lira|euro|dolar)\b", re.IGNORECASE
)
_REGULATED_RE = re.compile(r"\b(ISO|TSE|DIN|EN|TS)[-\s]?\d{2,6}\b|\bISO-?9001\b", re.IGNORECASE)
_INJECTION_RE = re.compile(
    r"(ignore (all |the |previous )?(instructions|rules)|system prompt|talimatlar[ıi] yok say|"
    r"kurallar[ıi] yok say|önceki talimat|\bassistant:|\basistan:)",
    re.IGNORECASE,
)
_LINK_RE = re.compile(r"(https?://\S+|www\.\S+|\b[\w.+-]+@[\w-]+\.[\w.]+\b|\+?\d[\d\s()-]{8,}\d)")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_MATERIAL_CODE_RE = re.compile(
    r"\b(GG|GGG|MC|CK|EC|DK|ED|TS|BP|AISI|ST)[-\s]?\d{1,6}\b", re.IGNORECASE
)
_RANGE_RE = re.compile(r"\d[\d.,]*\s*(mm|cm|m)?\s*(ile|-|–)\s*\d[\d.,]*\s*(mm|cm|m)?\s*aras[ıi]", re.IGNORECASE)


class AuditVerdict(StrEnum):
    OK = "ok"
    STRUCTURE = "structure"
    UNKNOWN_REF = "unknown_ref"
    UNSUPPORTED_VALUE = "unsupported_value"
    PROTECTED_LEXICON = "protected_lexicon"
    INJECTION_MARKER = "injection_marker"
    FOREIGN_LINK = "foreign_link"
    LOW_OVERLAP = "low_overlap"
    LENGTH = "length"


@dataclass(frozen=True)
class BlockAudit:
    index: int
    verdict: AuditVerdict
    overlap: float = 0.0
    entailment: float | None = None
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "verdict": self.verdict.value,
            "overlap": round(self.overlap, 3),
            "entailment": None if self.entailment is None else round(self.entailment, 3),
            "missing": list(self.missing),
        }


def text_anchors(text: str) -> frozenset[str]:
    """Numbers, product/bearing/measure codes and material codes in ``text``."""

    anchors: set[str] = set()
    for number in _NUMBER_RE.findall(text):
        anchors.add(number.replace(",", "."))
    anchors.update(code.lower() for code in extract_codes(text))
    for match in _MATERIAL_CODE_RE.finditer(text):
        anchors.add(re.sub(r"[-\s]", "", match.group(0)).lower())
    return frozenset(anchors)


def content_overlap(text: str, evidence_texts: list[str]) -> float:
    tokens = [t for t in query_tokens(text) if len(t) >= 4]
    if not tokens:
        return 1.0
    evidence_tokens = {t for e in evidence_texts for t in query_tokens(e)}
    prefixes = {t[:5] for t in evidence_tokens if len(t) >= 5}
    matched = sum(1 for t in tokens if t in evidence_tokens or (len(t) >= 5 and t[:5] in prefixes))
    return matched / len(tokens)


def audit_block(
    index: int,
    block: ContentBlock,
    evidence: dict[str, EvidenceItem],
    *,
    max_block_characters: int,
    allowed_links: frozenset[str] = frozenset(),
) -> BlockAudit:
    text = block.text.strip()
    if len(text) > max_block_characters:
        return BlockAudit(index, AuditVerdict.LENGTH)
    if _INJECTION_RE.search(text):
        return BlockAudit(index, AuditVerdict.INJECTION_MARKER)
    if has_protected_intent(text) or _CURRENCY_RE.search(text) or _REGULATED_RE.search(text):
        return BlockAudit(index, AuditVerdict.PROTECTED_LEXICON)
    links = {m.group(0).rstrip(".,;") for m in _LINK_RE.finditer(text)}
    if links - allowed_links:
        return BlockAudit(index, AuditVerdict.FOREIGN_LINK)
    unknown = [ref for ref in block.claim_refs if ref not in evidence]
    if unknown:
        return BlockAudit(index, AuditVerdict.UNKNOWN_REF)
    if block.type in EVIDENTIARY_BLOCKS:
        if not block.claim_refs:
            return BlockAudit(index, AuditVerdict.STRUCTURE)
        evidence_texts = [evidence[ref].text for ref in block.claim_refs]
        evidence_anchors = frozenset().union(*(text_anchors(e) for e in evidence_texts))
        missing = tuple(sorted(a for a in text_anchors(text) if a not in evidence_anchors))
        if missing:
            return BlockAudit(index, AuditVerdict.UNSUPPORTED_VALUE, missing=missing)
        overlap = content_overlap(text, evidence_texts)
        return BlockAudit(index, AuditVerdict.OK, overlap=overlap)
    # Non-factual blocks may not carry values, only language.
    if text_anchors(text):
        return BlockAudit(
            index, AuditVerdict.UNSUPPORTED_VALUE, missing=tuple(sorted(text_anchors(text)))
        )
    return BlockAudit(index, AuditVerdict.OK, overlap=1.0)


class NullEntailment:
    backend = "none"
    available = False

    async def score(self, claim: str, evidence_texts: list[str]) -> float:
        return 0.0


class CrossEncoderEntailment:
    """bge-reranker relevance as an entailment proxy: catches off-topic/foreign prose."""

    available = True

    def __init__(self, reranker: Reranker) -> None:
        self._reranker = reranker
        self.backend = reranker.model_name

    async def score(self, claim: str, evidence_texts: list[str]) -> float:
        if not evidence_texts:
            return 0.0
        scores = await self._reranker.rerank(claim, evidence_texts)
        return max(scores) if scores else 0.0


def build_entailment_verifier() -> EntailmentVerifier:
    settings = get_settings()
    if not settings.reranker_enabled:
        return NullEntailment()
    reranker = get_reranker()
    return CrossEncoderEntailment(reranker) if reranker.available else NullEntailment()


async def audit_content_plan(
    plan: ContentPlan,
    evidence: dict[str, EvidenceItem],
    *,
    verifier: EntailmentVerifier | None,
    entailment_threshold: float,
    max_block_characters: int,
    allowed_links: frozenset[str] = frozenset(),
    low_overlap: float = 0.35,
) -> tuple[list[tuple[int, ContentBlock]], list[BlockAudit]]:
    """Return kept (index, block) pairs and every audit record."""

    kept: list[tuple[int, ContentBlock]] = []
    audits: list[BlockAudit] = []
    for index, block in enumerate(plan.blocks):
        audit = audit_block(
            index,
            block,
            evidence,
            max_block_characters=max_block_characters,
            allowed_links=allowed_links,
        )
        if (
            audit.verdict == AuditVerdict.OK
            and block.type in EVIDENTIARY_BLOCKS
            and audit.overlap < low_overlap
        ):
            if verifier is None or not verifier.available:
                audit = BlockAudit(index, AuditVerdict.LOW_OVERLAP, overlap=audit.overlap)
            else:
                try:
                    entailment = await verifier.score(
                        block.text, [evidence[r].text for r in block.claim_refs]
                    )
                except Exception:
                    entailment = 0.0
                audit = BlockAudit(
                    index,
                    AuditVerdict.OK
                    if entailment >= entailment_threshold
                    else AuditVerdict.LOW_OVERLAP,
                    overlap=audit.overlap,
                    entailment=entailment,
                )
        audits.append(audit)
        if audit.verdict == AuditVerdict.OK:
            kept.append((index, block))
    return kept, audits


def generalizes_list(text: str, evidence_texts: list[str]) -> bool:
    """A range phrase over a list-valued fact is a quantitative generalization."""

    if not _RANGE_RE.search(text):
        return False
    return any(len(_NUMBER_RE.findall(e)) >= 3 for e in evidence_texts)


__all__ = [
    "AuditVerdict",
    "BlockAudit",
    "ContentBlockType",
    "CrossEncoderEntailment",
    "NullEntailment",
    "audit_block",
    "audit_content_plan",
    "build_entailment_verifier",
    "content_overlap",
    "generalizes_list",
    "normalize_text",
    "text_anchors",
]
