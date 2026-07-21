"""Outreach dispatcher — the sender loop.

Fires every minute (see beat schedule). Pulls PENDING / DEFERRED (past due)
outreach jobs, runs them through the compliance gate, picks a healthy sender
that respects daily caps and warmup tier, then dispatches to WhatsApp.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.celery_app import celery_app
from src.core.db import get_sessionmaker, set_tenant_context
from src.integrations.whatsapp import WhatsAppClient
from src.modules.auth.models import Tenant
from src.modules.compliance.models import ComplianceResult
from src.modules.compliance.service import ComplianceService
from src.modules.discovery.models import Lead, LeadContact, LeadStatus
from src.modules.outreach.models import (
    Conversation,
    Message,
    MessageDirection,
    MessageTemplate,
    MessageType,
    OutreachJob,
    OutreachJobStatus,
    SenderHealthStatus,
    SenderProfile,
)

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)


@celery_app.task(name="src.workers.outreach.dispatch_outreach")
def dispatch_outreach() -> dict[str, Any]:
    return run_async(_dispatch())


async def _dispatch() -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as session:
        tenants = list((await session.execute(select(Tenant))).scalars().all())

    total_sent = 0
    total_blocked = 0
    total_deferred = 0
    for tenant in tenants:
        s, b, d = await _dispatch_for_tenant(tenant.id)
        total_sent += s
        total_blocked += b
        total_deferred += d
    return {"sent": total_sent, "blocked": total_blocked, "deferred": total_deferred}


async def _dispatch_for_tenant(tenant_id: UUID) -> tuple[int, int, int]:
    sm = get_sessionmaker()
    async with sm() as session:
        await set_tenant_context(session, tenant_id)
        now = datetime.now(UTC)
        stmt = (
            select(OutreachJob)
            .where(
                OutreachJob.tenant_id == tenant_id,
                OutreachJob.status.in_([OutreachJobStatus.PENDING, OutreachJobStatus.DEFERRED]),
            )
            .limit(50)
        )
        jobs = list((await session.execute(stmt)).scalars().all())
        if not jobs:
            return (0, 0, 0)

        sent = blocked = deferred = 0
        compliance = ComplianceService(session)

        for job in jobs:
            if job.scheduled_for and job.scheduled_for > now:
                continue

            contact = await session.get(LeadContact, job.contact_id)
            if contact is None:
                job.status = OutreachJobStatus.FAILED
                job.error = "contact_missing"
                continue
            lead = await session.get(Lead, job.lead_id)
            country = lead.country if lead else None

            decision = await compliance.check_contact(tenant_id, contact, country)
            if decision.decision == ComplianceResult.BLOCK:
                job.status = OutreachJobStatus.BLOCKED
                job.error = decision.reason
                if lead:
                    lead.status = LeadStatus.BLOCKED_BY_COMPLIANCE
                blocked += 1
                continue
            if decision.decision == ComplianceResult.DEFER:
                job.status = OutreachJobStatus.DEFERRED
                job.scheduled_for = decision.next_allowed_at
                deferred += 1
                continue

            sender = await _pick_sender(session, tenant_id, job.sender_id)
            if sender is None:
                job.status = OutreachJobStatus.DEFERRED
                job.scheduled_for = _tomorrow_utc()
                deferred += 1
                continue

            template = await session.get(MessageTemplate, job.template_id)
            if template is None:
                job.status = OutreachJobStatus.FAILED
                job.error = "template_missing"
                continue

            wa = WhatsAppClient(
                phone_number_id=sender.phone_number_id,
            )
            try:
                job.status = OutreachJobStatus.SENDING
                job.attempts += 1
                components = _build_template_components(template, job.variables)
                resp = await wa.send_template(
                    to=contact.normalized_value,
                    template_name=template.wa_template_id or template.name,
                    language=template.language,
                    components=components,
                )
                wa_id = None
                with contextlib.suppress(KeyError, IndexError, TypeError):
                    wa_id = resp.get("messages", [{}])[0].get("id")
                job.wa_message_id = wa_id
                job.sent_at = datetime.now(UTC)
                job.status = OutreachJobStatus.SENT
                sender.daily_sent += 1
                if lead:
                    lead.status = LeadStatus.CONTACTED
                    lead.contacted_at = datetime.now(UTC)

                # Ensure conversation & log outbound message
                conv_stmt = select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.contact_id == contact.id,
                )
                conv = (await session.execute(conv_stmt)).scalar_one_or_none()
                if conv is None:
                    conv = Conversation(
                        tenant_id=tenant_id, lead_id=job.lead_id, contact_id=contact.id
                    )
                    session.add(conv)
                    await session.flush()
                conv.last_message_at = datetime.now(UTC)
                session.add(
                    Message(
                        tenant_id=tenant_id,
                        conversation_id=conv.id,
                        direction=MessageDirection.OUTBOUND,
                        message_type=MessageType.TEMPLATE,
                        body=_render(template.body, job.variables),
                        wa_message_id=wa_id,
                        outreach_job_id=job.id,
                        raw=resp,
                    )
                )
                sent += 1
            except Exception as e:
                job.status = OutreachJobStatus.FAILED
                job.error = str(e)[:1000]
                logger.warning("outreach_send_failed", job_id=str(job.id), error=str(e))

        await session.commit()
        return (sent, blocked, deferred)


async def _pick_sender(
    session: AsyncSession, tenant_id: UUID, preferred: UUID | None
) -> SenderProfile | None:
    if preferred is not None:
        sender = await session.get(SenderProfile, preferred)
        if (
            sender
            and sender.tenant_id == tenant_id
            and sender.is_active
            and sender.health_status != SenderHealthStatus.BANNED
            and sender.daily_sent < sender.daily_cap
        ):
            return sender

    stmt = (
        select(SenderProfile)
        .where(
            SenderProfile.tenant_id == tenant_id,
            SenderProfile.is_active.is_(True),
            SenderProfile.health_status != SenderHealthStatus.BANNED,
            SenderProfile.daily_sent < SenderProfile.daily_cap,
        )
        .order_by(SenderProfile.daily_sent.asc())
    )
    return (await session.execute(stmt)).scalars().first()


def _build_template_components(
    template: MessageTemplate, variables: dict[str, Any]
) -> list[dict[str, Any]]:
    if not template.variables:
        return []
    return [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": str(variables.get(v, ""))}
                for v in template.variables
            ],
        }
    ]


def _render(body: str, variables: dict[str, Any]) -> str:
    out = body
    for k, v in (variables or {}).items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def _tomorrow_utc() -> datetime:
    n = datetime.now(UTC)
    return n.replace(hour=9, minute=0, second=0, microsecond=0)
