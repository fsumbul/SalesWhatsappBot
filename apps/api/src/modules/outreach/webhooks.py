"""WhatsApp webhook receiver — one endpoint per tenant slug."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.db import get_sessionmaker, set_tenant_context
from src.integrations.whatsapp import WhatsAppClient
from src.modules.auth.models import Tenant
from src.modules.compliance.models import OptOut, OptOutSource
from src.modules.discovery.models import ContactType, LeadContact
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
    r"^\s*(stop|dur|i̇stemi?yorum|istemiyorum|unsubscribe|stopp|الإلغاء|стоп)\s*$",
    re.IGNORECASE,
)


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
    raw = await request.body()
    wa = WhatsAppClient()
    if get_settings().whatsapp_app_secret and not wa.verify_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="invalid signature")

    payload = await request.json()
    sm = get_sessionmaker()
    async with sm() as session:
        # Locate tenant
        t_stmt = select(Tenant).where(Tenant.slug == tenant_slug)
        tenant = (await session.execute(t_stmt)).scalar_one_or_none()
        if tenant is None:
            logger.warning("webhook_unknown_tenant", slug=tenant_slug)
            return {"ok": False}
        await set_tenant_context(session, tenant.id)

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                await _handle_statuses(session, tenant.id, value.get("statuses", []))
                await _handle_messages(session, tenant.id, value.get("messages", []))
        await session.commit()
    return {"ok": True}


async def _handle_statuses(
    session: AsyncSession, tenant_id: UUID, statuses: list[dict[str, Any]]
) -> None:
    for st in statuses:
        wa_id = st.get("id")
        status_name = st.get("status")
        if not wa_id or not status_name:
            continue
        stmt = select(OutreachJob).where(
            OutreachJob.tenant_id == tenant_id, OutreachJob.wa_message_id == wa_id
        )
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is None:
            continue
        now = datetime.now(UTC)
        if status_name == "sent":
            job.status = OutreachJobStatus.SENT
            job.sent_at = job.sent_at or now
        elif status_name == "delivered":
            job.status = OutreachJobStatus.DELIVERED
            job.delivered_at = now
        elif status_name == "read":
            job.status = OutreachJobStatus.READ
            job.read_at = now
        elif status_name == "failed":
            job.status = OutreachJobStatus.FAILED
            job.error = str(st.get("errors"))[:1000]


async def _handle_messages(
    session: AsyncSession, tenant_id: UUID, messages: list[dict[str, Any]]
) -> None:
    for m in messages:
        from_number = "+" + m.get("from", "") if not m.get("from", "").startswith("+") else m["from"]
        body = (m.get("text") or {}).get("body")
        msg_type = m.get("type", "text")
        wa_id = m.get("id")

        contact_stmt = select(LeadContact).where(
            LeadContact.tenant_id == tenant_id,
            LeadContact.type == ContactType.PHONE,
            LeadContact.normalized_value == from_number,
        )
        contact = (await session.execute(contact_stmt)).scalar_one_or_none()
        if contact is None:
            logger.info("webhook_inbound_unknown_contact", from_=from_number)
            continue

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

        conv.last_message_at = datetime.now(UTC)
        conv.unread_count = (conv.unread_count or 0) + 1

        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conv.id,
                direction=MessageDirection.INBOUND,
                message_type=MessageType(msg_type) if msg_type in MessageType._value2member_map_ else MessageType.TEXT,
                body=body,
                wa_message_id=wa_id,
                raw=m,
            )
        )

        # Opt-out auto-detection
        if body and _OPT_OUT_RE.match(body):
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
