# ruff: noqa: RUF001
"""Postgres-backed coverage for the GraphRAG hooks inside the runtime worker.

Mocked boundaries: the local LLM, Meta's HTTP call, the FalkorDB-backed
retriever/memory factories and Celery delivery. Everything else (RLS, the
durable job state machine, the audit trail) is real.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.db import session_scope
from src.integrations.llm import LLMMessage
from src.integrations.whatsapp import WhatsAppClient
from src.modules.agents.models import AgentVersion
from src.modules.agents.runtime_models import AgentRuntimeJob, AgentRuntimeJobStatus
from src.modules.guardrails.ports import GuardCheck, GuardDecision, GuardVerdict
from src.modules.knowledge import service as knowledge_service
from src.modules.knowledge.memory import ConversationMemoryStore, CustomerMemory
from src.modules.knowledge.ports import FactCandidate, RetrievalResult
from src.modules.outreach.models import Conversation, ConversationStatus, Message
from src.modules.outreach.webhooks import _handle_messages
from src.workers import agent_runtime as runtime_worker
from src.workers import knowledge as knowledge_worker
from tests.test_whatsapp_runtime_integration import (
    _seed_runtime_tenant,
    runtime_database,  # noqa: F401 - pytest fixture
)

_INBOUND_TEXT = "Size nasıl ulaşabilirim?"


async def _create_inbound_job(tenant_id: UUID, wa_message_id: str) -> tuple[UUID, UUID]:
    """Like the shared helper, but with a message that has no protected intent."""

    async with session_scope(tenant_id) as session:
        job_ids = await _handle_messages(
            session,
            tenant_id,
            [{"from": "905321112233", "id": wa_message_id, "type": "text", "text": {"body": _INBOUND_TEXT}}],
        )
        assert len(job_ids) == 1
        job = await session.get(AgentRuntimeJob, job_ids[0])
        assert job is not None
        conversation_id = job.conversation_id
        await session.commit()
    return job_ids[0], conversation_id


def _company_config() -> dict[str, object]:
    return {
        "schema_version": "company-agent-config/1.0",
        "lifecycle": "approved",
        "organization": {"id": "company", "display_names": {"tr-TR": "Artı Kasnak Test"}},
        "offerings": [
            {"id": "cast_pulley", "kind": "physical_product", "display_names": {"tr-TR": "Döküm kasnak"}}
        ],
        "customer_profiles": [
            {"id": "quote", "applies_to": ["organization"], "fields": [{"id": "diameter_mm", "type": "number"}]}
        ],
        "parties": [
            {"id": "buyer", "kind": "organization", "roles": ["customer"], "display_names": {"tr-TR": "Müşteri"}, "profile_ids": ["quote"]}
        ],
        "facts": [
            {
                "id": "support-contact",
                "subject_id": "company",
                "category": "support",
                "value": "phone",
                "source": "website",
                "customer_visible": True,
                "customer_text": {"tr-TR": "Satış ekibimize 0212 000 00 00 üzerinden ulaşabilirsiniz."},
                "search_terms": ["fiyat", "teklif", "iletişim"],
            }
        ],
        "agent": {
            "purposes": ["sales"],
            "supported_locales": ["tr-TR"],
            "default_locale": "tr-TR",
            "unknown_fact_action": "handoff",
            "handoff_fact_id": "support-contact",
        },
    }


class _SelectingLLM:
    def __init__(self) -> None:
        self.systems: list[str] = []

    async def complete(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        self.systems.append(kwargs.get("system", ""))
        return json.dumps({"action": "reply", "fact_ids": ["support-contact"]})


class _StubScopedRetriever:
    backend = "falkordb-stub"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def retrieve(self, query: str, **kwargs: Any) -> RetrievalResult:
        self.calls.append({"query": query, **kwargs})
        return RetrievalResult(
            candidates=[FactCandidate("support-contact", 0.9, ("vector", "fulltext"))],
            backend=self.backend,
            timings_ms={"total": 3.0},
        )


class _FakeGraphStore:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    @staticmethod
    def memory_graph_name(tenant_id: UUID) -> str:
        return f"mem_{tenant_id.hex}"

    async def graph_exists(self, name: str) -> bool:
        return True

    async def query(self, graph: str, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        self.queries.append((cypher, params))
        if cypher.startswith("CREATE INDEX"):
            raise RuntimeError("already indexed")
        if "WHERE c.last_job_id = $job" in cypher:
            return []
        if "RETURN c.turns" in cypher:
            return [[2, "information", "neutral", "2026-09-15T00:00:00+00:00"]]
        if "INTERESTED_IN]->(s:Subject) RETURN" in cypher:
            return [["cast_pulley"]]
        return []


async def _install_live_version(tenant_id: UUID) -> None:
    async with session_scope(tenant_id) as session:
        version = (
            await session.execute(select(AgentVersion).where(AgentVersion.tenant_id == tenant_id))
        ).scalar_one()
        version.company_config = _company_config()
        await session.commit()


@pytest.mark.asyncio
async def test_worker_uses_graph_retrieval_and_memory_then_enqueues_enrichment(
    runtime_database: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "falkordb")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    get_settings.cache_clear()
    tenant_id, _owner_id = await _seed_runtime_tenant(with_agent=True)
    await _install_live_version(tenant_id)
    job_id, _conversation_id = await _create_inbound_job(tenant_id, "wamid.knowledge-turn-one")

    llm = _SelectingLLM()
    retriever = _StubScopedRetriever()
    fake_store = _FakeGraphStore()
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: llm)
    monkeypatch.setattr(knowledge_service, "build_scoped_retriever", lambda **kwargs: retriever)
    memory_store = ConversationMemoryStore(fake_store)  # type: ignore[arg-type]
    monkeypatch.setattr(knowledge_service, "build_memory_store", lambda store=None: memory_store)
    # The worker module binds the factory at import time; patch that name too so
    # the enrichment step never touches a real FalkorDB during tests.
    monkeypatch.setattr(knowledge_worker, "build_memory_store", lambda store=None: memory_store)
    monkeypatch.setattr(
        knowledge_worker.enrich_conversation_memory,
        "delay",
        lambda tenant, job: enqueued.append((tenant, job)),
    )

    async def send_success(_client: WhatsAppClient, _to: str, _body: str, _preview_url: bool = False) -> dict[str, Any]:
        return {"messages": [{"id": "wamid.knowledge-bot-one"}]}

    monkeypatch.setattr(WhatsAppClient, "send_text_once", send_success)

    result = await runtime_worker._process_runtime_job(tenant_id, job_id)

    assert result == {"status": AgentRuntimeJobStatus.SENT.value, "wa_message_id": "wamid.knowledge-bot-one"}
    assert retriever.calls and retriever.calls[0]["query"] == _INBOUND_TEXT
    assert retriever.calls[0]["memory_subject_ids"] == ("cast_pulley",)
    assert '"customer_memory"' in llm.systems[-1] and "Döküm kasnak" in llm.systems[-1]
    assert enqueued == [(str(tenant_id), str(job_id))]
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        assert job.status == AgentRuntimeJobStatus.SENT.value
        assert job.audit["retrieval"]["status"] == "ok"
        assert job.audit["retrieval"]["backend"] == "falkordb-stub"
        assert [c["id"] for c in job.audit["retrieval"]["candidates"]] == ["support-contact"]
        assert job.audit["customer_memory"] == {"subjects": ["cast_pulley"], "turns": 2}
        assert "905321112233" not in json.dumps(job.audit)

    # --- background enrichment of the durable turn ---
    class _ExtractingLLM:
        async def complete(self, messages: list[LLMMessage], **kwargs: Any) -> str:
            assert messages[-1].role == "assistant"
            assert kwargs["response_schema"]["properties"]["subject_ids"]["items"]["enum"] == ["cast_pulley"]
            return json.dumps(
                {
                    "subject_ids": ["cast_pulley", "not-approved"],
                    "requirements": [{"field": "diameter_mm", "value": "320"}],
                    "intent": "price",
                    "sentiment": "neutral",
                }
            )

    monkeypatch.setattr(knowledge_worker, "get_llm_client", lambda *_args, **_kwargs: _ExtractingLLM())
    summary = await knowledge_worker._enrich_conversation_memory(tenant_id, job_id)

    assert summary == {
        "status": "recorded",
        "subjects": ["cast_pulley"],
        "requirements": 1,
        "intent": "price",
        "sentiment": "neutral",
    }
    written = " ".join(cypher for cypher, _ in fake_store.queries)
    assert "INTERESTED_IN" in written and "STATED" in written
    params = [p for c, p in fake_store.queries if p and "key" in p]
    assert params and all(len(p["key"]) == 32 and "905321112233" not in p["key"] for p in params)
    subject_write = next(p for c, p in fake_store.queries if p is not None and "subjects" in p)
    assert subject_write["subjects"] == ["cast_pulley"]


@pytest.mark.asyncio
async def test_worker_keeps_lexical_path_when_knowledge_backend_is_off(
    runtime_database: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "lexical")
    get_settings.cache_clear()
    tenant_id, _owner_id = await _seed_runtime_tenant(with_agent=True)
    await _install_live_version(tenant_id)
    job_id, _conversation_id = await _create_inbound_job(tenant_id, "wamid.knowledge-lexical-one")
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: _SelectingLLM())
    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        knowledge_worker.enrich_conversation_memory,
        "delay",
        lambda tenant, job: enqueued.append((tenant, job)),
    )

    async def send_success(_client: WhatsAppClient, _to: str, _body: str, _preview_url: bool = False) -> dict[str, Any]:
        return {"messages": [{"id": "wamid.knowledge-bot-lexical"}]}

    monkeypatch.setattr(WhatsAppClient, "send_text_once", send_success)

    result = await runtime_worker._process_runtime_job(tenant_id, job_id)

    assert result["status"] == AgentRuntimeJobStatus.SENT.value
    assert enqueued == []
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        assert job.audit["retrieval"] is None
        assert job.audit["customer_memory"] is None
    assert isinstance(CustomerMemory(), CustomerMemory)


# --- guardrail gate (NIM plan WP1) ------------------------------------------------


class _RefusingLLM:
    """The model must never be called for a blocked message."""

    async def complete(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        raise AssertionError("LLM was called although the guardrail blocked the message")


class _FakeGuard:
    available = True

    def __init__(self, verdict: GuardVerdict) -> None:
        self.verdict = verdict
        self.calls: list[dict[str, Any]] = []

    async def check_customer_message(self, text: str, **kwargs: Any) -> GuardVerdict:
        self.calls.append({"text": text, **kwargs})
        return self.verdict

    async def check_document_text(self, text: str) -> GuardVerdict:
        return self.verdict


async def _send_ok(_client: WhatsAppClient, _to: str, _body: str, _preview_url: bool = False) -> dict[str, Any]:
    return {"messages": [{"id": "wamid.guardrail-bot"}]}


@pytest.mark.asyncio
async def test_worker_guardrail_block_skips_the_model_and_sends_approved_decline(
    runtime_database: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _owner_id = await _seed_runtime_tenant(with_agent=True)
    await _install_live_version(tenant_id)
    job_id, _conversation_id = await _create_inbound_job(tenant_id, "wamid.guardrail-block")
    guard = _FakeGuard(
        GuardVerdict(
            GuardDecision.BLOCK,
            (GuardCheck("jailbreak", GuardDecision.BLOCK, 0.93, ("jailbreak",), 41.0, "nemoguard-jailbreak-detect"),),
            "jailbreak:jailbreak",
        )
    )
    monkeypatch.setattr(runtime_worker, "build_input_guard", lambda: guard)
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: _RefusingLLM())
    monkeypatch.setattr(WhatsAppClient, "send_text_once", _send_ok)

    result = await runtime_worker._process_runtime_job(tenant_id, job_id)

    assert result == {"status": AgentRuntimeJobStatus.SENT.value, "wa_message_id": "wamid.guardrail-bot"}
    assert guard.calls and guard.calls[0]["text"] == _INBOUND_TEXT
    assert guard.calls[0]["topic"].company_name == "Artı Kasnak Test"
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        assert job.status == AgentRuntimeJobStatus.SENT.value
        assert job.action == "decline" and job.fact_ids == [] and job.used_fallback is True
        assert job.audit["model"] == "guardrail"
        assert job.audit["response_source"] == "guardrail"
        assert job.audit["fallback_reason"] == "guardrail:jailbreak:jailbreak"
        assert job.audit["guardrail"]["decision"] == "block"
        assert job.audit["guardrail"]["checks"][0]["model"] == "nemoguard-jailbreak-detect"
        assert job.audit["planned_reply"] == "Bu konuda bilgi veremiyorum. Başka bir konuda yardımcı olabilirim."
        # The verdict never carries the classified text or the phone number.
        assert _INBOUND_TEXT not in json.dumps(job.audit["guardrail"])
        assert "905321112233" not in json.dumps(job.audit)
        outbound = await session.get(Message, job.outbound_message_id)
        assert outbound is not None and outbound.body == job.audit["planned_reply"]
        # A blocked turn never pauses the conversation for human review: the
        # job ends in SENT (not HANDOFF) and the conversation stays open.
        conversation = await session.get(Conversation, job.conversation_id)
        assert conversation is not None and conversation.status == ConversationStatus.OPEN


@pytest.mark.asyncio
async def test_worker_guardrail_unavailable_in_closed_mode_is_a_safe_handoff(
    runtime_database: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _owner_id = await _seed_runtime_tenant(with_agent=True)
    await _install_live_version(tenant_id)
    job_id, _conversation_id = await _create_inbound_job(tenant_id, "wamid.guardrail-down")
    guard = _FakeGuard(GuardVerdict(GuardDecision.UNAVAILABLE, reason="unavailable:content_safety"))
    monkeypatch.setattr(runtime_worker, "build_input_guard", lambda: guard)
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: _RefusingLLM())
    monkeypatch.setattr(WhatsAppClient, "send_text_once", _send_ok)

    result = await runtime_worker._process_runtime_job(tenant_id, job_id)

    assert result["status"] == AgentRuntimeJobStatus.HANDOFF.value
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        assert job.audit["fallback_reason"] == "guardrail:unavailable"
        assert job.audit["guardrail"]["decision"] == "unavailable"
        assert job.audit["model"] == "guardrail"
        assert "0212 000 00 00" in job.audit["planned_reply"]


@pytest.mark.asyncio
async def test_worker_guardrail_allow_and_flag_reach_the_model(
    runtime_database: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _owner_id = await _seed_runtime_tenant(with_agent=True)
    await _install_live_version(tenant_id)
    job_id, _conversation_id = await _create_inbound_job(tenant_id, "wamid.guardrail-flag")
    guard = _FakeGuard(
        GuardVerdict(
            GuardDecision.FLAG,
            (GuardCheck("topic_control", GuardDecision.FLAG, None, ("off_topic",), 80.0, "topic"),),
            "topic_control:off_topic",
            "open",
        )
    )
    llm = _SelectingLLM()
    monkeypatch.setattr(runtime_worker, "build_input_guard", lambda: guard)
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: llm)
    monkeypatch.setattr(WhatsAppClient, "send_text_once", _send_ok)

    result = await runtime_worker._process_runtime_job(tenant_id, job_id)

    assert result["status"] == AgentRuntimeJobStatus.SENT.value
    assert llm.systems, "a flagged message still reaches the model"
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        assert job.audit["model"] == "qwen3:8b"
        assert job.audit["models"] == {"customer": {"provider": "ollama", "model": "qwen3:8b"}, "generation": None}
        assert job.audit["guardrail"] == guard.verdict.audit()
        assert job.fact_ids == ["support-contact"]
