"""Reports service — funnel, sender health, sales performance."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.discovery.models import Lead, LeadStatus
from src.modules.outreach.models import SenderProfile


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def funnel(self, tenant_id: UUID) -> dict[str, Any]:
        stmt = (
            select(Lead.status, func.count())
            .where(Lead.tenant_id == tenant_id)
            .group_by(Lead.status)
        )
        # Comprehension, not dict(rows): SQLAlchemy Row isn't a tuple[K, V] as
        # far as mypy's dict() overloads are concerned, even with this
        # explicit annotation.
        counts: dict[LeadStatus, int] = {  # noqa: C416
            status: count for status, count in (await self.session.execute(stmt)).all()
        }
        return {
            "discovered": counts.get(LeadStatus.DISCOVERED, 0),
            "enriched": counts.get(LeadStatus.ENRICHED, 0),
            "qualified": counts.get(LeadStatus.QUALIFIED, 0),
            "contacted": counts.get(LeadStatus.CONTACTED, 0),
            "replied": counts.get(LeadStatus.REPLIED, 0),
            "interested": counts.get(LeadStatus.INTERESTED, 0),
            "won": counts.get(LeadStatus.WON, 0),
            "lost": counts.get(LeadStatus.LOST, 0),
        }

    async def sender_health(self, tenant_id: UUID) -> list[dict[str, Any]]:
        stmt = select(SenderProfile).where(SenderProfile.tenant_id == tenant_id)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return [
            {
                "sender_id": str(s.id),
                "display_name": s.display_name,
                "tier": s.tier.value,
                "health_status": s.health_status.value,
                "daily_sent": s.daily_sent,
                "daily_cap": s.daily_cap,
            }
            for s in rows
        ]

    async def sales_performance(self, tenant_id: UUID) -> dict[str, Any]:
        stmt = select(Lead.status, func.count()).where(
            Lead.tenant_id == tenant_id
        ).group_by(Lead.status)
        # Comprehension, not dict(rows): SQLAlchemy Row isn't a tuple[K, V] as
        # far as mypy's dict() overloads are concerned, even with this
        # explicit annotation.
        counts: dict[LeadStatus, int] = {  # noqa: C416
            status: count for status, count in (await self.session.execute(stmt)).all()
        }
        total = sum(counts.values()) or 1
        contacted = counts.get(LeadStatus.CONTACTED, 0)
        replied = counts.get(LeadStatus.REPLIED, 0) + counts.get(
            LeadStatus.INTERESTED, 0
        ) + counts.get(LeadStatus.WON, 0)
        won = counts.get(LeadStatus.WON, 0)
        return {
            "total_leads": total,
            "contacted": contacted,
            "reply_rate": round((replied / contacted) if contacted else 0, 3),
            "conversion_rate": round((won / total), 3),
        }
