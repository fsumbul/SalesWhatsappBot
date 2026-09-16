"""WhatsApp webhook receiver — one endpoint per tenant slug."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.db import session_scope, set_tenant_context
from src.core.runtime_timing import timing_event
from src.integrations.whatsapp import WhatsAppClient
from src.modules.agents.runtime_models import AgentRuntimeJob, AgentRuntimeJobStatus
from src.modules.auth.models import Tenant, TenantStatus
from src.modules.compliance.models import OptOut, OptOutSource
from src.modules.discovery.models import (
    ConsentStatus,
    ContactType,
    Lead,
    LeadContact,
    LeadPriority,
    LeadStatus,
)
from src.modules.outreach.models import (
    Conversation,
    Message,
    MessageDirection,
    MessageType,
    OutreachJob,
    OutreachJobStatus,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])

_OPT_OUT_RE = re.compile(
    r"""
    ^\s*(?:
        stop
        | dur
        | istemiyorum
        | ilgilenmiyorum
        | unsubscribe
        | stopp
        | الإلغاء
        | стоп
        | (?:(?:art\u0131k|artik)\s+)?(?:bana\s+)?mesaj(?:lar[\u0131i])?\s+
          (?:almak\s+)?istemiyorum
        | (?:(?:art\u0131k|artik)\s+)?(?:bana\s+)?(?:bir\s+daha\s+)?mesaj\s+
          (?:atma(?:y[\u0131i]n)?|g[öo]nderme(?:y[\u0131i]n)?)
        | (?:bana\s+)?bir\s+daha\s+yazma(?:y[\u0131i]n)?
        | beni\s+(?:mesaj\s+)?liste(?:niz)?den\s+(?:ç\u0131kar|cikar)(?:[\u0131i]n)?
        | abonelikten\s+(?:ç\u0131kar|cikar)(?:[\u0131i]n)?
        | pazarlama\s+(?:mesaj[\u0131i]|iletişim(?:i)?|iletisim(?:i)?)\s+istemiyorum
        | (?:art\u0131k|artik)\s+(?:iletişim|iletisim)\s+istemiyorum
        | (?:beni\s+)?rahats[\u0131i]z\s+etme(?:y[\u0131i]n)?
    )\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DELIVERY_SUCCESS_RANK = {"sent": 1, "delivered": 2, "read": 3}
_DELIVERY_STATUSES = {*_DELIVERY_SUCCESS_RANK, "failed"}
_RUNTIME_DELIVERY_HISTORY_LIMIT = 100
_RUNTIME_DELIVERY_ERRORS_LIMIT = 100
_OUTREACH_ERROR_HISTORY_LIMIT = 20


def _delivery_status_name(value: object) -> str | None:
    if isinstance(value, OutreachJobStatus):
        value = value.value
    return value if isinstance(value, str) and value in _DELIVERY_STATUSES else None


def _next_delivery_status(current: object, incoming: str) -> str | None:
    """Return a monotonic, conservative effective Meta delivery status.

    Successful evidence only moves ``sent -> delivered -> read``. A failure
    is accepted only before any successful callback; it never retracts proof
    that Meta already accepted, delivered, or exposed the message as read.
    Once failed, a later bare ``sent`` callback is also insufficient to clear
    that failure, while ``delivered`` or ``read`` is conclusive and may do so.
    """

    current_name = _delivery_status_name(current)
    if incoming not in _DELIVERY_STATUSES:
        return current_name
    if incoming == "failed":
        return current_name if current_name in _DELIVERY_SUCCESS_RANK else "failed"
    if current_name == "failed" and incoming == "sent":
        return "failed"
    if current_name not in _DELIVERY_SUCCESS_RANK:
        return incoming
    if _DELIVERY_SUCCESS_RANK[incoming] > _DELIVERY_SUCCESS_RANK[current_name]:
        return incoming
    return current_name


def _meta_callback_at(status_payload: dict[str, Any]) -> tuple[datetime, str]:
    """Parse Meta's Unix-seconds callback time, falling back to receipt time."""

    received_at = datetime.now(UTC)
    raw_timestamp = status_payload.get("timestamp")
    if isinstance(raw_timestamp, bool) or not isinstance(raw_timestamp, str | int | float):
        return received_at, "received_at"
    try:
        callback_at = datetime.fromtimestamp(int(raw_timestamp), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return received_at, "received_at"
    return callback_at, "meta"


def _earliest_timestamp(current: datetime | None, incoming: datetime) -> datetime:
    return incoming if current is None or incoming < current else current


def _meta_errors(status_payload: dict[str, Any]) -> list[Any]:
    errors = status_payload.get("errors")
    if errors is None:
        return []
    return errors if isinstance(errors, list) else [errors]


def _append_outreach_failure(job: OutreachJob, callback_at: datetime, errors: list[Any]) -> None:
    entry = json.dumps(
        {
            "status": "failed",
            "callback_at": callback_at.isoformat(),
            "errors": errors,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    existing = (job.error or "").splitlines()
    if entry not in existing:
        existing.append(entry)
    job.error = "\n".join(existing[-_OUTREACH_ERROR_HISTORY_LIMIT:])


def _append_runtime_delivery_audit(
    runtime_job: AgentRuntimeJob,
    status_name: str,
    callback_at: datetime,
    timestamp_source: str,
    errors: list[Any],
) -> None:
    audit = dict(runtime_job.audit or {})
    event: dict[str, Any] = {
        "status": status_name,
        "callback_at": callback_at.isoformat(),
        "timestamp_source": timestamp_source,
    }
    if errors:
        event["errors"] = errors

    raw_history = audit.get("delivery_history")
    history = list(raw_history) if isinstance(raw_history, list) else []
    if event not in history:
        history.append(event)
    audit["delivery_history"] = history[-_RUNTIME_DELIVERY_HISTORY_LIMIT:]

    raw_delivery_errors = audit.get("delivery_errors")
    delivery_errors = list(raw_delivery_errors) if isinstance(raw_delivery_errors, list) else []
    for error in errors:
        error_event = {
            "status": status_name,
            "callback_at": callback_at.isoformat(),
            "error": error,
        }
        if error_event not in delivery_errors:
            delivery_errors.append(error_event)
    audit["delivery_errors"] = delivery_errors[-_RUNTIME_DELIVERY_ERRORS_LIMIT:]

    current_status = _delivery_status_name(audit.get("delivery_status"))
    effective_status = _next_delivery_status(current_status, status_name)
    if effective_status is not None:
        if effective_status != current_status:
            audit["delivery_status"] = effective_status
            audit["delivery_status_at"] = callback_at.isoformat()
        elif effective_status == status_name:
            try:
                current_at = datetime.fromisoformat(str(audit.get("delivery_status_at")))
            except (TypeError, ValueError):
                current_at = None
            if current_at is not None and current_at.tzinfo is None:
                current_at = current_at.replace(tzinfo=UTC)
            audit["delivery_status_at"] = _earliest_timestamp(current_at, callback_at).isoformat()
    runtime_job.audit = audit


@router.get("/{tenant_slug}")
async def verify_webhook(
    tenant_slug: str,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
) -> object:
    """Meta webhook verification handshake."""
    expected = get_settings().whatsapp_webhook_verify_token
    if hub_mode == "subscribe" and hub_verify_token == expected and hub_challenge:
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify token mismatch")


@router.post("/{tenant_slug}", status_code=status.HTTP_200_OK)
async def receive_webhook(
    tenant_slug: str,
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    webhook_received_at = datetime.now(UTC)
    webhook_started = perf_counter()
    raw = await request.body()
    settings = get_settings()
    wa = WhatsAppClient()
    if not settings.whatsapp_app_secret:
        logger.error("webhook_signature_secret_missing")
        raise HTTPException(status_code=503, detail="webhook authentication unavailable")
    if not wa.verify_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="invalid signature")

    payload = await request.json()
    async with session_scope() as session:
        # Locate tenant
        t_stmt = select(Tenant).where(Tenant.slug == tenant_slug)
        tenant = (await session.execute(t_stmt)).scalar_one_or_none()
        if tenant is None:
            logger.warning("webhook_unknown_tenant", slug=tenant_slug)
            return {"ok": False}
        if tenant.status != TenantStatus.ACTIVE:
            logger.warning("webhook_inactive_tenant", slug=tenant_slug)
            raise HTTPException(status_code=403, detail="tenant is not active")
        if (
            not settings.whatsapp_business_account_id
            or tenant.wa_business_account_id != settings.whatsapp_business_account_id
        ):
            logger.error("webhook_tenant_waba_mismatch", slug=tenant_slug)
            raise HTTPException(status_code=403, detail="sender binding mismatch")
        await set_tenant_context(session, tenant.id)

        from .channel import resolve_channel
        sender = await resolve_channel(session, tenant.id)
        runtime_job_ids: list[UUID] = []
        for entry in payload.get("entry", []):
            if entry.get("id") != sender.business_account_id:
                logger.warning("webhook_entry_waba_mismatch", slug=tenant_slug)
                raise HTTPException(status_code=403, detail="sender binding mismatch")
            for change in entry.get("changes", []):
                value = change.get("value", {})
                metadata = value.get("metadata") or {}
                if metadata.get("phone_number_id") != sender.phone_number_id:
                    logger.warning("webhook_phone_number_mismatch", slug=tenant_slug)
                    raise HTTPException(status_code=403, detail="sender binding mismatch")
                await _handle_statuses(session, tenant.id, value.get("statuses", []))
                runtime_job_ids.extend(
                    await _handle_messages(session, tenant.id, value.get("messages", []),
                                           webhook_received_at=webhook_received_at)
                )
        await session.commit()

    # The webhook must return to Meta quickly. Jobs are durable in Postgres;
    # the periodic recovery dispatcher will enqueue them if Redis is briefly
    # unavailable here.
    webhook_persist_ms = round((perf_counter() - webhook_started) * 1000, 2)
    if runtime_job_ids:
        from src.workers.agent_runtime import process_runtime_job

        for job_id in dict.fromkeys(runtime_job_ids):
            dispatch_started = perf_counter()
            dispatch_status = "enqueued"
            try:
                process_runtime_job.delay(str(tenant.id), str(job_id))
            except Exception as exc:
                dispatch_status = "deferred"
                logger.warning("agent_runtime_enqueue_deferred", job_id=str(job_id), error=str(exc))
            finally:
                if settings.runtime_timing_enabled:
                    timing_event("runtime.webhook.timing", job_id=str(job_id),
                                webhook_received_at=webhook_received_at.isoformat(),
                                persist_ms=webhook_persist_ms,
                                dispatch_ms=round((perf_counter() - dispatch_started) * 1000, 2),
                                status=dispatch_status)
    return {"ok": True}


async def _handle_statuses(
    session: AsyncSession, tenant_id: UUID, statuses: list[dict[str, Any]]
) -> None:
    for st in statuses:
        wa_id = st.get("id")
        status_name = st.get("status")
        if not wa_id or not isinstance(status_name, str):
            continue
        from src.modules.admin_chat.outbound_models import OutboundRecipient
        outbound = await session.scalar(select(OutboundRecipient).where(
            OutboundRecipient.tenant_id == tenant_id, OutboundRecipient.wa_message_id == wa_id))
        callback = st.get("biz_opaque_callback_data", "")
        if outbound is None and isinstance(callback, str) and callback.startswith("chat-outbound:"):
            try:
                recipient_id = UUID(callback.removeprefix("chat-outbound:"))
                outbound = await session.scalar(select(OutboundRecipient).where(
                    OutboundRecipient.tenant_id == tenant_id, OutboundRecipient.id == recipient_id,
                    OutboundRecipient.status.in_(["sending", "ambiguous"])))
            except ValueError:
                pass
        if outbound is not None:
            ranks = {"sending": 0, "ambiguous": 0, "accepted": 0, "failed": 0, "sent": 1, "delivered": 2, "read": 3}
            if status_name in {"sent", "delivered", "read", "failed"} and ranks.get(status_name, 0) >= ranks.get(outbound.status, 0):
                outbound.status = status_name
                outbound.wa_message_id = wa_id
                if status_name == "failed":
                    outbound.reason = "Meta teslimatı başarısız bildirdi; otomatik tekrar yapılmaz."
                else:
                    outbound.reason = None
        manual_stmt = select(Message).where(Message.tenant_id == tenant_id, Message.direction == MessageDirection.OUTBOUND)
        if isinstance(callback, str) and callback.startswith("workflow-manual:"):
            try:
                manual_stmt = manual_stmt.where(Message.id == UUID(callback.removeprefix("workflow-manual:")))
            except ValueError:
                manual_stmt = manual_stmt.where(Message.wa_message_id == wa_id)
        else:
            manual_stmt = manual_stmt.where(Message.wa_message_id == wa_id)
        manual = await session.scalar(manual_stmt.with_for_update().execution_options(populate_existing=True))
        if manual is not None and manual.raw.get("manual_send_state"):
            effective = _next_delivery_status(manual.raw.get("delivery_status"), status_name)
            if effective:
                manual.wa_message_id = wa_id
                manual.raw = {**manual.raw, "delivery_status": effective, "manual_send_state": "sent" if effective in {"sent", "delivered", "read"} else manual.raw["manual_send_state"]}
        callback_at, timestamp_source = _meta_callback_at(st)
        errors = _meta_errors(st)
        stmt = select(OutreachJob).where(
            OutreachJob.tenant_id == tenant_id, OutreachJob.wa_message_id == wa_id
        )
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is None:
            runtime_job = (
                await session.execute(
                    select(AgentRuntimeJob).where(
                        AgentRuntimeJob.tenant_id == tenant_id,
                        AgentRuntimeJob.outbound_wa_message_id == wa_id,
                    ).with_for_update()
                )
            ).scalar_one_or_none()
            if runtime_job is not None:
                _append_runtime_delivery_audit(
                    runtime_job,
                    status_name,
                    callback_at,
                    timestamp_source,
                    errors,
                )
            continue

        if status_name == "sent":
            job.sent_at = _earliest_timestamp(job.sent_at, callback_at)
        elif status_name == "delivered":
            job.delivered_at = _earliest_timestamp(job.delivered_at, callback_at)
        elif status_name == "read":
            job.read_at = _earliest_timestamp(job.read_at, callback_at)
        elif status_name == "failed":
            _append_outreach_failure(job, callback_at, errors)

        effective_status = _next_delivery_status(job.status, status_name)
        if effective_status is not None:
            job.status = OutreachJobStatus(effective_status)


async def _cancel_runtime_jobs_for_opt_out(
    session: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    opted_out_at: datetime,
) -> set[UUID]:
    """Terminalize every not-yet-sent bot turn while the phone lock is held."""

    jobs = list(
        (
            await session.execute(
                select(AgentRuntimeJob)
                .where(
                    AgentRuntimeJob.tenant_id == tenant_id,
                    AgentRuntimeJob.conversation_id == conversation_id,
                    AgentRuntimeJob.status.in_(
                        [
                            AgentRuntimeJobStatus.PENDING.value,
                            AgentRuntimeJobStatus.PROCESSING.value,
                        ]
                    ),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for job in jobs:
        job.status = AgentRuntimeJobStatus.SKIPPED.value
        job.completed_at = opted_out_at
        job.error = "contact opted out before outbound send"
        job.audit = {
            **(job.audit or {}),
            "cancelled_by_opt_out": True,
            "opt_out_at": opted_out_at.isoformat(),
        }
    return {job.id for job in jobs}


async def _handle_messages(
    session: AsyncSession, tenant_id: UUID, messages: list[dict[str, Any]],
    *, webhook_received_at: datetime | None = None,
) -> list[UUID]:
    runtime_job_ids: list[UUID] = []
    for m in messages:
        from_number = _normalize_wa_number(m.get("from"))
        if not from_number:
            logger.warning("webhook_inbound_missing_sender")
            continue
        body = _message_body(m)
        msg_type = m.get("type", "text")
        wa_id = m.get("id")

        # Serialize first-contact creation for one tenant/phone identity. The
        # matching unique index remains the final invariant, while this lock
        # avoids turning concurrent first messages into a transaction error.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"{tenant_id}:{from_number}"},
        )

        if wa_id:
            existing_message = (
                await session.execute(
                    select(Message).where(
                        Message.tenant_id == tenant_id,
                        Message.direction == MessageDirection.INBOUND,
                        Message.wa_message_id == wa_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_message is not None:
                existing_job = (
                    await session.execute(
                        select(AgentRuntimeJob).where(
                            AgentRuntimeJob.tenant_id == tenant_id,
                            AgentRuntimeJob.inbound_message_id == existing_message.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_job is not None and existing_job.status in {
                    AgentRuntimeJobStatus.PENDING.value,
                    AgentRuntimeJobStatus.PROCESSING.value,
                }:
                    runtime_job_ids.append(existing_job.id)
                continue

        contact_stmt = select(LeadContact).where(
            LeadContact.tenant_id == tenant_id,
            LeadContact.type == ContactType.PHONE,
            LeadContact.normalized_value == from_number,
        )
        contact = (await session.execute(contact_stmt)).scalar_one_or_none()
        if contact is None:
            now = datetime.now(UTC)
            digits = from_number.lstrip("+")
            lead = Lead(
                tenant_id=tenant_id,
                sector_id=None,
                campaign_id=None,
                company_name=f"WhatsApp müşteri {digits[-4:]}",
                normalized_name=f"whatsapp-{digits}",
                website=None,
                domain=None,
                country="TR" if from_number.startswith("+90") else None,
                city=None,
                address=None,
                source="whatsapp_inbound",
                source_url=None,
                status=LeadStatus.REPLIED,
                fit_score=0,
                priority=LeadPriority.LOW,
                discovered_at=now,
                contacted_at=None,
            )
            session.add(lead)
            await session.flush()
            contact = LeadContact(
                tenant_id=tenant_id,
                lead_id=lead.id,
                type=ContactType.PHONE,
                raw_value=from_number,
                normalized_value=from_number,
                country_code="TR" if from_number.startswith("+90") else None,
                is_valid=True,
                is_whatsapp=True,
                consent_status=ConsentStatus.UNKNOWN,
            )
            session.add(contact)
            await session.flush()
            logger.info("webhook_inbound_contact_created", contact_id=str(contact.id))
        else:
            existing_lead = await session.get(Lead, contact.lead_id)
            if existing_lead is not None and existing_lead.status not in {
                LeadStatus.WON,
                LeadStatus.LOST,
                LeadStatus.BLACKLISTED,
            }:
                existing_lead.status = LeadStatus.REPLIED

        # Get-or-create conversation
        conv_stmt = select(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.contact_id == contact.id
        )
        conv = (await session.execute(conv_stmt)).scalar_one_or_none()
        if conv is None:
            conv = Conversation(
                tenant_id=tenant_id,
                lead_id=contact.lead_id,
                contact_id=contact.id,
            )
            session.add(conv)
            await session.flush()

        if (
            not body
            and msg_type in {"image", "document"}
            and get_settings().selection_rollout != "disabled"
        ):
            from src.modules.selection.models import SelectionRequest

            active_selection = await session.scalar(
                select(SelectionRequest.id)
                .where(
                    SelectionRequest.conversation_id == conv.id, SelectionRequest.status == "draft"
                )
                .limit(1)
            )
            if active_selection is not None:
                body = "[attachment]"

        received_at = datetime.now(UTC)
        conv.last_message_at = received_at
        conv.unread_count = (conv.unread_count or 0) + 1

        inbound = Message(
            tenant_id=tenant_id,
            conversation_id=conv.id,
            direction=MessageDirection.INBOUND,
            message_type=(
                MessageType(msg_type)
                if msg_type in MessageType._value2member_map_
                else MessageType.TEXT
            ),
            body=body,
            wa_message_id=wa_id,
            raw=m,
            created_at=received_at,
        )
        session.add(inbound)
        await session.flush()

        # Opt-out auto-detection
        if body and _OPT_OUT_RE.match(body):
            contact.consent_status = ConsentStatus.OPT_OUT
            exists = (
                await session.execute(
                    select(OptOut).where(
                        OptOut.tenant_id == tenant_id,
                        OptOut.phone_e164 == contact.normalized_value,
                    )
                )
            ).scalar_one_or_none()
            if not exists:
                session.add(
                    OptOut(
                        tenant_id=tenant_id,
                        phone_e164=contact.normalized_value,
                        source=OptOutSource.USER_REPLY,
                        reason=f"keyword: {body[:60]}",
                    )
                )
                logger.info("auto_opt_out", phone=contact.normalized_value)
            from src.modules.selection.models import SelectionRequest

            draft = await session.scalar(
                select(SelectionRequest)
                .where(
                    SelectionRequest.conversation_id == conv.id,
                    SelectionRequest.status == "draft",
                )
                .with_for_update(skip_locked=True)
            )
            if draft is not None:
                draft.status = "cancelled"
                draft.revision += 1
            cancelled_job_ids = await _cancel_runtime_jobs_for_opt_out(
                session,
                tenant_id,
                conv.id,
                received_at,
            )
            if cancelled_job_ids:
                runtime_job_ids = [
                    job_id for job_id in runtime_job_ids if job_id not in cancelled_job_ids
                ]
            continue

        if body and contact.consent_status != ConsentStatus.OPT_OUT:
            blocking_jobs = list(
                (
                    await session.execute(
                        select(AgentRuntimeJob)
                        .where(
                            AgentRuntimeJob.tenant_id == tenant_id,
                            AgentRuntimeJob.conversation_id == conv.id,
                            AgentRuntimeJob.status.in_(
                                [
                                    AgentRuntimeJobStatus.HANDOFF.value,
                                    AgentRuntimeJobStatus.FAILED.value,
                                ]
                            ),
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if blocking_jobs:
                logger.info("agent_runtime_paused_for_human", conversation_id=str(conv.id))
                continue
            runtime_job = AgentRuntimeJob(
                tenant_id=tenant_id,
                inbound_message_id=inbound.id,
                conversation_id=conv.id,
                status=AgentRuntimeJobStatus.PENDING.value,
                audit={"wa_message_type": msg_type,
                       "webhook_received_at": (webhook_received_at or received_at).isoformat()},
                created_at=received_at,
            )
            session.add(runtime_job)
            await session.flush()
            runtime_job_ids.append(runtime_job.id)

    return runtime_job_ids


def _normalize_wa_number(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    digits = re.sub(r"\D", "", raw)
    return f"+{digits}" if digits else None


def _message_body(message: dict[str, Any]) -> str | None:
    """Extract text from Meta text, quick-reply, and list/button payloads."""

    msg_type = message.get("type")
    body: object = None
    if msg_type == "text":
        body = (message.get("text") or {}).get("body")
    elif msg_type == "button":
        button = message.get("button") or {}
        body = button.get("text") or button.get("payload")
    elif msg_type == "interactive":
        interactive = message.get("interactive") or {}
        nfm_reply = interactive.get("nfm_reply") or {}
        response_json = nfm_reply.get("response_json")
        if isinstance(response_json, str):
            try:
                response_data = json.loads(response_json)
            except json.JSONDecodeError:
                response_data = None
            if isinstance(response_data, dict):
                # Full signed form data stays in Message.raw. Only this opaque
                # marker becomes customer text or model-visible history.
                body = "[flow_response]"
        else:
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            title = reply.get("title")
            reply_id = reply.get("id")
            body = f"{title} [{reply_id}]" if title and reply_id else title or reply_id
    elif msg_type in {"image", "document", "video"}:
        body = (message.get(msg_type) or {}).get("caption")
    if not isinstance(body, str):
        return None
    clean = body.strip()
    return clean[:4000] if clean else None
