# ruff: noqa: RUF001
"""Durable WhatsApp inbound -> approved company agent -> outbound worker."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, case, or_, select, text

from src.core.agent_celery_app import agent_celery_app as celery_app
from src.core.config import get_settings
from src.core.db import session_scope
from src.integrations.llm import LLMMessage, get_llm_client
from src.integrations.whatsapp import WhatsAppClient
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import CompanyAgentRuntime, CustomerReplyAction
from src.modules.agents.models import Agent, AgentVersion, AgentVersionStatus
from src.modules.agents.runtime_models import AgentRuntimeJob, AgentRuntimeJobStatus
from src.modules.auth.models import Tenant, TenantStatus, User, UserRole
from src.modules.compliance.models import OptOut
from src.modules.discovery.models import ConsentStatus, LeadContact
from src.modules.outreach.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    MessageType,
)

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)


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
        if newer_job_id is not None:
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
        if contact.consent_status == ConsentStatus.OPT_OUT or opted_out is not None:
            job.status = AgentRuntimeJobStatus.SKIPPED.value
            job.completed_at = datetime.now(UTC)
            job.error = "contact opted out"
            await session.commit()
            return {"status": job.status}

        version = await _resolve_live_version(session, tenant_id)
        if version is None:
            raise RuntimeError("no active live WhatsApp agent version")
        config = CompanyAgentConfig.model_validate(version.company_config)
        history = await _conversation_history(session, conversation.id, inbound)
        typing_indicator_sent = await _send_typing_indicator_best_effort(inbound.wa_message_id)
        turn = await CompanyAgentRuntime(config, get_llm_client()).reply(
            inbound.body, history=history
        )

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
        if newer_job_id is not None:
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
                return {"status": job.status}
            if (
                tenant.status != TenantStatus.ACTIVE
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
            if contact.consent_status == ConsentStatus.OPT_OUT or boundary_opted_out is not None:
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
                "model": settings.llm_model,
                "provider": settings.llm_provider,
                "history_messages": len(history),
                "typing_indicator_sent": typing_indicator_sent,
                "planned_reply": turn.reply,
                "send_started_at": datetime.now(UTC).isoformat(),
                "external_send_attempts": 1,
            }
            await session.commit()

            response = await WhatsAppClient().send_text_once(contact.normalized_value, turn.reply)
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

    return (
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
    slug = get_settings().whatsapp_agent_slug.strip()
    if slug:
        stmt = stmt.where(Agent.slug == slug)
    return (await session.execute(stmt)).scalars().first()


async def _conversation_history(
    session: Any, conversation_id: UUID, current_message: Message
) -> list[LLMMessage]:
    rows = list(
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    or_(
                        Message.created_at < current_message.created_at,
                        and_(
                            Message.created_at == current_message.created_at,
                            Message.id < current_message.id,
                        ),
                    ),
                    Message.body.is_not(None),
                    Message.message_type == MessageType.TEXT,
                )
                .order_by(Message.created_at.desc())
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    # Qwen runs with a 4096-token context. Keep recent history bounded so the
    # approved system facts and current customer turn cannot be truncated.
    remaining_characters = 6000
    history_desc: list[LLMMessage] = []
    for item in rows:
        content = (item.body or "")[:1500]
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
    return history_desc
