"""Pluggable LLM clients for onboarding and customer-agent decisions.

This is scaffolding for Phase E2 (the WhatsApp "agent builder bot", which
needs to turn a tenant's conversational description into a structured
`AgentVersion`) and E3 (the auto-reply runtime). Both fundamentally need
a real LLM. ``OllamaLLMClient`` and ``OpenAICompatibleLLMClient`` are
production adapters;
``NullLLMClient`` keeps an unconfigured deployment fail-closed.

`LLMClient` is shaped after the common "messages + system prompt" chat-
completion pattern (Anthropic's Messages API, OpenAI's Chat Completions,
etc. all look like this), so wiring in a real provider later is a thin
adapter, not a redesign. ``NullLLMClient`` raises rather than silently
fabricating a reply: an agent-builder flow that "succeeds" by returning
empty or made-up content would be worse than one that visibly fails.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from src.core.config import get_settings


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["user", "assistant"]
    content: str


class LLMNotConfiguredError(RuntimeError):
    """Raised by NullLLMClient — no LLM provider is wired in yet."""


class LLMCompletionError(RuntimeError):
    """The configured model endpoint did not return a usable completion.

    Provider response bodies are intentionally not included in this exception:
    they can contain echoed customer messages or implementation details and
    should not leak through an API error response.
    """


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str: ...


class NullLLMClient:
    """Every call raises instead of returning a fake/empty completion."""

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        raise LLMNotConfiguredError(
            "No LLM provider is configured (Settings.llm_provider is empty). "
            "Phase E2/E3 (agent builder bot, auto-reply runtime) need a real "
            "provider wired in here before they can do anything — that's an "
            "operator decision (which provider, API key/budget), not something "
            "this scaffold can default to."
        )


class OllamaLLMClient:
    """Talk to a local Ollama server through its native ``/api/chat`` API.

    Settings historically store an OpenAI-style base ending in ``/v1``;
    ``api_url`` safely removes only that terminal path segment. The native API
    is used because Qwen3's ``think`` control and schema-valued ``format`` are
    explicit there and are already exercised by the local simulator.
    """

    def __init__(self, *, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        ollama_root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        self.api_url = f"{ollama_root.rstrip('/')}/api/chat"
        self.model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        payload_messages: list[dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend({"role": m.role, "content": m.content} for m in messages)

        payload: dict[str, object] = {
            "model": self.model,
            "messages": payload_messages,
            "stream": False,
            # Qwen3 routine customer decisions do not need a reasoning trace;
            # disabling it is both faster and keeps structured content clean.
            "think": False,
            "keep_alive": "10m",
            "options": {
                # Keep classification stable. Customer-visible prose is
                # rendered by the server, so creativity has no value here.
                "temperature": 0,
                "num_predict": max_tokens,
                "num_ctx": 4096,
            },
        }
        if response_schema is not None:
            # If the installed Ollama/model cannot honor the schema, the
            # request fails closed instead of retrying unconstrained.
            payload["format"] = response_schema

        timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    self.api_url,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            if not isinstance(data, dict):
                raise TypeError("completion response must be an object")
            message = data.get("message")
            if not isinstance(message, dict):
                raise TypeError("completion response has no message")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise TypeError("completion message has no content")
            return content
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise LLMCompletionError("The local model did not return a usable completion") from exc


class OpenAICompatibleLLMClient:
    """Talk to any Chat Completions-compatible model endpoint.

    This adapter intentionally uses the small, widely implemented
    ``/chat/completions`` contract rather than an SDK. It can therefore point
    to a hosted API or a self-hosted server on Linux, macOS, Windows, or in a
    container. ``base_url`` may be either the API base (``.../v1``) or the
    complete chat-completions URL.
    """

    def __init__(self, *, base_url: str, model: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_url = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )
        self.model = model
        self.api_key = api_key

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        payload_messages: list[dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend({"role": m.role, "content": m.content} for m in messages)
        payload: dict[str, object] = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "customer_reply",
                    "strict": True,
                    "schema": response_schema,
                },
            }

        headers = {"Content-Type": "application/json"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, headers=headers
            ) as client:
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()
                data = response.json()

            if not isinstance(data, dict):
                raise TypeError("completion response must be an object")
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise TypeError("completion response has no choices")
            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                raise TypeError("completion choice must be an object")
            message = first_choice.get("message")
            if not isinstance(message, dict):
                raise TypeError("completion choice has no message")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise TypeError("completion message has no content")
            return content
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise LLMCompletionError("The configured model did not return a usable completion") from exc


_FIELD_ORDER = [
    "persona",
    "tone",
    "languages",
    "product_knowledge",
    "qualification_questions",
    "guardrails",
    "reply_policies",
]

_QUESTIONS = {
    "persona": (
        "Merhaba! Yeni WhatsApp asistanini birlikte yapilandiralim 🙂 Ilk soru: "
        'bu asistan nasil bir karaktere sahip olsun? (Orn: "Sicak, samimi ve '
        'cozum odakli bir satis temsilcisi")'
    ),
    "tone": 'Super. Genel konusma tonu icin kisa bir etiket yazar misin? (Orn: "samimi", "resmi", "enerjik")',
    "languages": 'Hangi dillerde yanit versin? Virgulle ayirarak yaz (Orn: "tr, en").',
    "product_knowledge": "Asistanin musterilere urun/hizmetiniz hakkinda bilmesi gereken en onemli bilgiler neler?",
    "qualification_questions": (
        "Bir musteri adayiyla konusurken hangi sorulari sorarak onu nitelendirmeli? "
        "Birden fazla soruyu virgulle ayirabilirsin."
    ),
    "guardrails": (
        'Asistanin kesinlikle konusmamasi gereken konular var mi? (Orn: "fiyat '
        'pazarligi, rakip karsilastirmasi") — virgulle ayirarak yaz, yoksa "yok" yazabilirsin.'
    ),
    "reply_policies": (
        'Son olarak, uymasi gereken genel davranis kurallari var mi? (Orn: "Asla '
        'taahhut verme, her zaman satis ekibine yonlendir")'
    ),
}

_DRAFT_STATE_RE = re.compile(r"Current draft state:\n(\{.*\})\n\nRespond", re.DOTALL)


def _is_set(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict):
        return bool(value)
    return True


def _coerce(field: str, answer: str) -> object:
    answer = answer.strip()
    if field == "languages":
        return [p.strip().lower() for p in re.split(r"[,\n]", answer) if p.strip()]
    if field == "qualification_questions":
        return [p.strip() for p in re.split(r"[,;\n]", answer) if p.strip()]
    if field == "guardrails":
        topics = [
            p.strip()
            for p in re.split(r"[,;\n]", answer)
            if p.strip() and p.strip().lower() not in {"yok", "hayir", "hayır", "-"}  # noqa: RUF001
        ]
        return {
            "forbidden_topics": topics,
            "escalation_triggers": ["sikayet", "acil durum", "yasal konular"],
        }
    if field == "reply_policies":
        return {"notes": answer}
    return answer


class MockOnboardingLLMClient:
    """Deterministic, no-network stand-in for a real LLM: walks the tenant
    through the same fixed field order `build_system_prompt` asks a real
    model to gather, one question at a time. Exists so the agent-builder
    conversation (the WhatsApp-style onboarding flow) can be exercised
    end-to-end without an LLM provider or API budget. Opt in explicitly
    with `LLM_PROVIDER=mock` — never the default; a real deployment still
    gets `NullLLMClient`'s loud failure instead of fake configuration.

    Reads "Current draft state" back out of the system prompt (rather than
    tracking its own state) so it stays a pure function of
    (messages, system) like a real LLMClient would be — no hidden session
    beyond what AgentBuilderService already persists.
    """

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        match = _DRAFT_STATE_RE.search(system)
        current = json.loads(match.group(1)) if match else {}

        unset_before = [f for f in _FIELD_ORDER if not _is_set(current.get(f))]

        patch: dict[str, object] = {}
        # The first turn is just the tenant opening the conversation — nothing
        # to apply yet. Every later turn's user message answers whichever
        # field was first unset *before* this turn (the one we just asked).
        if len(messages) > 1 and unset_before:
            target_field = unset_before[0]
            patch[target_field] = _coerce(target_field, messages[-1].content)

        remaining = [f for f in _FIELD_ORDER if f not in patch and not _is_set(current.get(f))]
        if remaining:
            reply = _QUESTIONS[remaining[0]]
            ready = False
        else:
            reply = "Tesekkurler! Asistaninin yapilandirmasi tamamlandi ve teste hazir. 🎉"
            ready = True

        return json.dumps(
            {"reply": reply, "draft_patch": patch or None, "ready_to_promote": ready},
            ensure_ascii=False,
        )


def get_llm_client() -> LLMClient:
    """Factory so callers never construct a client directly — this is the
    one place that needs to change once a real provider is wired in."""
    s = get_settings()
    if s.llm_provider == "mock":
        return MockOnboardingLLMClient()
    if s.llm_provider == "ollama":
        return OllamaLLMClient(base_url=s.llm_base_url, model=s.llm_model)
    if s.llm_provider == "openai_compatible":
        return OpenAICompatibleLLMClient(
            base_url=s.llm_base_url,
            model=s.llm_model,
            api_key=s.llm_api_key,
        )
    if not s.llm_provider:
        return NullLLMClient()
    # Keep unknown provider names fail-closed.
    return NullLLMClient()
