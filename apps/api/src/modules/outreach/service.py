"""Outreach service: template & sender CRUD, job enqueue, conversation ops."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.errors import ConflictError, NotFoundError
from src.modules.discovery.models import LeadContact

from .models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    MessageTemplate,
    MessageType,
    OutreachJob,
    SenderProfile,
    TemplateStatus,
)
from .schemas import (
    OutreachEnqueueIn,
    SenderIn,
    SenderPatchIn,
    TemplateIn,
    TemplatePatchIn,
)


class TemplateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, tenant_id: UUID) -> list[MessageTemplate]:
        stmt = (
            select(MessageTemplate)
            .where(MessageTemplate.tenant_id == tenant_id)
            .order_by(MessageTemplate.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, tenant_id: UUID, data: TemplateIn) -> MessageTemplate:
        obj = MessageTemplate(
            tenant_id=tenant_id,
            name=data.name,
            language=data.language,
            category=data.category,
            body=data.body,
            variables=data.variables,
            sector_id=data.sector_id,
            status=TemplateStatus.DRAFT,
        )
        self.session.add(obj)
        try:
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise ConflictError("template already exists") from e
        return obj

    async def get(self, tenant_id: UUID, template_id: UUID) -> MessageTemplate:
        obj = await self.session.get(MessageTemplate, template_id)
        if obj is None or obj.tenant_id != tenant_id:
            raise NotFoundError("MessageTemplate", str(template_id))
        return obj

    async def patch(
        self, tenant_id: UUID, template_id: UUID, data: TemplatePatchIn
    ) -> MessageTemplate:
        obj = await self.get(tenant_id, template_id)
        for f, v in data.model_dump(exclude_none=True).items():
            setattr(obj, f, v)
        await self.session.commit()
        return obj


class SenderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, tenant_id: UUID) -> list[SenderProfile]:
        stmt = (
            select(SenderProfile)
            .where(SenderProfile.tenant_id == tenant_id)
            .order_by(SenderProfile.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, tenant_id: UUID, data: SenderIn) -> SenderProfile:
        obj = SenderProfile(
            tenant_id=tenant_id,
            display_name=data.display_name,
            phone_number_id=data.phone_number_id,
            business_account_id=data.business_account_id,
            tier=data.tier,
            daily_cap=data.daily_cap,
        )
        self.session.add(obj)
        try:
            await self.session.commit()
        except Exception as e:
            await self.session.rollback()
            raise ConflictError("sender already registered") from e
        return obj

    async def get(self, tenant_id: UUID, sender_id: UUID) -> SenderProfile:
        obj = await self.session.get(SenderProfile, sender_id)
        if obj is None or obj.tenant_id != tenant_id:
            raise NotFoundError("SenderProfile", str(sender_id))
        return obj

    async def patch(self, tenant_id: UUID, sender_id: UUID, data: SenderPatchIn) -> SenderProfile:
        obj = await self.get(tenant_id, sender_id)
        for f, v in data.model_dump(exclude_none=True).items():
            setattr(obj, f, v)
        await self.session.commit()
        return obj


class OutreachService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(self, tenant_id: UUID, data: OutreachEnqueueIn) -> list[OutreachJob]:
        raise ConflictError(
            "Legacy outreach enqueue is retired. Prepare and send via the authenticated admin-chat outbox."
        )

    async def list_jobs(self, tenant_id: UUID, limit: int = 100) -> list[OutreachJob]:
        stmt = (
            select(OutreachJob)
            .where(OutreachJob.tenant_id == tenant_id)
            .order_by(OutreachJob.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_conversations(self, tenant_id: UUID) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.tenant_id == tenant_id)
            .order_by(Conversation.last_message_at.desc().nulls_last())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_or_create_by_contact(self, tenant_id: UUID, contact: LeadContact) -> Conversation:
        stmt = select(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.contact_id == contact.id
        )
        conv = (await self.session.execute(stmt)).scalar_one_or_none()
        if conv is None:
            conv = Conversation(
                tenant_id=tenant_id,
                lead_id=contact.lead_id,
                contact_id=contact.id,
                status=ConversationStatus.OPEN,
            )
            self.session.add(conv)
            await self.session.commit()
        return conv

    async def messages(self, tenant_id: UUID, conv_id: UUID) -> list[Message]:
        conv = await self.session.get(Conversation, conv_id)
        if conv is None or conv.tenant_id != tenant_id:
            raise NotFoundError("Conversation", str(conv_id))
        stmt = (
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def send_free_form(self, tenant_id: UUID, conv_id: UUID, body: str) -> Message:
        """Send a session (24h window) message — used by agents in the inbox."""
        conv = await self.session.get(Conversation, conv_id)
        if conv is None or conv.tenant_id != tenant_id:
            raise NotFoundError("Conversation", str(conv_id))
        contact = await self.session.get(LeadContact, conv.contact_id)
        if contact is None:
            raise NotFoundError("LeadContact", str(conv.contact_id))

        from datetime import timedelta

        from sqlalchemy import text

        from src.core.db import session_scope
        from src.core.errors import ConflictError
        from src.integrations.whatsapp import WhatsAppClient
        from src.modules.compliance.models import OptOut

        from .channel import resolve_channel

        async with session_scope(tenant_id) as guard:
            await guard.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                                {"identity": f"{tenant_id}:{contact.normalized_value}"})
            await resolve_channel(guard, tenant_id)
            if (await guard.execute(select(OptOut.id).where(OptOut.tenant_id == tenant_id,
                    OptOut.phone_e164 == contact.normalized_value))).first():
                raise ConflictError("Customer opted out")
            ambiguous = (await guard.execute(select(Message.id).where(Message.tenant_id == tenant_id,
                Message.conversation_id == conv_id,
                Message.raw["manual_send_state"].astext.in_(["sending", "ambiguous"])))).first()
            if ambiguous:
                raise ConflictError("Previous manual delivery is ambiguous; review it before sending again")
            last_inbound = await guard.scalar(select(Message.created_at).where(
                Message.tenant_id == tenant_id, Message.conversation_id == conv_id,
                Message.direction == MessageDirection.INBOUND).order_by(Message.created_at.desc()).limit(1))
            if last_inbound is None or last_inbound < datetime.now(UTC) - timedelta(hours=24):
                raise ConflictError("The WhatsApp 24-hour response window has closed")
            msg = Message(tenant_id=tenant_id, conversation_id=conv_id,
                          direction=MessageDirection.OUTBOUND, message_type=MessageType.TEXT,
                          body=body, raw={"manual_send_state": "sending"})
            self.session.add(msg)
            await self.session.commit()
            try:
                response = await WhatsAppClient().send_text_once(contact.normalized_value, body)
                wa_id = response.get("messages", [{}])[0].get("id")
                if not wa_id:
                    raise RuntimeError("Missing delivery id")
            except Exception as exc:
                msg.raw = {"manual_send_state": "ambiguous"}
                await self.session.commit()
                raise ConflictError("Manual delivery could not be confirmed; do not resend before review") from exc
            msg.wa_message_id = wa_id
            msg.raw = {"manual_send_state": "sent", "transport": response}
            conv.last_message_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(msg)
            return msg
