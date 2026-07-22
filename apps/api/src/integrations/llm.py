"""LLM client interface — pluggable, no live provider wired in.

This is scaffolding for Phase E2 (the WhatsApp "agent builder bot", which
needs to turn a tenant's conversational description into a structured
`AgentVersion`) and E3 (the auto-reply runtime). Both fundamentally need
a real LLM. Picking a provider (Anthropic, OpenAI, ...) and paying for
API usage is a decision for the operator — the same category of external
blocker as the Meta Business verification that gates Phase B. Nothing in
this file calls a real API.

`LLMClient` is shaped after the common "messages + system prompt" chat-
completion pattern (Anthropic's Messages API, OpenAI's Chat Completions,
etc. all look like this), so wiring in a real provider later is a thin
adapter, not a redesign. `NullLLMClient` — the only implementation that
exists right now — raises rather than silently fabricating a reply: an
agent-builder flow that "succeeds" by returning empty or made-up content
would be worse than one that visibly fails with a clear reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from src.core.config import get_settings


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


class LLMNotConfiguredError(RuntimeError):
    """Raised by NullLLMClient — no LLM provider is wired in yet."""


class LLMClient(Protocol):
    async def complete(
        self, messages: list[LLMMessage], *, system: str = "", max_tokens: int = 1024
    ) -> str: ...


class NullLLMClient:
    """The only LLMClient implementation today. Every call raises
    LLMNotConfiguredError — see module docstring for why that's the right
    default instead of returning a fake/empty completion."""

    async def complete(
        self, messages: list[LLMMessage], *, system: str = "", max_tokens: int = 1024
    ) -> str:
        raise LLMNotConfiguredError(
            "No LLM provider is configured (Settings.llm_provider is empty). "
            "Phase E2/E3 (agent builder bot, auto-reply runtime) need a real "
            "provider wired in here before they can do anything — that's an "
            "operator decision (which provider, API key/budget), not something "
            "this scaffold can default to."
        )


def get_llm_client() -> LLMClient:
    """Factory so callers never construct a client directly — this is the
    one place that needs to change once a real provider is wired in."""
    s = get_settings()
    if not s.llm_provider:
        return NullLLMClient()
    # No provider implemented yet. When one is: branch on s.llm_provider
    # here (e.g. "anthropic" -> AnthropicLLMClient(api_key=s.llm_api_key)),
    # keeping NullLLMClient as the fallback for an unrecognized value.
    return NullLLMClient()
