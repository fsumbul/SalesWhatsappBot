# ruff: noqa: RUF001
"""Durable WhatsApp inbound -> approved company agent -> outbound worker."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, case, or_, select, text

from src.core.agent_celery_app import agent_celery_app as celery_app
from src.core.config import get_settings
from src.core.db import session_scope
from src.integrations.llm import LLMMessage, get_llm_client, role_llm_client
from src.integrations.whatsapp import WhatsAppClient
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import (
    CompanyAgentRuntime,
    CustomerReplyAction,
    RuntimeInteractionKind,
    RuntimeTurn,
    RuntimeWhatsAppCapabilities,
)
from src.modules.agents.grounded_types import EntailmentVerifier
from src.modules.agents.models import Agent, AgentVersion, AgentVersionStatus
from src.modules.agents.runtime_models import AgentRuntimeJob, AgentRuntimeJobStatus
from src.modules.auth.models import Tenant, TenantStatus, User, UserRole
from src.modules.compliance.models import OptOut
from src.modules.discovery.models import ConsentStatus, LeadContact
from src.modules.guardrails.ports import GuardVerdict, TopicContext
from src.modules.guardrails.service import build_input_guard
from src.modules.guardrails.turns import guardrail_blocked_turn
from src.modules.knowledge.memory import CustomerMemory
from src.modules.knowledge.ports import EvidenceSearch, KnowledgeRetriever
from src.modules.outreach.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    MessageType,
)
from src.modules.selection import service as selection_service

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)

_TYPING_INDICATOR_REFRESH_SECONDS = 20.0
_HISTORY_MESSAGE_LIMIT = 8
_HISTORY_CHARACTER_LIMIT = 3000
_HISTORY_MESSAGE_CHARACTER_LIMIT = 1500


def _tenant_whatsapp_capabilities(tenant_id: UUID) -> RuntimeWhatsAppCapabilities | None:
    """Load one tenant's private Flow binding, failing closed if malformed."""

    raw = get_settings().whatsapp_tenant_capabilities_json.strip()
    if not raw:
        return None
    try:
        document = json.loads(raw)
        item = document[str(tenant_id)]
        enabled_values = item.get("enabled", [])
        flow_ids = item.get("flow_ids", {})
        flow_tokens = item.get("flow_tokens", {})
        if (
            not isinstance(item, dict)
            or not isinstance(enabled_values, list)
            or not isinstance(flow_ids, dict)
            or not isinstance(flow_tokens, dict)
            or not all(
                isinstance(key, str) and isinstance(value, str) and value
                for key, value in flow_ids.items()
            )
            or not all(
                isinstance(key, str) and isinstance(value, str) and value
                for key, value in flow_tokens.items()
            )
        ):
            return None
        enabled = frozenset(
            RuntimeInteractionKind(value)
            for value in enabled_values
            if isinstance(value, str) and value in RuntimeInteractionKind._value2member_map_
        )
        return RuntimeWhatsAppCapabilities(
            flow_ids=flow_ids,
            flow_tokens=flow_tokens,
            enabled=enabled,
        )
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _model_audit(settings: Any) -> dict[str, Any]:
    """Provider/model per LLM role used by a customer turn (plan WP5); no URLs, no keys."""

    audit: dict[str, Any] = {}
    for role in ("customer", "generation"):
        try:
            endpoint = settings.llm_endpoint(role)
        except AttributeError:  # settings doubles in tests
            return audit
        if role == "generation" and not endpoint.override:
            audit[role] = None
            continue
        audit[role] = {"provider": endpoint.provider, "model": endpoint.model}
    return audit


def _interaction_audit(turn: RuntimeTurn) -> dict[str, Any] | None:
    interaction = turn.interaction
    if interaction is None:
        return None
    return {
        "kind": interaction.kind.value,
        "button_text": interaction.button_text,
        "url": interaction.url,
        "flow_id_present": interaction.flow_id is not None,
        "flow_token_present": interaction.flow_token is not None,
        "header_media_id": (
            interaction.header_media.id if interaction.header_media is not None else None
        ),
        "options": [
            {
                "id": option.id,
                "title": option.title,
                "description": option.description,
            }
            for option in interaction.options
        ],
        "carousel_cards": [
            {
                "offering_id": card.offering_id,
                "button_text": card.button_text,
                "url": card.url,
                "header_media_id": card.header_media.id,
            }
            for card in interaction.carousel_cards
        ],
    }


async def _send_runtime_turn_once(to: str, turn: RuntimeTurn) -> tuple[dict[str, Any], str]:
    """Cross the at-most-once Meta boundary with the planned message shape."""

    client = WhatsAppClient()
    interaction = turn.interaction
    if interaction is None:
        return await client.send_text_once(to, turn.reply), "text"
    header_media = (
        {
            "kind": interaction.header_media.kind.value,
            "link": interaction.header_media.url,
            "mime_type": interaction.header_media.mime_type,
            "size_bytes": str(interaction.header_media.size_bytes),
        }
        if interaction.header_media is not None
        else None
    )
    if interaction.kind == RuntimeInteractionKind.REPLY_BUTTONS:
        button_kwargs: dict[str, Any] = {}
        if header_media is not None:
            button_kwargs["header_media"] = header_media
        response = await client.send_reply_buttons_once(
            to,
            turn.reply,
            [{"id": option.id, "title": option.title} for option in interaction.options],
            **button_kwargs,
        )
    elif interaction.kind == RuntimeInteractionKind.LIST:
        response = await client.send_list_once(
            to,
            turn.reply,
            button_text=interaction.button_text,
            section_title=interaction.section_title or "Seçenekler",
            rows=[
                {
                    "id": option.id,
                    "title": option.title,
                    **(
                        {"description": option.description}
                        if option.description is not None
                        else {}
                    ),
                }
                for option in interaction.options
            ],
        )
    elif interaction.kind == RuntimeInteractionKind.CAROUSEL:
        response = await client.send_carousel_once(
            to,
            turn.reply,
            cards=[
                {
                    "body_text": card.body_text,
                    "button_text": card.button_text,
                    "url": card.url,
                    "header_media": {
                        "kind": card.header_media.kind.value,
                        "link": card.header_media.url,
                        "mime_type": card.header_media.mime_type,
                        "size_bytes": str(card.header_media.size_bytes),
                    },
                }
                for card in interaction.carousel_cards
            ],
        )
    elif interaction.kind == RuntimeInteractionKind.CTA_URL and interaction.url is not None:
        cta_kwargs: dict[str, Any] = {
            "button_text": interaction.button_text,
            "url": interaction.url,
        }
        if header_media is not None:
            cta_kwargs["header_media"] = header_media
        response = await client.send_cta_url_once(
            to,
            turn.reply,
            **cta_kwargs,
        )
    elif (
        interaction.kind == RuntimeInteractionKind.FLOW
        and interaction.flow_id is not None
        and interaction.flow_token is not None
    ):
        response = await client.send_flow_once(
            to,
            turn.reply,
            button_text=interaction.button_text,
            flow_id=interaction.flow_id,
            flow_token=interaction.flow_token,
        )
    else:  # pragma: no cover - planner constructs only complete interactions
        raise ValueError("invalid runtime interaction")
    return response, f"interactive:{interaction.kind.value}"


async def _send_typing_indicator_best_effort(message_id: str | None) -> bool:
    """Show WhatsApp's native typing UI without making replies depend on it."""

    if not message_id:
        return False
    try:
        await WhatsAppClient().send_typing_indicator(message_id)
        return True
    except Exception as exc:
        logger.warning(
            "wa_typing_indicator_failed",
            error_type=type(exc).__name__,
        )
        return False


async def _refresh_typing_indicator(
    message_id: str,
    stats: dict[str, int],
) -> None:
    """Refresh Meta's short-lived typing UI until the reply is ready."""

    while True:
        await asyncio.sleep(_TYPING_INDICATOR_REFRESH_SECONDS)
        stats["attempts"] += 1
        if await _send_typing_indicator_best_effort(message_id):
            stats["successes"] += 1
            stats["refreshes"] += 1


@celery_app.task(name="src.workers.agent_runtime.process_runtime_job")
def process_runtime_job(tenant_id: str, job_id: str) -> dict[str, Any]:
    return run_async(_process_runtime_job(UUID(tenant_id), UUID(job_id)))


@celery_app.task(name="src.workers.agent_runtime.dispatch_pending_runtime_jobs")
def dispatch_pending_runtime_jobs() -> dict[str, int]:
    return run_async(_dispatch_pending_runtime_jobs())


async def _dispatch_pending_runtime_jobs() -> dict[str, int]:
    async with session_scope() as session:
        tenants = list(
            (
                await session.execute(
                    select(Tenant.id).where(
                        Tenant.status == TenantStatus.ACTIVE,
                        Tenant.wa_business_account_id
                        == get_settings().whatsapp_business_account_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    queued = 0
    stale_before = datetime.now(UTC) - timedelta(minutes=5)
    for tenant_id in tenants:
        ready_job_ids: list[UUID] = []
        async with session_scope(tenant_id) as session:
            rows = list(
                (
                    await session.execute(
                        select(AgentRuntimeJob)
                        .where(
                            AgentRuntimeJob.tenant_id == tenant_id,
                            (
                                (AgentRuntimeJob.status == AgentRuntimeJobStatus.PENDING.value)
                                | (
                                    AgentRuntimeJob.status.in_(
                                        [
                                            AgentRuntimeJobStatus.PROCESSING.value,
                                            AgentRuntimeJobStatus.SENDING.value,
                                        ]
                                    )
                                    & (AgentRuntimeJob.updated_at < stale_before)
                                )
                            ),
                        )
                        .order_by(AgentRuntimeJob.created_at.asc())
                        .limit(50)
                    )
                )
                .scalars()
                .all()
            )
            for job in rows:
                if job.status == AgentRuntimeJobStatus.SENDING.value:
                    job.status = AgentRuntimeJobStatus.FAILED.value
                    job.completed_at = datetime.now(UTC)
                    job.error = "stale send outcome is unknown; automatic retry suppressed"
                    job.audit = {
                        **(job.audit or {}),
                        "manual_review_required": True,
                        "retry_suppressed": True,
                    }
                    conversation = await session.get(Conversation, job.conversation_id)
                    if conversation is not None:
                        await _queue_human_review(
                            session,
                            tenant_id,
                            job,
                            conversation,
                            "WhatsApp gönderim sonucu belirsiz; otomatik tekrar kapatıldı. Manuel kontrol gerekli.",
                        )
                    continue
                if job.status == AgentRuntimeJobStatus.PROCESSING.value:
                    job.status = AgentRuntimeJobStatus.PENDING.value
                ready_job_ids.append(job.id)
            await session.commit()
        # Publish only after stale-state recovery is committed. Otherwise a
        # fast worker can observe the old ``processing`` row, no-op, and leave
        # it dormant until the next recovery sweep.
        for ready_job_id in ready_job_ids:
            try:
                process_runtime_job.delay(str(tenant_id), str(ready_job_id))
                queued += 1
            except Exception as exc:
                # The durable pending row remains recoverable on the next beat.
                logger.warning(
                    "agent_runtime_enqueue_deferred",
                    job_id=str(ready_job_id),
                    error_type=type(exc).__name__,
                )
    # The existing recovery schedule drains durable operator sends too (including Windows).
    from src.workers.chat_outbound import dispatch as dispatch_chat_outbound
    for tenant_id in tenants:
        try:
            await dispatch_chat_outbound(tenant_id)
        except Exception as exc:
            logger.warning("chat_outbound_recovery_failed", error_type=type(exc).__name__)
    return {"queued": queued}


async def _process_runtime_job(tenant_id: UUID, job_id: UUID) -> dict[str, Any]:
    async with session_scope(tenant_id) as session:
        job = (
            await session.execute(
                select(AgentRuntimeJob)
                .where(AgentRuntimeJob.id == job_id)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None:
            return {"status": "locked"}
        # Only a durable pending row may start work. Duplicate Celery delivery
        # while another worker is processing is therefore a no-op.
        if job.status != AgentRuntimeJobStatus.PENDING.value:
            return {"status": job.status}
        earlier_job_id = await _ordered_conversation_job(
            session,
            job,
            direction="earlier",
            statuses={
                AgentRuntimeJobStatus.PENDING.value,
                AgentRuntimeJobStatus.PROCESSING.value,
                AgentRuntimeJobStatus.SENDING.value,
            },
        )
        if earlier_job_id is not None:
            # Keep this row durable and pending. The ordered recovery sweep
            # will enqueue it after the preceding turn reaches a terminal
            # state; processing it now could answer messages out of order.
            return {"status": "deferred", "waiting_for_job_id": str(earlier_job_id)}
        job.status = AgentRuntimeJobStatus.PROCESSING.value
        job.attempts += 1
        job.started_at = datetime.now(UTC)
        job.error = None
        await session.commit()

    try:
        return await _execute_runtime_job(tenant_id, job_id)
    except Exception as exc:
        logger.exception("agent_runtime_job_failed", job_id=str(job_id), error=str(exc))
        async with session_scope(tenant_id) as session:
            job = await session.get(AgentRuntimeJob, job_id)
            if job is not None:
                job.error = str(exc)[:2000]
                send_outcome_unknown = job.status == AgentRuntimeJobStatus.SENDING.value
                job.status = (
                    AgentRuntimeJobStatus.FAILED.value
                    if (send_outcome_unknown or job.attempts >= 5)
                    else AgentRuntimeJobStatus.PENDING.value
                )
                if job.status == AgentRuntimeJobStatus.FAILED.value:
                    job.completed_at = datetime.now(UTC)
                    job.audit = {
                        **(job.audit or {}),
                        "manual_review_required": True,
                        "retry_suppressed": send_outcome_unknown,
                    }
                    conversation = await session.get(Conversation, job.conversation_id)
                    if conversation is not None:
                        reason = (
                            "WhatsApp gönderim sonucu belirsiz; otomatik tekrar kapatıldı. Manuel kontrol gerekli."
                            if send_outcome_unknown
                            else "Bot yanıtı beş denemede hazırlanamadı. Manuel yanıt gerekli."
                        )
                        await _queue_human_review(session, tenant_id, job, conversation, reason)
                await session.commit()
        return {
            "status": "failed",
            "retryable": bool(
                job and job.status == AgentRuntimeJobStatus.PENDING.value and job.attempts < 5
            ),
        }


async def _knowledge_inputs(
    tenant_id: UUID,
    agent_version_id: UUID,
    contact_identity: str,
) -> tuple[KnowledgeRetriever | None, CustomerMemory | None, EvidenceSearch | None, EntailmentVerifier | None]:
    """GraphRAG retriever bound to the LIVE version, the customer's memory, and
    the hybrid-mode evidence search + verifier (ADR-002/ADR-003).

    All are optional decision inputs. Any failure here degrades to the lexical
    retriever / literal answers; it never blocks the reply.
    """

    from src.modules.agents.grounded_audit import build_entailment_verifier
    from src.modules.knowledge.service import (
        build_memory_store,
        build_scoped_evidence_retriever,
        build_scoped_retriever,
        memory_key,
    )

    retriever = build_scoped_retriever(tenant_id=tenant_id, agent_version_id=agent_version_id)
    evidence = build_scoped_evidence_retriever(tenant_id=tenant_id)
    verifier: EntailmentVerifier | None
    try:
        verifier = build_entailment_verifier()
    except Exception as exc:
        logger.warning("knowledge.verifier.unavailable", error=type(exc).__name__)
        verifier = None
    memory: CustomerMemory | None = None
    memory_store = build_memory_store()
    if memory_store is not None:
        try:
            memory = await memory_store.customer_memory(
                tenant_id=tenant_id,
                key=memory_key(tenant_id, contact_identity),
            )
        except Exception as exc:
            logger.warning("knowledge.memory.unavailable", error=type(exc).__name__)
            memory = None
    return retriever, memory, evidence, verifier


def _enqueue_memory_enrichment(tenant_id: UUID, job_id: UUID) -> bool:
    """Best-effort background enrichment after the turn is durable."""

    from src.modules.knowledge.service import build_memory_store

    if build_memory_store() is None:
        return False
    try:
        from src.workers.knowledge import enrich_conversation_memory

        enrich_conversation_memory.delay(str(tenant_id), str(job_id))
        return True
    except Exception as exc:  # broker down: memory is an optimization, not correctness
        logger.warning("knowledge.memory.enqueue_failed", error=type(exc).__name__)
        return False


async def _execute_runtime_job(tenant_id: UUID, job_id: UUID) -> dict[str, Any]:
    async with session_scope(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        settings = get_settings()
        if (
            tenant is None
            or tenant.status != TenantStatus.ACTIVE
            or tenant.wa_business_account_id != settings.whatsapp_business_account_id
        ):
            raise RuntimeError("tenant or WhatsApp sender binding is inactive")
        from src.modules.outreach.channel import resolve_channel
        sender = await resolve_channel(session, tenant_id)
        job = await session.get(AgentRuntimeJob, job_id)
        if job is None:
            return {"status": "missing"}
        inbound = await session.get(Message, job.inbound_message_id)
        conversation = await session.get(Conversation, job.conversation_id)
        if inbound is None or conversation is None or not inbound.body:
            job.status = AgentRuntimeJobStatus.SKIPPED.value
            job.completed_at = datetime.now(UTC)
            job.error = "inbound message has no text body"
            await session.commit()
            return {"status": job.status}

        blocking_handoff = (
            await session.execute(
                select(AgentRuntimeJob.id)
                .where(
                    AgentRuntimeJob.tenant_id == tenant_id,
                    AgentRuntimeJob.conversation_id == conversation.id,
                    AgentRuntimeJob.id != job.id,
                    AgentRuntimeJob.status.in_(
                        [
                            AgentRuntimeJobStatus.HANDOFF.value,
                            AgentRuntimeJobStatus.FAILED.value,
                        ]
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if blocking_handoff is not None:
            job.status = AgentRuntimeJobStatus.SKIPPED.value
            job.completed_at = datetime.now(UTC)
            job.error = "conversation is paused for human review"
            job.audit = {
                **(job.audit or {}),
                "blocking_handoff_job_id": str(blocking_handoff),
            }
            await session.commit()
            return {"status": job.status}

        version = await _resolve_live_version(session, tenant_id)
        if version is None:
            raise RuntimeError("no active live WhatsApp agent version")
        config = CompanyAgentConfig.model_validate(version.company_config)
        selection_active = await selection_service.applicable(
            session, config, conversation, inbound
        )

        newer_job_id = await _ordered_conversation_job(
            session,
            job,
            direction="newer",
            statuses={
                AgentRuntimeJobStatus.PENDING.value,
                AgentRuntimeJobStatus.PROCESSING.value,
                AgentRuntimeJobStatus.SENDING.value,
            },
        )
        if newer_job_id is not None and not selection_active:
            job.status = AgentRuntimeJobStatus.SKIPPED.value
            job.completed_at = datetime.now(UTC)
            job.error = "coalesced into a newer inbound turn"
            job.audit = {
                **(job.audit or {}),
                "coalesced_into_job_id": str(newer_job_id),
            }
            await session.commit()
            return {"status": job.status}

        contact = await session.get(LeadContact, conversation.contact_id)
        if contact is None:
            raise RuntimeError("runtime contact is missing")
        opted_out = (
            await session.execute(
                select(OptOut.id).where(
                    OptOut.tenant_id == tenant_id,
                    OptOut.phone_e164 == contact.normalized_value,
                )
            )
        ).scalar_one_or_none()
        if str(contact.consent_status) == ConsentStatus.OPT_OUT.value or opted_out is not None:
            job.status = AgentRuntimeJobStatus.SKIPPED.value
            job.completed_at = datetime.now(UTC)
            job.error = "contact opted out"
            await session.commit()
            return {"status": job.status}

        history, context_fact_ids = await _conversation_history_with_context(
            session,
            conversation.id,
            inbound,
            agent_version_id=version.id,
        )
        # Guardrail gate (plan WP1): classify the customer message before the
        # typing indicator, the selection flow and any model call. A block or
        # an unavailable classifier (closed mode) yields an approved turn that
        # travels through the normal durable send path below.
        guard_verdict: GuardVerdict | None = None
        guard_turn: RuntimeTurn | None = None
        guard = build_input_guard()
        if guard.available:
            guard_verdict = await guard.check_customer_message(
                inbound.body,
                history=[(m.role, m.content) for m in history[-4:]],
                topic=TopicContext.from_config(config),
            )
            guard_turn = guardrail_blocked_turn(config, guard_verdict)
            if guard_turn is not None:
                logger.info(
                    "guardrail.blocked",
                    job_id=str(job.id),
                    decision=guard_verdict.decision.value,
                    reason=guard_verdict.reason,
                )
        typing_stats = {"attempts": 0, "successes": 0, "refreshes": 0}
        typing_refresh_task: asyncio.Task[None] | None = None
        if inbound.wa_message_id:
            typing_stats["attempts"] = 1
            if await _send_typing_indicator_best_effort(inbound.wa_message_id):
                typing_stats["successes"] = 1
            typing_refresh_task = asyncio.create_task(
                _refresh_typing_indicator(inbound.wa_message_id, typing_stats)
            )
        try:
            selection_request = None
            selection_confirmed = False
            turn = guard_turn
            customer_memory: CustomerMemory | None = None
            resume_prompt = None
            selection_side_handoff = False
            selection_processing_ms = None
            if turn is None and selection_active:
                selection_started = perf_counter()
                (
                    turn,
                    resume_prompt,
                    selection_request,
                    selection_confirmed,
                ) = await selection_service.handle(session, config, conversation, inbound)
                selection_processing_ms = round((perf_counter() - selection_started) * 1000, 2)
            selection_deterministic = turn is not None and guard_turn is None
            if turn is None:
                fact_retriever, customer_memory, evidence_search, verifier = await _knowledge_inputs(
                    tenant_id,
                    version.id,
                    contact.normalized_value,
                )
                turn = await CompanyAgentRuntime(
                    config,
                    get_llm_client("customer"),
                    whatsapp_capabilities=_tenant_whatsapp_capabilities(tenant_id),
                    fact_retriever=fact_retriever,
                    customer_memory=customer_memory,
                    evidence_retriever=evidence_search,
                    entailment_verifier=verifier,
                    generation_llm=role_llm_client("generation"),
                ).reply(inbound.body, history=history, context_fact_ids=context_fact_ids)
                if resume_prompt is not None and turn.action in {
                    CustomerReplyAction.REPLY,
                    CustomerReplyAction.ASK_CLARIFICATION,
                    CustomerReplyAction.HANDOFF,
                    CustomerReplyAction.DECLINE,
                }:
                    selection_side_handoff = turn.action == CustomerReplyAction.HANDOFF
                    suffix = selection_service.as_turn(resume_prompt)
                    combined = turn.reply + "\n\n" + suffix.reply
                    turn = replace(
                        turn,
                        action=CustomerReplyAction.REPLY,
                        reply=combined,
                        interaction=suffix.interaction if len(combined) <= 1024 else None,
                    )

        finally:
            if typing_refresh_task is not None:
                typing_refresh_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await typing_refresh_task

        # A second customer message may arrive while the local model is
        # deciding. Prefer one reply to the latest turn instead of sending a
        # stale answer followed immediately by another bot message.
        newer_job_id = await _ordered_conversation_job(
            session,
            job,
            direction="newer",
            statuses={
                AgentRuntimeJobStatus.PENDING.value,
                AgentRuntimeJobStatus.PROCESSING.value,
                AgentRuntimeJobStatus.SENDING.value,
            },
        )
        if newer_job_id is not None and not selection_active:
            job.status = AgentRuntimeJobStatus.SKIPPED.value
            job.completed_at = datetime.now(UTC)
            job.error = "coalesced into a newer inbound turn"
            job.audit = {
                **(job.audit or {}),
                "coalesced_into_job_id": str(newer_job_id),
            }
            await session.commit()
            return {"status": job.status}

        # Serialize the final send boundary with inbound processing for this
        # tenant/phone. The guard transaction intentionally stays open across
        # both the durable SENDING commit and the one Meta POST: a concurrent
        # STOP webhook either commits first (and is observed below) or waits
        # until this already-claimed send has completed. A separate connection
        # is required because committing SENDING must not release the guard.
        async with session_scope(tenant_id) as guard_session:
            await guard_session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": f"{tenant_id}:{contact.normalized_value}"},
            )

            # The model call may have taken long enough for STOP, suspension,
            # or a sender-binding change to commit in another transaction.
            # Refresh every decision input after acquiring the shared identity
            # lock; never trust the ORM snapshots loaded before the model call.
            await session.refresh(job)
            await session.refresh(tenant)
            await session.refresh(contact)
            if job.status != AgentRuntimeJobStatus.PROCESSING.value:
                result_status = job.status
                if selection_request is not None and (job.audit or {}).get("cancelled_by_opt_out"):
                    request_id = selection_request.id
                    # STOP skips locked drafts to avoid a lock inversion with this
                    # send guard. Discard the uncommitted answer before cancelling.
                    await session.rollback()
                    from src.modules.selection.models import SelectionRequest

                    draft = await session.get(SelectionRequest, request_id)
                    if draft is not None and draft.status == "draft":
                        draft.status = "cancelled"
                        draft.revision += 1
                        await session.commit()
                return {"status": result_status}
            try:
                boundary_sender = await resolve_channel(session, tenant_id)
                binding_changed = boundary_sender.id != sender.id or boundary_sender.agent_id != version.agent_id
            except Exception:
                binding_changed = True

            if (
                binding_changed or tenant.status != TenantStatus.ACTIVE
                or tenant.wa_business_account_id != settings.whatsapp_business_account_id
            ):
                job.status = AgentRuntimeJobStatus.SKIPPED.value
                job.completed_at = datetime.now(UTC)
                job.error = "tenant or WhatsApp sender binding became inactive before send"
                job.audit = {**(job.audit or {}), "send_boundary_rejected": "tenant_binding"}
                await session.commit()
                return {"status": job.status}

            boundary_opted_out = (
                await session.execute(
                    select(OptOut.id).where(
                        OptOut.tenant_id == tenant_id,
                        OptOut.phone_e164 == contact.normalized_value,
                    )
                )
            ).scalar_one_or_none()
            if str(contact.consent_status) == ConsentStatus.OPT_OUT.value or boundary_opted_out is not None:
                job.status = AgentRuntimeJobStatus.SKIPPED.value
                job.completed_at = datetime.now(UTC)
                job.error = "contact opted out before outbound send"
                job.audit = {
                    **(job.audit or {}),
                    "cancelled_by_opt_out": True,
                    "send_boundary_rejected": "opt_out",
                }
                await session.commit()
                return {"status": job.status}

            # Persist the transition before the only external POST. If the
            # process dies or the response is ambiguous after this commit,
            # recovery marks the job for human review and never replays it.
            job.agent_version_id = version.id
            job.action = turn.action.value
            job.fact_ids = list(turn.fact_ids)
            job.used_fallback = turn.used_fallback
            job.status = AgentRuntimeJobStatus.SENDING.value
            job.audit = {
                "model": (
                    "guardrail"
                    if guard_turn is not None
                    else "deterministic_selection"
                    if selection_deterministic
                    else settings.llm_model
                ),
                "guardrail": guard_verdict.audit() if guard_verdict is not None else None,
                "models": _model_audit(settings),
                "selection_request_id": str(selection_request.id) if selection_request else None,
                "selection_confirmed": selection_confirmed,
                "selection_processing_ms": selection_processing_ms,
                "provider": settings.llm_provider,
                "response_source": turn.response_source,
                "fallback_reason": turn.fallback_reason,
                "history_messages": len(history),
                "context_fact_ids": list(context_fact_ids),
                "typing_indicator_sent": typing_stats["successes"] > 0,
                "typing_indicator_attempts": typing_stats["attempts"],
                "typing_indicator_refreshes": typing_stats["refreshes"],
                "planned_reply": turn.reply,
                "planned_interaction": _interaction_audit(turn),
                "request_resolutions": list(turn.request_resolutions),
                "retrieval": turn.retrieval,
                "answer_origin": turn.answer_origin,
                "answer_verified": turn.answer_verified,
                "evidence_ids": list(turn.evidence_ids),
                "generation": turn.generation,
                "customer_memory": (
                    {"subjects": list(customer_memory.subject_ids), "turns": customer_memory.turns}
                    if customer_memory is not None and not customer_memory.empty
                    else None
                ),
                "send_started_at": datetime.now(UTC).isoformat(),
                "external_send_attempts": 1,
            }
            if selection_side_handoff:
                await _queue_human_review(
                    session,
                    tenant_id,
                    job,
                    conversation,
                    "Seçim sırasında gelen ek soru teknik ekip yanıtı bekliyor.",
                )
            if selection_confirmed and selection_request is not None:
                await _queue_human_review(
                    session,
                    tenant_id,
                    job,
                    conversation,
                    "Müşteri seçim özetini onayladı; teknik/satış incelemesi bekliyor.",
                )
                selection_request.assigned_to = conversation.assigned_to
            await session.commit()

            response, transport_type = await _send_runtime_turn_once(
                contact.normalized_value,
                turn,
            )
            wa_id: str | None = None
            with contextlib.suppress(KeyError, IndexError, TypeError):
                wa_id = response.get("messages", [{}])[0].get("id")
            if not wa_id:
                raise RuntimeError("WhatsApp response did not include a message id")

            outbound = Message(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                direction=MessageDirection.OUTBOUND,
                message_type=MessageType.TEXT,
                body=turn.reply,
                wa_message_id=wa_id,
                raw={
                    "transport": response,
                    "runtime_job_id": str(job.id),
                    "in_reply_to_wa_message_id": inbound.wa_message_id,
                    "agent_version_id": str(version.id),
                    "action": turn.action.value,
                    "fact_ids": list(turn.fact_ids),
                    "used_fallback": turn.used_fallback,
                    "response_source": turn.response_source,
                    "answer_origin": turn.answer_origin,
                    "evidence_ids": list(turn.evidence_ids),
                    "transport_type": transport_type,
                    "interaction": _interaction_audit(turn),
                    "request_resolutions": list(turn.request_resolutions),
                },
            )
            session.add(outbound)
            await session.flush()
            conversation.last_message_at = datetime.now(UTC)
            job.outbound_message_id = outbound.id
            job.outbound_wa_message_id = wa_id
            job.status = (
                AgentRuntimeJobStatus.HANDOFF.value
                if turn.action == CustomerReplyAction.HANDOFF
                else AgentRuntimeJobStatus.SENT.value
            )
            job.completed_at = datetime.now(UTC)
            job.audit = {
                **(job.audit or {}),
                "send_completed_at": job.completed_at.isoformat(),
            }
            if turn.action == CustomerReplyAction.HANDOFF:
                await _queue_human_review(
                    session,
                    tenant_id,
                    job,
                    conversation,
                    "Bot bu talebi otomatik yanıtlayamadı. İnsan incelemesi ve gerekirse manuel yanıt gerekli.",
                )
            await session.commit()
            # The turn is durable: enrich the customer's memory graph off the
            # critical path (knowledge queue), never before the Meta POST.
            _enqueue_memory_enrichment(tenant_id, job.id)
            return {"status": job.status, "wa_message_id": wa_id}


async def _queue_human_review(
    session: Any,
    tenant_id: UUID,
    job: AgentRuntimeJob,
    conversation: Conversation,
    reason: str,
) -> None:
    """Assign an open inbox conversation and leave an internal audit message."""

    conversation.status = ConversationStatus.OPEN
    if conversation.assigned_to is None:
        assignee = (
            await session.execute(
                select(User.id)
                .where(User.tenant_id == tenant_id, User.is_active.is_(True))
                .order_by(
                    case(
                        (User.role == UserRole.TENANT_OWNER, 0),
                        (User.role == UserRole.SALES_MANAGER, 1),
                        (User.role == UserRole.SALES_AGENT, 2),
                        else_=3,
                    ),
                    User.created_at.asc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        conversation.assigned_to = assignee

    job.audit = {
        **(job.audit or {}),
        "manual_review_required": True,
        "human_review_queued_at": datetime.now(UTC).isoformat(),
        "assigned_to": str(conversation.assigned_to) if conversation.assigned_to else None,
    }

    session.add(
        Message(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            direction=MessageDirection.OUTBOUND,
            message_type=MessageType.SYSTEM,
            body=reason,
            raw={"internal_only": True, "runtime_job_id": str(job.id)},
        )
    )


async def _ordered_conversation_job(
    session: Any,
    job: AgentRuntimeJob,
    *,
    direction: str,
    statuses: set[str],
) -> UUID | None:
    """Find a live job before/after ``job`` in one deterministic turn order."""

    if direction == "earlier":
        position = or_(
            AgentRuntimeJob.created_at < job.created_at,
            and_(
                AgentRuntimeJob.created_at == job.created_at,
                AgentRuntimeJob.id < job.id,
            ),
        )
        ordering = (AgentRuntimeJob.created_at.asc(), AgentRuntimeJob.id.asc())
    elif direction == "newer":
        position = or_(
            AgentRuntimeJob.created_at > job.created_at,
            and_(
                AgentRuntimeJob.created_at == job.created_at,
                AgentRuntimeJob.id > job.id,
            ),
        )
        ordering = (AgentRuntimeJob.created_at.desc(), AgentRuntimeJob.id.desc())
    else:  # pragma: no cover - private helper has two fixed callers
        raise ValueError("direction must be 'earlier' or 'newer'")

    result: UUID | None = (
        await session.execute(
            select(AgentRuntimeJob.id)
            .where(
                AgentRuntimeJob.tenant_id == job.tenant_id,
                AgentRuntimeJob.conversation_id == job.conversation_id,
                AgentRuntimeJob.id != job.id,
                AgentRuntimeJob.status.in_(statuses),
                position,
            )
            .order_by(*ordering)
            .limit(1)
        )
    ).scalar_one_or_none()
    return result


async def _resolve_live_version(session: Any, tenant_id: UUID) -> AgentVersion | None:
    stmt = (
        select(AgentVersion)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .where(
            AgentVersion.tenant_id == tenant_id,
            AgentVersion.status == AgentVersionStatus.LIVE,
            Agent.tenant_id == tenant_id,
            Agent.is_active.is_(True),
        )
        .order_by(AgentVersion.version.desc())
    )
    from src.modules.outreach.channel import resolve_channel
    sender = await resolve_channel(session, tenant_id)
    stmt = stmt.where(Agent.id == sender.agent_id)
    result: AgentVersion | None = (await session.execute(stmt)).scalars().first()
    return result


async def _conversation_history(
    session: Any, conversation_id: UUID, current_message: Message
) -> list[LLMMessage]:
    history, _ = await _conversation_history_with_context(
        session,
        conversation_id,
        current_message,
        agent_version_id=None,
    )
    return history


def _trusted_runtime_context_fact_ids(
    message: Message | None,
    *,
    agent_version_id: UUID | None,
) -> tuple[str, ...]:
    """Read canonical fact ids only from a matching server-owned bot turn."""

    if (
        message is None
        or agent_version_id is None
        or message.direction != MessageDirection.OUTBOUND
        or not isinstance(message.raw, dict)
    ):
        return ()
    raw = message.raw
    fact_ids = raw.get("fact_ids")
    if (
        not isinstance(raw.get("runtime_job_id"), str)
        or raw.get("agent_version_id") != str(agent_version_id)
        or raw.get("action") != CustomerReplyAction.REPLY.value
        or not isinstance(fact_ids, list)
        or not 1 <= len(fact_ids) <= 9
        or any(not isinstance(fact_id, str) for fact_id in fact_ids)
        or len(set(fact_ids)) != len(fact_ids)
    ):
        return ()
    return tuple(fact_ids)


async def _conversation_history_with_context(
    session: Any,
    conversation_id: UUID,
    current_message: Message,
    *,
    agent_version_id: UUID | None,
) -> tuple[list[LLMMessage], tuple[str, ...]]:
    precedes_current = or_(
        Message.created_at < current_message.created_at,
        and_(
            Message.created_at == current_message.created_at,
            Message.id < current_message.id,
        ),
    )
    rows = list(
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    precedes_current,
                    Message.body.is_not(None),
                    Message.message_type == MessageType.TEXT,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(_HISTORY_MESSAGE_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    current_raw = current_message.raw if isinstance(current_message.raw, dict) else {}
    quoted_context = current_raw.get("context")
    quoted_wa_message_id = (
        quoted_context.get("id")
        if isinstance(quoted_context, dict) and isinstance(quoted_context.get("id"), str)
        else None
    )
    context_message: Message | None
    if quoted_wa_message_id:
        context_message = (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.wa_message_id == quoted_wa_message_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    else:
        # Context has a stricter barrier than LLM history. The latest outbound
        # message of any type wins, including a manual text, template, image,
        # document or internal system turn. If that message has no matching
        # runtime metadata, do not jump backwards and revive stale bot facts.
        # Explicit WhatsApp quotes above intentionally override this barrier.
        context_message = (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.direction == MessageDirection.OUTBOUND,
                    precedes_current,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    context_fact_ids = _trusted_runtime_context_fact_ids(
        context_message,
        agent_version_id=agent_version_id,
    )
    # The production llama.cpp runtime uses a measured 4096-token context.
    # Keep recent history bounded so approved facts, the current turn and the
    # 256-token decision budget retain ample headroom.
    remaining_characters = _HISTORY_CHARACTER_LIMIT
    history_desc: list[LLMMessage] = []
    for item in rows:
        if (agent_version_id is not None and item.direction == MessageDirection.OUTBOUND
                and isinstance(item.raw, dict) and item.raw.get("runtime_job_id")
                and item.raw.get("agent_version_id") != str(agent_version_id)):
            break
        content = (item.body or "")[:_HISTORY_MESSAGE_CHARACTER_LIMIT]
        if not content or remaining_characters <= 0:
            continue
        content = content[:remaining_characters]
        remaining_characters -= len(content)
        history_desc.append(
            LLMMessage(
                role=("user" if item.direction == MessageDirection.INBOUND else "assistant"),
                content=content,
            )
        )
    history_desc.reverse()
    return history_desc, context_fact_ids
