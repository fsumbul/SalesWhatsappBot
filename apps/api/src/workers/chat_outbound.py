"""Durable outbox drain, also invoked by the existing runtime recovery task on Windows."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, text, update

from src.core.db import get_engine, session_scope
from src.core.errors import ConflictError
from src.core.rbac import Role, role_at_least
from src.integrations.whatsapp import WhatsAppClient
from src.modules.admin_chat.campaign_imports import campaign_imports_enabled_for
from src.modules.admin_chat.outbound import (
    capacity,
    eligibility,
    is_campaign_import_batch,
    meta_templates,
    rendered,
    template_components,
)
from src.modules.admin_chat.outbound_models import OutboundBatch, OutboundRecipient
from src.modules.auth.models import User
from src.modules.discovery.models import ContactType, Lead, LeadContact
from src.modules.outreach.channel import resolve_channel
from src.modules.outreach.models import (
    Conversation,
    Message,
    MessageDirection,
    MessageType,
    SenderProfile,
)

DISPATCH_RECIPIENT_LIMIT = 100
DISPATCH_CONCURRENCY = 4
# Refresh the provider catalog before each small fan-out wave, rather than
# paying for one Meta catalog call per recipient. A later wave gets a newer
# snapshot even in a large batch.
TEMPLATE_SNAPSHOT_WAVE_SIZE = 20


def _post_failure_state(exc: Exception) -> tuple[str, str]:
    """Classify only an observed provider rejection as a definitive failure.

    A timeout or any other transport/protocol failure can occur after Meta has
    accepted the request, so it must remain ``ambiguous`` and is never retried.
    An ``HTTPStatusError`` contains a complete response from Meta; no receipt
    was accepted for that request, and we can safely record a terminal failure
    without exposing the provider response body to the manager card.
    """

    if isinstance(exc, httpx.HTTPStatusError):
        return (
            "failed",
            f"Meta isteği HTTP {exc.response.status_code} ile reddetti; otomatik tekrar kapalı.",
        )
    return (
        "ambiguous",
        "Meta sonucu doğrulanamadı. Otomatik tekrar kapalı; teslimatı kontrol edin.",
    )


async def dispatch(tenant_id: UUID) -> None:
    async with session_scope(tenant_id) as db:
        # A process may die after Meta accepted the request. Never return sending to queued.
        await db.execute(
            update(OutboundRecipient)
            .where(
                OutboundRecipient.tenant_id == tenant_id,
                OutboundRecipient.status == "sending",
                OutboundRecipient.attempted_at < datetime.now(UTC) - timedelta(minutes=5),
            )
            .values(
                status="ambiguous",
                reason="İşlem yarıda kaldı; teslimat belirsiz. Otomatik tekrar kapalı.",
            )
        )
        recipients = await db.execute(
            select(OutboundRecipient.id, OutboundRecipient.batch_id)
            .where(
                OutboundRecipient.tenant_id == tenant_id,
                OutboundRecipient.status == "queued",
            )
            .order_by(OutboundRecipient.created_at)
            # Bounded but large enough for the file-import canary to
            # drain a reviewed batch in one normal worker pass. The
            # per-recipient worker keeps the durable pre-POST checks.
            .limit(DISPATCH_RECIPIENT_LIMIT)
        )
        queued_by_batch: dict[UUID, list[UUID]] = {}
        for recipient_id, batch_id in recipients.all():
            queued_by_batch.setdefault(batch_id, []).append(recipient_id)
        await db.commit()
    for batch_id, recipient_ids in queued_by_batch.items():
        await _dispatch_batch(tenant_id, batch_id, recipient_ids)


async def _template_snapshot(tenant_id: UUID, batch_id: UUID) -> list[dict[str, Any]] | None:
    """Read one fresh catalog for a small, bounded recipient fan-out wave."""

    async with session_scope(tenant_id) as db:
        batch = await db.get(OutboundBatch, batch_id)
        if batch is None or batch.status != "queued":
            return []
        try:
            sender = await resolve_channel(db, tenant_id)
        except Exception:
            return None  # Provider/channel availability is retryable; leave rows queued.
        if sender.id != batch.sender_id:
            return []
        try:
            return await meta_templates(sender)
        except Exception:
            return None  # Do not turn a transient catalog outage into 100 blocked rows.


async def _dispatch_batch(tenant_id: UUID, batch_id: UUID, recipient_ids: list[UUID]) -> None:
    """Send one batch with explicit fan-out and a short-lived catalog snapshot."""

    for start in range(0, len(recipient_ids), TEMPLATE_SNAPSHOT_WAVE_SIZE):
        catalog = await _template_snapshot(tenant_id, batch_id)
        if catalog is None:
            return
        catalog_for_wave: list[dict[str, Any]] = catalog
        wave = recipient_ids[start : start + TEMPLATE_SNAPSHOT_WAVE_SIZE]
        semaphore = asyncio.Semaphore(DISPATCH_CONCURRENCY)

        async def send_with_limit(
            recipient_id: UUID,
            *,
            _semaphore: asyncio.Semaphore = semaphore,
            _catalog: list[dict[str, Any]] = catalog_for_wave,
        ) -> None:
            async with _semaphore:
                await send_one(tenant_id, recipient_id, template_snapshot=_catalog)

        results = await asyncio.gather(
            *(send_with_limit(recipient_id) for recipient_id in wave), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):  # pragma: no cover - defensive task boundary
                # Other recipients in the bounded wave remain independent.
                continue


async def send_one(
    tenant_id: UUID,
    recipient_id: UUID,
    *,
    template_snapshot: list[dict[str, Any]] | None = None,
) -> None:
    """Make one at-most-once Meta POST after the final guarded checks.

    Sender/batch locks serialize the short validation-and-SENDING transition,
    then release before the provider call.  A dedicated connection retains the
    same per-phone transaction advisory lock until the POST returns, so an
    opt-out cannot commit between the final eligibility check and Meta.
    """

    async with session_scope(tenant_id) as db:
        row = await db.get(OutboundRecipient, recipient_id)
        if row is None or row.status != "queued":
            return
        batch = await db.scalar(
            select(OutboundBatch)
            .where(OutboundBatch.id == row.batch_id, OutboundBatch.tenant_id == tenant_id)
            .with_for_update()
        )
        if batch is None:
            return
        # Retain the established sender -> phone locking order. It prevents a
        # queue-time sender lock and a send-time phone lock from deadlocking.
        await db.execute(
            select(SenderProfile)
            .where(SenderProfile.id == batch.sender_id, SenderProfile.tenant_id == tenant_id)
            .with_for_update()
        )
        async with get_engine().connect() as phone_lock, phone_lock.begin():
            await phone_lock.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": f"{tenant_id}:{row.phone}"},
            )
            await db.refresh(row)
            if row.status != "queued":
                return
            try:
                sender = await resolve_channel(db, tenant_id)
                user = await db.get(User, batch.user_id)
                if (
                    batch.status != "queued"
                    or sender.id != batch.sender_id
                    or not user
                    or not user.is_active
                    or not role_at_least(user.role, Role.SALES_MANAGER)
                ):
                    raise ValueError("Gönderim yetkisi veya bağlantı artık geçerli değil.")
                if sender.health_status == "banned":
                    raise ValueError("WhatsApp göndericisi kullanılamıyor.")
                if is_campaign_import_batch(batch) and not campaign_imports_enabled_for(
                    tenant_id, user.id
                ):
                    raise ValueError("Dosya kampanyası bu hesap için artık etkin değil.")
                # The queued recipient already occupies one reservation. A
                # stricter current daily cap can make the total exceed its
                # cap after queueing, so stop before the Meta POST.
                current_capacity = await capacity(db, tenant_id, include_meta=False)
                if int(current_capacity.get("local_used", 0)) > sender.daily_cap:
                    raise ValueError("Şirketin güncel gönderim kapasitesi aşıldı.")
                reason = await eligibility(
                    db, tenant_id, row.phone, batch.consent_evidence, exclude_id=row.id
                )
                if reason:
                    raise ValueError(reason)
                # A wave snapshot is fetched immediately before its
                # bounded fan-out. Direct calls retain the old fresh-read
                # behaviour; either path is checked under the phone lock.
                catalog = (
                    template_snapshot
                    if template_snapshot is not None
                    else await meta_templates(sender)
                )
                if batch.template not in catalog:
                    raise ValueError("Şablon değişmiş veya Meta onayı kaldırılmış.")
            except (httpx.HTTPError, TimeoutError):
                # Catalog availability is a preflight dependency, not a
                # recipient-specific compliance decision. Keep the durable
                # row queued so the bounded dispatcher can retry it after a
                # temporary Meta/network outage; no Meta POST occurred.
                return
            except (ValueError, ConflictError) as exc:
                row.status = "blocked"
                row.reason = str(exc.detail) if isinstance(exc, ConflictError) else str(exc)
                await db.commit()
                return
            row.status = "sending"
            row.attempted_at = datetime.now(UTC)
            # MUST precede the POST. The dedicated phone lock remains
            # active even though this commit releases sender/batch locks.
            await db.commit()
            try:
                components = template_components(batch, row.id)
                response = await WhatsAppClient(
                    phone_number_id=sender.phone_number_id
                ).send_template_once(
                    row.phone,
                    batch.template["name"],
                    batch.template["language"],
                    components,
                    callback_data=f"chat-outbound:{row.id}",
                )
                wa_id = response.get("messages", [{}])[0].get("id")
                if not wa_id:
                    raise ValueError("Missing Meta message id")
            except Exception as exc:
                row.status, row.reason = _post_failure_state(exc)
                await db.commit()
                return
            await db.refresh(row)  # A receipt may already have arrived using callback_data.
            if row.status == "sending":
                row.status = "accepted"
            row.wa_message_id = wa_id
            await log_message(db, tenant_id, row, batch)
            await db.commit()


async def log_message(db: Any, tenant_id: UUID, row: Any, batch: Any) -> None:
    contact = await db.scalar(
        select(LeadContact).where(
            LeadContact.tenant_id == tenant_id,
            LeadContact.type == ContactType.PHONE,
            LeadContact.normalized_value == row.phone,
        )
    )
    if contact is None:
        lead = Lead(
            tenant_id=tenant_id,
            company_name=row.phone,
            normalized_name=row.phone,
            source="operator_chat",
            discovered_at=datetime.now(UTC),
        )
        db.add(lead)
        await db.flush()
        contact = LeadContact(
            tenant_id=tenant_id,
            lead_id=lead.id,
            type=ContactType.PHONE,
            raw_value=row.phone,
            normalized_value=row.phone,
        )
        db.add(contact)
        await db.flush()
    conv = await db.scalar(
        select(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.contact_id == contact.id
        )
    )
    if conv is None:
        conv = Conversation(tenant_id=tenant_id, contact_id=contact.id, lead_id=contact.lead_id)
        db.add(conv)
        await db.flush()
    conv.last_message_at = datetime.now(UTC)
    db.add(
        Message(
            tenant_id=tenant_id,
            conversation_id=conv.id,
            direction=MessageDirection.OUTBOUND,
            message_type=MessageType.TEMPLATE,
            body=rendered(batch),
            wa_message_id=row.wa_message_id,
            raw={"chat_outbound_recipient_id": str(row.id), "batch_id": str(batch.id)},
        )
    )
