"""Background knowledge tasks on the isolated agent-runtime Celery app.

* ``index_agent_version`` builds the FalkorDB index for one LIVE version.
* ``enrich_conversation_memory`` extracts structured memory after a sent turn.
* ``sync_knowledge_source`` / ``delete_knowledge_source`` run the self-service
  knowledge pipeline (ADR-003): crawl or extract, chunk, embed, propose, publish.

Both run on the ``knowledge`` queue so a slow model call can never delay a
customer reply on ``agent_runtime``. Start a worker with
``-Q agent_runtime,knowledge`` (or a dedicated ``-Q knowledge`` worker).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from celery.signals import worker_process_init
from sqlalchemy import select

from src.core.agent_celery_app import agent_celery_app
from src.core.db import session_scope
from src.integrations.llm import LLMMessage, get_llm_client
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.models import AgentVersion
from src.modules.agents.runtime_models import AgentRuntimeJob, AgentRuntimeJobStatus
from src.modules.discovery.models import LeadContact
from src.modules.knowledge.memory import extract_memory
from src.modules.knowledge.service import build_indexer, build_memory_store, memory_key
from src.modules.outreach.models import Conversation, Message, MessageDirection, MessageType

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)
_MEMORY_HISTORY_LIMIT = 6


@worker_process_init.connect
def _warm_reranker(**_: Any) -> None:
    """Load the cross-encoder once at worker start, not on the first customer.

    Runs for every worker of this app, including the ``agent_runtime`` reply
    worker where retrieval actually happens.
    """

    from src.core.config import get_settings
    from src.integrations.reranker import get_reranker
    from src.modules.knowledge.service import knowledge_enabled

    settings = get_settings()
    if not knowledge_enabled() or not settings.reranker_enabled:
        return
    try:
        run_async(get_reranker().rerank("ısınma", ["ısınma"]))
        logger.info("knowledge.reranker.warm", model=settings.reranker_model)
    except Exception as exc:  # the retriever simply runs without rerank
        logger.warning("knowledge.reranker.warmup_failed", error=type(exc).__name__)


@agent_celery_app.task(name="src.workers.knowledge.index_agent_version")
def index_agent_version(tenant_id: str, version_id: str, rebuild: bool = False) -> dict[str, Any]:
    return run_async(_index_agent_version(UUID(tenant_id), UUID(version_id), rebuild))


async def _index_agent_version(tenant_id: UUID, version_id: UUID, rebuild: bool) -> dict[str, Any]:
    async with session_scope(tenant_id) as session:
        version = await session.get(AgentVersion, version_id)
        if version is None or version.tenant_id != tenant_id:
            return {"status": "missing"}
        config = CompanyAgentConfig.model_validate(version.company_config)
    report = await build_indexer().index_version(
        tenant_id=tenant_id,
        agent_version_id=version_id,
        config=config,
        rebuild=rebuild,
    )
    logger.info("knowledge.index.built", **report.as_dict())
    return {"status": "indexed", **report.as_dict()}


@agent_celery_app.task(
    name="src.workers.knowledge.sync_knowledge_source",
    time_limit=3600,
    soft_time_limit=3300,
)
def sync_knowledge_source(tenant_id: str, source_id: str) -> dict[str, Any]:
    """Crawl/extract one tenant source, embed chunks, extract candidates, publish."""

    return run_async(_sync_knowledge_source(UUID(tenant_id), UUID(source_id)))


async def _sync_knowledge_source(tenant_id: UUID, source_id: UUID) -> dict[str, Any]:
    from src.modules.guardrails.service import build_ingest_guard
    from src.modules.knowledge.ingest import KnowledgeIngestService
    from src.modules.knowledge.ocr import build_document_ocr
    from src.modules.knowledge.service import build_evidence_graph
    from src.modules.knowledge.vision import build_media_verifier

    async with session_scope(tenant_id) as session:
        service = KnowledgeIngestService(
            session,
            llm=get_llm_client(),
            graph=build_evidence_graph(),
            guard=build_ingest_guard(),
            ocr=build_document_ocr(),
            vision=build_media_verifier(),
        )
        result = await service.sync_source(tenant_id, source_id)
    logger.info("knowledge.source.synced", source_id=str(source_id), **{k: v for k, v in result.items() if k != "publish"})
    return result


@agent_celery_app.task(name="src.workers.knowledge.delete_knowledge_source", time_limit=900)
def delete_knowledge_source(tenant_id: str, source_id: str) -> dict[str, Any]:
    return run_async(_delete_knowledge_source(UUID(tenant_id), UUID(source_id)))


async def _delete_knowledge_source(tenant_id: UUID, source_id: UUID) -> dict[str, Any]:
    from src.modules.knowledge.models import KnowledgeSource
    from src.modules.knowledge.publisher import KnowledgePublisher
    from src.modules.knowledge.service import build_evidence_graph

    async with session_scope(tenant_id) as session:
        source = await session.get(KnowledgeSource, source_id)
        if source is None or source.tenant_id != tenant_id:
            return {"status": "missing"}
        report = await KnowledgePublisher(session).revoke_source(tenant_id, source, reason="source deleted")
        graph = build_evidence_graph()
        if graph is not None:
            await graph.delete_source(tenant_id, source_id)
        await session.delete(source)
        await session.commit()
    return {"status": "deleted", **report.as_dict()}


@agent_celery_app.task(name="src.workers.knowledge.enrich_conversation_memory")
def enrich_conversation_memory(tenant_id: str, job_id: str) -> dict[str, Any]:
    return run_async(_enrich_conversation_memory(UUID(tenant_id), UUID(job_id)))


async def _enrich_conversation_memory(tenant_id: UUID, job_id: UUID) -> dict[str, Any]:
    memory_store = build_memory_store()
    if memory_store is None:
        return {"status": "disabled"}
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        if job is None or job.tenant_id != tenant_id:
            return {"status": "missing"}
        if job.status not in {
            AgentRuntimeJobStatus.SENT.value,
            AgentRuntimeJobStatus.HANDOFF.value,
        }:
            return {"status": "skipped", "reason": f"job status {job.status}"}
        if job.agent_version_id is None:
            return {"status": "skipped", "reason": "job has no agent version"}
        version = await session.get(AgentVersion, job.agent_version_id)
        conversation = await session.get(Conversation, job.conversation_id)
        inbound = await session.get(Message, job.inbound_message_id)
        if version is None or conversation is None or inbound is None or not inbound.body:
            return {"status": "skipped", "reason": "turn is incomplete"}
        contact = await session.get(LeadContact, conversation.contact_id)
        if contact is None:
            return {"status": "skipped", "reason": "contact is missing"}
        config = CompanyAgentConfig.model_validate(version.company_config)
        rows = list(
            (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.message_type == MessageType.TEXT,
                        Message.body.is_not(None),
                        Message.created_at <= inbound.created_at,
                        Message.id != inbound.id,
                    )
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(_MEMORY_HISTORY_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        outbound = (
            await session.get(Message, job.outbound_message_id)
            if job.outbound_message_id is not None
            else None
        )
        key = memory_key(tenant_id, contact.normalized_value)
        # Materialize everything the model needs while the session is open;
        # ORM instances expire once the scope commits.
        turns: list[LLMMessage] = [
            LLMMessage(
                role="user" if item.direction == MessageDirection.INBOUND else "assistant",
                content=item.body or "",
            )
            for item in reversed(rows)
        ]
        turns.append(LLMMessage(role="user", content=inbound.body))
        if outbound is not None and outbound.body:
            turns.append(LLMMessage(role="assistant", content=outbound.body))

    extraction = await extract_memory(get_llm_client(), config, turns)
    recorded = await memory_store.record_turn(
        tenant_id=tenant_id,
        key=key,
        extraction=extraction,
        job_id=str(job_id),
        observed_at=datetime.now(UTC),
    )
    summary = {
        "status": "recorded" if recorded else "duplicate",
        "subjects": extraction.subject_ids,
        "requirements": len(extraction.requirements),
        "intent": extraction.intent,
        "sentiment": extraction.sentiment,
    }
    logger.info("knowledge.memory.turn", job_id=str(job_id), **summary)
    return summary
