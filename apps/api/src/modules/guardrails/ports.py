"""Ports of the guardrail gate: verdicts, the composite guard and classifier adapters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from src.modules.agents.company_config import CompanyAgentConfig


class GuardDecision(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    BLOCK = "block"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class GuardCheck:
    """One classifier's outcome. Never carries the classified text."""

    name: str
    decision: GuardDecision
    score: float | None = None
    labels: tuple[str, ...] = ()
    latency_ms: float = 0.0
    model: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "decision": self.decision.value,
            "score": None if self.score is None else round(self.score, 4),
            "labels": list(self.labels),
            "latency_ms": round(self.latency_ms, 1),
            "model": self.model,
        }


@dataclass(frozen=True)
class GuardVerdict:
    decision: GuardDecision
    checks: tuple[GuardCheck, ...] = ()
    reason: str | None = None
    fail_mode: str = "closed"

    def audit(self) -> dict[str, object]:
        """Compact, text-free trace for ``AgentRuntimeJob.audit`` / ``KnowledgeChunk.guard``."""

        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "fail_mode": self.fail_mode,
            "latency_ms": round(max((c.latency_ms for c in self.checks), default=0.0), 1),
            "checks": [check.as_dict() for check in self.checks],
        }


ALLOWED = GuardVerdict(GuardDecision.ALLOW)


@dataclass(frozen=True)
class TopicContext:
    """What the topic-control classifier is told the assistant is for."""

    company_name: str
    purposes: tuple[str, ...]
    offering_labels: tuple[str, ...]
    locale: str = "tr"

    @classmethod
    def from_config(cls, config: CompanyAgentConfig) -> TopicContext:
        assert config.agent is not None
        locale = config.agent.default_locale
        company = ""
        if config.organization is not None:
            names = config.organization.display_names
            company = names.get(locale) or next(iter(names.values()), "")
        labels = tuple(
            (
                offering.display_names.get(locale)
                or next(iter(offering.display_names.values()), offering.id)
            )
            for offering in config.offerings
            if offering.active
        )
        purposes = tuple(str(getattr(p, "value", p)) for p in config.agent.purposes)
        return cls(company_name=company, purposes=purposes, offering_labels=labels, locale=locale)


class InputGuard(Protocol):
    @property
    def available(self) -> bool: ...

    async def check_customer_message(
        self,
        text: str,
        *,
        history: Sequence[tuple[str, str]] = (),
        topic: TopicContext | None = None,
    ) -> GuardVerdict: ...

    async def check_document_text(self, text: str) -> GuardVerdict: ...


class NullInputGuard:
    """Feature disabled: every input is allowed, exactly today's behaviour."""

    available = False

    async def check_customer_message(
        self,
        text: str,
        *,
        history: Sequence[tuple[str, str]] = (),
        topic: TopicContext | None = None,
    ) -> GuardVerdict:
        return ALLOWED

    async def check_document_text(self, text: str) -> GuardVerdict:
        return ALLOWED


# --- classifier adapters (implemented in src/integrations/nim/guardrails.py) ---


@dataclass(frozen=True)
class ContentSafetyResult:
    user_safe: bool
    categories: tuple[str, ...] = ()
    response_safe: bool | None = None


class JailbreakClassifier(Protocol):
    @property
    def model(self) -> str: ...

    async def classify(self, text: str) -> tuple[bool, float]: ...


class ContentSafetyClassifier(Protocol):
    @property
    def model(self) -> str: ...

    async def classify(self, text: str) -> ContentSafetyResult: ...


class TopicClassifier(Protocol):
    @property
    def model(self) -> str: ...

    async def classify(
        self, text: str, *, system_prompt: str, history: Sequence[tuple[str, str]]
    ) -> bool: ...
