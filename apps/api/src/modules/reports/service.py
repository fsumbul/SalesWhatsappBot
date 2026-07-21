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

    async def source_precision(self, tenant_id: UUID) -> dict[str, Any]:
        """Per-source lead-quality snapshot (Phase D "quality dashboard" scaffold).

        CAVEAT — read before trusting this number: `qualification_rate_of_settled`
        is not a true precision-since-discovery metric. Enrichment hard-deletes
        `Lead` rows scoring below the fit threshold (see
        `workers/enrichment.py::_enrich_campaign`), and `LeadSource.lead_id`
        cascades on delete — so "how many did we ever try from this source"
        does not survive in the database. This method can only report what's
        currently visible, split into settled-qualified vs. settled-discarded
        vs. still-pending-enrichment. For any source whose enrichment run has
        already completed, discarded rows are typically already gone, so the
        rate trends toward 100% regardless of true source quality — that's a
        real limitation, not a data bug.

        A trustworthy metric needs one of:
          - stop hard-deleting sub-threshold leads (soft-delete via status
            only, e.g. keep DISCARDED rows instead of purging them), or
          - a small append-only per-source counter incremented at discovery
            time and at enrichment time, independent of the leads table's
            row lifecycle.
        Neither is implemented — this method is scaffolding for whichever an
        operator decides, not a finished dashboard metric.
        """
        stmt = (
            select(Lead.source, Lead.status, func.count())
            .where(Lead.tenant_id == tenant_id)
            .group_by(Lead.source, Lead.status)
        )
        rows = (await self.session.execute(stmt)).all()

        pending_statuses = {LeadStatus.DISCOVERED, LeadStatus.ENRICHING, LeadStatus.ENRICHED}
        per_source: dict[str, dict[str, int]] = {}
        for source, status, count in rows:
            bucket = per_source.setdefault(
                source, {"qualified_or_later": 0, "pending": 0, "discarded": 0}
            )
            if status == LeadStatus.DISCARDED:
                bucket["discarded"] += count
            elif status in pending_statuses:
                bucket["pending"] += count
            else:
                bucket["qualified_or_later"] += count

        sources = []
        for source, b in sorted(per_source.items()):
            settled = b["qualified_or_later"] + b["discarded"]
            rate = round(b["qualified_or_later"] / settled, 3) if settled else None
            sources.append(
                {
                    "source": source,
                    "qualified_or_later": b["qualified_or_later"],
                    "pending": b["pending"],
                    "discarded_currently_visible": b["discarded"],
                    "qualification_rate_of_settled": rate,
                }
            )

        return {
            "sources": sources,
            "caveat": (
                "qualification_rate_of_settled only reflects leads still present "
                "in the database right now. Sub-threshold leads are hard-deleted "
                "during enrichment, so this rate trends toward 100% over time and "
                "understates true source quality. See ReportService.source_precision "
                "docstring for what would need to change to fix this."
            ),
        }
