"""Bind existing installation credentials to an explicit tenant/agent. Never prints secrets."""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import get_settings
from src.core.db import session_scope, set_tenant_context
from src.modules.agents.models import Agent
from src.modules.auth.models import Tenant
from src.modules.compliance.models import AuditLog
from src.modules.outreach.models import SenderProfile


async def bind(tenant_slug: str, agent_slug: str, dry_run: bool) -> None:
    settings = get_settings()
    async with session_scope() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == tenant_slug).with_for_update())
        ).scalar_one()
        if (
            not settings.whatsapp_phone_number_id
            or not settings.whatsapp_business_account_id
            or tenant.wa_business_account_id != settings.whatsapp_business_account_id
        ):
            raise RuntimeError("Existing installation and tenant WABA must match")
        await set_tenant_context(db, tenant.id)
        agent = (
            await db.execute(
                select(Agent).where(Agent.tenant_id == tenant.id, Agent.slug == agent_slug)
            )
        ).scalar_one()
        sender = (
            await db.execute(
                select(SenderProfile).where(
                    SenderProfile.tenant_id == tenant.id,
                    SenderProfile.phone_number_id == settings.whatsapp_phone_number_id,
                )
            )
        ).scalar_one_or_none()
        if sender is None:
            sender = SenderProfile(
                tenant_id=tenant.id,
                display_name=tenant.name,
                phone_number_id=settings.whatsapp_phone_number_id,
                business_account_id=settings.whatsapp_business_account_id,
            )
            db.add(sender)
        if sender.business_account_id != settings.whatsapp_business_account_id:
            raise RuntimeError("Sender WABA does not match installation")
        sender.agent_id = agent.id
        sender.is_active = True
        db.add(
            AuditLog(
                tenant_id=tenant.id,
                action="bind_runtime_channel",
                entity="agent",
                entity_id=str(agent.id),
            )
        )
        await db.flush()
        if dry_run:
            await db.rollback()
        else:
            await db.commit()
        print("Channel binding validated" if dry_run else "Channel binding saved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--agent-slug", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(bind(args.tenant_slug, args.agent_slug, args.dry_run))
