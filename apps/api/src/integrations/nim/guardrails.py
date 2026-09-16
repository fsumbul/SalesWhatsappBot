"""NemoGuard / Nemotron safety classifiers served as self-hosted NIM containers.

Request shapes (see docs/nvidia-nim-harness-agents-plan-2026-09-16.md §2):

* jailbreak detect — ``POST /v1/classify`` ``{"input": text}`` →
  ``{"jailbreak": bool, "score": float}`` (score in [-1, 1], positive = jailbreak);
* content safety — chat completions with the Aegis taxonomy prompt; the model
  answers with a JSON object (``"User Safety"``, ``"Safety Categories"``);
* topic control — chat completions with a system prompt that ends in the
  documented closing line; the model answers exactly ``on-topic`` / ``off-topic``.

Anything outside those contracts raises ``NimError`` and the gate treats the
check as unavailable (fail-closed in production).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from src.modules.guardrails.policy import content_safety_prompt, parse_categories
from src.modules.guardrails.ports import ContentSafetyResult

from .http import NimError, NimHttp

_MAX_INPUT_CHARS = 6000
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_USER_SAFETY_RE = re.compile(r'"?User Safety"?\s*:\s*"?(safe|unsafe)', re.IGNORECASE)
_UNSAFE_RE = re.compile(r"\bunsafe\b", re.IGNORECASE)
_SAFE_RE = re.compile(r"\bsafe\b", re.IGNORECASE)


def _first_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise NimError("classifier response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise NimError("classifier response has no message content")
    return str(message["content"])


def parse_content_safety(content: str) -> ContentSafetyResult:
    """Strict JSON first, then the documented keys inside prose, then bare labels."""

    text = _FENCE_RE.sub("", content.strip()).strip()
    parsed: object = None
    try:
        parsed = json.loads(text)
    except ValueError:
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except ValueError:
                parsed = None
    if isinstance(parsed, dict):
        user = str(parsed.get("User Safety", "")).strip().lower()
        if user not in {"safe", "unsafe"}:
            raise NimError("content safety JSON lacks a User Safety rating")
        response_raw = parsed.get("Response Safety")
        response = None if response_raw is None else str(response_raw).strip().lower() == "safe"
        return ContentSafetyResult(
            user_safe=user == "safe",
            categories=parse_categories(parsed.get("Safety Categories")),
            response_safe=response,
        )
    match = _USER_SAFETY_RE.search(text)
    if match:
        return ContentSafetyResult(
            user_safe=match.group(1).lower() == "safe", categories=parse_categories(text)
        )
    if _UNSAFE_RE.search(text):
        return ContentSafetyResult(user_safe=False, categories=parse_categories(text))
    if _SAFE_RE.search(text):
        return ContentSafetyResult(user_safe=True)
    raise NimError("content safety answer is not a recognised rating")


class NimJailbreakClient:
    def __init__(self, http: NimHttp, *, model: str = "nemoguard-jailbreak-detect") -> None:
        self.http = http
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def classify(self, text: str) -> tuple[bool, float]:
        data = await self.http.post_json("/v1/classify", {"input": text[:_MAX_INPUT_CHARS]})
        jailbreak = data.get("jailbreak")
        score = data.get("score")
        if (
            not isinstance(jailbreak, bool)
            or isinstance(score, bool)
            or not isinstance(score, int | float)
        ):
            raise NimError("jailbreak response has an unexpected shape")
        return jailbreak, float(score)


class NimContentSafetyClient:
    def __init__(self, http: NimHttp, *, model: str) -> None:
        self.http = http
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def classify(self, text: str) -> ContentSafetyResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "user", "content": content_safety_prompt(text[:_MAX_INPUT_CHARS])}
            ],
            "max_tokens": 120,
            "temperature": 0,
        }
        data = await self.http.post_json("/v1/chat/completions", payload)
        return parse_content_safety(_first_message_content(data))


class NimTopicControlClient:
    def __init__(self, http: NimHttp, *, model: str) -> None:
        self.http = http
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def classify(
        self, text: str, *, system_prompt: str, history: Sequence[tuple[str, str]]
    ) -> bool:
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for role, content in history:
            messages.append(
                {"role": "assistant" if role == "assistant" else "user", "content": content[:600]}
            )
        messages.append({"role": "user", "content": text[:_MAX_INPUT_CHARS]})
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": 8,
            "temperature": 0,
        }
        data = await self.http.post_json("/v1/chat/completions", payload)
        label = _first_message_content(data).strip().strip("\"'.").lower()
        if label == "on-topic":
            return True
        if label == "off-topic":
            return False
        raise NimError("topic control answered with an unexpected label")
