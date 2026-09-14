"""Durable outbox drain, also invoked by the existing runtime recovery task on Windows."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update

from src.core.db import session_scope
from src.core.rbac import Role, role_at_least
from src.integrations.whatsapp import WhatsAppClient
from src.modules.admin_chat.outbound import (
    eligibility,
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
        ids = list(
            (
                await db.scalars(
                    select(OutboundRecipient.id)
                    .where(
                        OutboundRecipient.tenant_id == tenant_id,
                        OutboundRecipient.status == "queued",
                    )
                    .order_by(OutboundRecipient.created_at)
                    .limit(10)
                )
            ).all()
        )
        await db.commit()
    for recipient_id in ids:
        await send_one(tenant_id, recipient_id)


async def send_one(tenant_id: UUID, recipient_id: UUID) -> None:
    # Guard locks live across the separate durable sending commit and the single network POST.
    async with session_scope(tenant_id) as guard, session_scope(tenant_id) as db:
        row = await db.get(OutboundRecipient, recipient_id)
        if row is None or row.status != "queued":
            return
        batch = await guard.scalar(
            select(OutboundBatch)
            .where(OutboundBatch.id == row.batch_id, OutboundBatch.tenant_id == tenant_id)
            .with_for_update()
        )
        if batch is None:
            return
        await guard.execute(
            select(SenderProfile)
            .where(SenderProfile.id == batch.sender_id, SenderProfile.tenant_id == tenant_id)
            .with_for_update()
        )
        await guard.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"{tenant_id}:{row.phone}"},
        )
        await db.refresh(row)
        if row.status != "queued":
            return
        try:
            sender = await resolve_channel(guard, tenant_id)
            user = await guard.get(User, batch.user_id)
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
            # Exclude this reserved row from duplicate detection, retain all other checks.
            reason = await eligibility(
                guard, tenant_id, row.phone, batch.consent_evidence, exclude_id=row.id
            )
            if reason:
                raise ValueError(reason)
            if batch.template not in await meta_templates(sender):
                raise ValueError("Şablon değişmiş veya Meta onayı kaldırılmış.")
        except Exception as exc:
            row.status = "blocked"
            row.reason = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Bağlantı veya Meta onayı doğrulanamadı."
            )
            await db.commit()
            return
        row.status = "sending"
        row.attempted_at = datetime.now(UTC)
        await db.commit()  # MUST precede the POST. No automatic retry past this point.
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
        except Exception:
            row.status = "ambiguous"
            row.reason = (
                "Meta sonucu doğrulanamadı. Otomatik tekrar kapalı; teslimatı kontrol edin."
            )
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
