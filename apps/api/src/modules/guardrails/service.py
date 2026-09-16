"""Composite guard over the configured classifiers, plus Settings factories."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
from dataclasses import replace
from time import perf_counter

import structlog

from src.core.config import Settings, get_settings
from src.core.runtime_timing import timed_stage

from .policy import (
    GuardPolicy,
    combine,
    content_safety_check,
    jailbreak_check,
    topic_check,
    topic_prompt,
    unavailable_check,
    wants_topic_check,
)
from .ports import (
    ContentSafetyClassifier,
    GuardCheck,
    GuardVerdict,
    InputGuard,
    JailbreakClassifier,
    NullInputGuard,
    TopicClassifier,
    TopicContext,
)

logger = structlog.get_logger(__name__)


class CompositeInputGuard:
    """Runs the enabled classifiers concurrently under one time budget."""

    available = True

    def __init__(
        self,
        policy: GuardPolicy,
        *,
        jailbreak: JailbreakClassifier | None = None,
        content_safety: ContentSafetyClassifier | None = None,
        topic: TopicClassifier | None = None,
    ) -> None:
        self.policy = policy
        self.jailbreak = jailbreak if "jailbreak" in policy.checks else None
        self.content_safety = content_safety if "content_safety" in policy.checks else None
        self.topic = topic if "topic_control" in policy.checks else None

    @timed_stage("guardrail.input")
    async def check_customer_message(
        self,
        text: str,
        *,
        history: Sequence[tuple[str, str]] = (),
        topic: TopicContext | None = None,
    ) -> GuardVerdict:
        runners: list[Coroutine[object, object, GuardCheck]] = []
        if self.jailbreak is not None:
            runners.append(self._run("jailbreak", self.jailbreak.model, self._jailbreak(text)))
        if self.content_safety is not None:
            runners.append(
                self._run("content_safety", self.content_safety.model, self._content(text))
            )
        if self.topic is not None and topic is not None and wants_topic_check(text, self.policy):
            runners.append(
                self._run("topic_control", self.topic.model, self._topic(text, topic, history))
            )
        checks = await asyncio.gather(*runners)
        return combine(checks, self.policy)

    async def check_document_text(self, text: str) -> GuardVerdict:
        runners: list[Coroutine[object, object, GuardCheck]] = []
        if self.jailbreak is not None:
            runners.append(self._run("jailbreak", self.jailbreak.model, self._jailbreak(text)))
        if self.content_safety is not None:
            runners.append(
                self._run("content_safety", self.content_safety.model, self._content(text))
            )
        checks = await asyncio.gather(*runners)
        return combine(checks, self.policy)

    # --- individual checks -------------------------------------------------

    @timed_stage("guardrail.jailbreak")
    async def _jailbreak(self, text: str) -> GuardCheck:
        assert self.jailbreak is not None
        jailbreak, score = await self.jailbreak.classify(text)
        return jailbreak_check(jailbreak, score, self.policy, model=self.jailbreak.model)

    @timed_stage("guardrail.content")
    async def _content(self, text: str) -> GuardCheck:
        assert self.content_safety is not None
        result = await self.content_safety.classify(text)
        return content_safety_check(result, self.policy, model=self.content_safety.model)

    @timed_stage("guardrail.topic")
    async def _topic(
        self, text: str, context: TopicContext, history: Sequence[tuple[str, str]]
    ) -> GuardCheck:
        assert self.topic is not None
        on_topic = await self.topic.classify(
            text, system_prompt=topic_prompt(context), history=history
        )
        return topic_check(on_topic, self.policy, model=self.topic.model)

    async def _run(
        self, name: str, model: str, coro: Coroutine[object, object, GuardCheck]
    ) -> GuardCheck:
        started = perf_counter()
        try:
            check = await asyncio.wait_for(coro, timeout=self.policy.timeout_seconds)
        except TimeoutError:
            latency = (perf_counter() - started) * 1000
            logger.warning("guardrail.check.timeout", check=name)
            return unavailable_check(name, model=model, error="Timeout", latency_ms=latency)
        except Exception as exc:  # classifier outage or malformed answer: never crash the turn
            latency = (perf_counter() - started) * 1000
            logger.warning("guardrail.check.failed", check=name, error=type(exc).__name__)
            return unavailable_check(
                name, model=model, error=type(exc).__name__, latency_ms=latency
            )
        return replace(check, latency_ms=(perf_counter() - started) * 1000)


def build_input_guard(settings: Settings | None = None) -> InputGuard:
    """Settings → guard. Disabled or unconfigured means ``NullInputGuard``."""

    s = settings or get_settings()
    if not s.guardrail_enabled:
        return NullInputGuard()
    from src.integrations.nim import NimHttp
    from src.integrations.nim.guardrails import (
        NimContentSafetyClient,
        NimJailbreakClient,
        NimTopicControlClient,
    )

    policy = GuardPolicy.from_settings(s)
    timeout = s.guardrail_timeout_seconds
    jailbreak = content_safety = topic = None
    if "jailbreak" in policy.checks and s.guardrail_jailbreak_base_url.strip():
        jailbreak = NimJailbreakClient(
            NimHttp(
                s.guardrail_jailbreak_base_url,
                api_key=s.guardrail_jailbreak_api_key or s.nim_api_key,
                timeout_seconds=timeout,
                name="guardrail.jailbreak",
            )
        )
    if "content_safety" in policy.checks and s.guardrail_content_safety_base_url.strip():
        content_safety = NimContentSafetyClient(
            NimHttp(
                s.guardrail_content_safety_base_url,
                api_key=s.guardrail_content_safety_api_key or s.nim_api_key,
                timeout_seconds=timeout,
                name="guardrail.content_safety",
            ),
            model=s.guardrail_content_safety_model,
        )
    if "topic_control" in policy.checks and s.guardrail_topic_control_base_url.strip():
        topic = NimTopicControlClient(
            NimHttp(
                s.guardrail_topic_control_base_url,
                api_key=s.guardrail_topic_control_api_key or s.nim_api_key,
                timeout_seconds=timeout,
                name="guardrail.topic_control",
            ),
            model=s.guardrail_topic_control_model,
        )
    if jailbreak is None and content_safety is None and topic is None:
        logger.warning("guardrail.unconfigured", checks=list(policy.checks))
        return NullInputGuard()
    return CompositeInputGuard(
        policy, jailbreak=jailbreak, content_safety=content_safety, topic=topic
    )


def build_ingest_guard(settings: Settings | None = None) -> InputGuard | None:
    """Guard for document chunks; ``None`` keeps ingestion exactly as before."""

    s = settings or get_settings()
    if not (s.guardrail_enabled and s.guardrail_ingest_enabled):
        return None
    guard = build_input_guard(s)
    return guard if guard.available else None
