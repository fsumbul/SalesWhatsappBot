"""Resolve a company's explicit sender binding before using installation credentials."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.errors import ConflictError
from src.modules.agents.models import Agent
from src.modules.auth.models import Tenant, TenantStatus

from .models import SenderProfile


async def resolve_channel(session: AsyncSession, tenant_id: UUID) -> SenderProfile:
    settings = get_settings()
    tenant = await session.get(Tenant, tenant_id)
    sender = (
        await session.execute(
            select(SenderProfile)
            .join(Agent, Agent.id == SenderProfile.agent_id)
            .where(
                SenderProfile.tenant_id == tenant_id,
                SenderProfile.is_active.is_(True),
                Agent.tenant_id == tenant_id,
                Agent.is_active.is_(True),
                SenderProfile.phone_number_id == settings.whatsapp_phone_number_id,
                SenderProfile.business_account_id == settings.whatsapp_business_account_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        tenant is None
        or tenant.status != TenantStatus.ACTIVE
        or sender is None
        or not settings.whatsapp_business_account_id
        or tenant.wa_business_account_id != sender.business_account_id
    ):
        raise ConflictError("This company has no active WhatsApp connection")
    return sender
