"""Outreach service: template & sender CRUD, job enqueue, conversation ops."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.errors import ConflictError, NotFoundError, ValidationError

from ..discovery.models import ContactType, Lead, LeadContact
from .models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    MessageTemplate,
    MessageType,
    OutreachJob,
    OutreachJobStatus,
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
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
            await self.session.rollback()
            raise ConflictError("sender already registered") from e
        return obj

    async def get(self, tenant_id: UUID, sender_id: UUID) -> SenderProfile:
        obj = await self.session.get(SenderProfile, sender_id)
        if obj is None or obj.tenant_id != tenant_id:
            raise NotFoundError("SenderProfile", str(sender_id))
        return obj

    async def patch(
        self, tenant_id: UUID, sender_id: UUID, data: SenderPatchIn
    ) -> SenderProfile:
        obj = await self.get(tenant_id, sender_id)
        for f, v in data.model_dump(exclude_none=True).items():
            setattr(obj, f, v)
        await self.session.commit()
        return obj


class OutreachService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(self, tenant_id: UUID, data: OutreachEnqueueIn) -> list[OutreachJob]:
        template = await TemplateService(self.session).get(tenant_id, data.template_id)
        if template.status != TemplateStatus.APPROVED:
            raise ValidationError("template must be APPROVED before use")

        leads_stmt = (
            select(Lead)
            .where(Lead.tenant_id == tenant_id, Lead.id.in_(data.lead_ids))
            .options(selectinload(Lead.contacts))
        )
        leads = list((await self.session.execute(leads_stmt)).scalars().all())
        jobs: list[OutreachJob] = []
        for lead in leads:
            phone_contact = next(
                (c for c in lead.contacts if c.type == ContactType.PHONE and c.is_valid),
                None,
            )
            if phone_contact is None:
                continue
            job = OutreachJob(
                tenant_id=tenant_id,
                campaign_id=data.campaign_id or lead.campaign_id,
                lead_id=lead.id,
                contact_id=phone_contact.id,
                template_id=template.id,
                sender_id=data.sender_id,
                variables=data.variables,
                status=OutreachJobStatus.PENDING,
                scheduled_for=data.scheduled_for,
            )
            self.session.add(job)
            jobs.append(job)
        await self.session.commit()
        return jobs

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

    async def list(self, tenant_id: UUID) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.tenant_id == tenant_id)
            .order_by(Conversation.last_message_at.desc().nulls_last())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_or_create_by_contact(
        self, tenant_id: UUID, contact: LeadContact
    ) -> Conversation:
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

    async def send_free_form(
        self, tenant_id: UUID, conv_id: UUID, body: str
    ) -> Message:
        """Send a session (24h window) message — used by agents in the inbox."""
        conv = await self.session.get(Conversation, conv_id)
        if conv is None or conv.tenant_id != tenant_id:
            raise NotFoundError("Conversation", str(conv_id))
        contact = await self.session.get(LeadContact, conv.contact_id)
        if contact is None:
            raise NotFoundError("LeadContact", str(conv.contact_id))

        # WhatsApp session window: last inbound must be within 24h
        from src.integrations.whatsapp import WhatsAppClient

        wa = WhatsAppClient()
        resp = await wa.send_text(contact.normalized_value, body)
        wa_id = None
        try:
            wa_id = resp.get("messages", [{}])[0].get("id")
        except (KeyError, IndexError):
            pass

        msg = Message(
            tenant_id=tenant_id,
            conversation_id=conv_id,
            direction=MessageDirection.OUTBOUND,
            message_type=MessageType.TEXT,
            body=body,
            wa_message_id=wa_id,
            raw=resp,
        )
        self.session.add(msg)
        conv.last_message_at = datetime.now(UTC)
        await self.session.commit()
        return msg
