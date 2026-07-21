"""Discovery service + fuzzy dedup logic."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.errors import NotFoundError
from src.integrations.base import RawLead
from src.modules.sectors.repository import SectorRepo

from .models import Campaign, CampaignStatus, Lead, LeadSource, LeadStatus
from .schemas import CampaignIn, CampaignPatchIn, LeadFilters


def normalize_company_name(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[^a-z0-9\u00c0-\u024f\u0400-\u04ff\u0600-\u06ff ]", " ", name)
    stopwords = {
        "ltd", "limited", "gmbh", "co", "inc", "corp", "sanayi", "ticaret",
        "san", "tic", "as", "a.s", "ltd.", "elevator", "asansor", "asansör",
    }
    tokens = [t for t in name.split() if t and t not in stopwords]
    return " ".join(sorted(tokens))


def extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        has_scheme = url.lower().startswith(("http://", "https://"))
        parsed = urlparse(url if has_scheme else f"http://{url}")
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


class DiscoveryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_campaigns(self, tenant_id: UUID) -> list[Campaign]:
        stmt = (
            select(Campaign)
            .where(Campaign.tenant_id == tenant_id)
            .order_by(Campaign.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create_campaign(self, tenant_id: UUID, data: CampaignIn) -> Campaign:
        sector = await SectorRepo(self.session).get(tenant_id, data.sector_id)
        if sector is None:
            raise NotFoundError("Sector", str(data.sector_id))
        campaign = Campaign(
            tenant_id=tenant_id,
            sector_id=data.sector_id,
            name=data.name,
            filters=data.filters,
            daily_quota=data.daily_quota,
            status=CampaignStatus.DRAFT,
        )
        self.session.add(campaign)
        await self.session.commit()
        return campaign

    async def get_campaign(self, tenant_id: UUID, campaign_id: UUID) -> Campaign:
        stmt = select(Campaign).where(
            Campaign.tenant_id == tenant_id, Campaign.id == campaign_id
        )
        camp = (await self.session.execute(stmt)).scalar_one_or_none()
        if camp is None:
            raise NotFoundError("Campaign", str(campaign_id))
        return camp

    async def patch_campaign(
        self, tenant_id: UUID, campaign_id: UUID, data: CampaignPatchIn
    ) -> Campaign:
        camp = await self.get_campaign(tenant_id, campaign_id)
        for f, v in data.model_dump(exclude_none=True).items():
            setattr(camp, f, v)
        await self.session.commit()
        return camp

    async def set_status(
        self, tenant_id: UUID, campaign_id: UUID, status: CampaignStatus
    ) -> Campaign:
        camp = await self.get_campaign(tenant_id, campaign_id)
        camp.status = status
        if status == CampaignStatus.RUNNING and camp.started_at is None:
            camp.started_at = datetime.now(UTC)
        if status == CampaignStatus.COMPLETED and camp.completed_at is None:
            camp.completed_at = datetime.now(UTC)
        await self.session.commit()
        return camp

    async def list_leads(self, tenant_id: UUID, f: LeadFilters) -> list[Lead]:
        stmt = (
            select(Lead)
            .where(Lead.tenant_id == tenant_id)
            .options(selectinload(Lead.contacts))
            .order_by(Lead.discovered_at.desc())
            .limit(f.limit)
            .offset(f.offset)
        )
        if f.campaign_id:
            stmt = stmt.where(Lead.campaign_id == f.campaign_id)
        if f.sector_id:
            stmt = stmt.where(Lead.sector_id == f.sector_id)
        if f.status:
            stmt = stmt.where(Lead.status == f.status)
        if f.country:
            stmt = stmt.where(Lead.country == f.country)
        if f.min_fit_score is not None:
            stmt = stmt.where(Lead.fit_score >= f.min_fit_score)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_lead(self, tenant_id: UUID, lead_id: UUID) -> Lead:
        stmt = (
            select(Lead)
            .where(Lead.tenant_id == tenant_id, Lead.id == lead_id)
            .options(selectinload(Lead.contacts), selectinload(Lead.enrichment))
        )
        lead = (await self.session.execute(stmt)).scalar_one_or_none()
        if lead is None:
            raise NotFoundError("Lead", str(lead_id))
        return lead

    async def status_summary(self, tenant_id: UUID, campaign_id: UUID) -> dict[str, Any]:
        camp = await self.get_campaign(tenant_id, campaign_id)
        counts_stmt = (
            select(Lead.status, func.count())
            .where(Lead.tenant_id == tenant_id, Lead.campaign_id == campaign_id)
            .group_by(Lead.status)
        )
        # Comprehension, not dict(rows): SQLAlchemy Row isn't a tuple[K, V] as
        # far as mypy's dict() overloads are concerned, even with this
        # explicit annotation.
        counts: dict[LeadStatus, int] = {  # noqa: C416
            status: count for status, count in (await self.session.execute(counts_stmt)).all()
        }
        total = sum(counts.values())
        discovered = counts.get(LeadStatus.DISCOVERED, 0)
        return {
            "campaign_id": campaign_id,
            "status": camp.status,
            "total_leads": total,
            "discovered": discovered,
            # Leads already run through enrichment (left the DISCOVERED queue).
            "scanned": total - discovered,
            "enriched": counts.get(LeadStatus.ENRICHED, 0),
            "qualified": counts.get(LeadStatus.QUALIFIED, 0),
        }

    async def ingest_raw_leads(
        self,
        tenant_id: UUID,
        campaign_id: UUID,
        sector_id: UUID,
        raw_leads: list[RawLead],
    ) -> int:
        """Insert raw leads with fuzzy dedup on (normalized_name + domain)."""
        inserted = 0
        for rl in raw_leads:
            norm = normalize_company_name(rl.company_name)
            domain = extract_domain(rl.website)
            if not norm:
                continue

            dedup_stmt = select(Lead).where(
                Lead.tenant_id == tenant_id, Lead.normalized_name == norm
            )
            if domain:
                dedup_stmt = dedup_stmt.where((Lead.domain == domain) | (Lead.domain.is_(None)))

            existing = (await self.session.execute(dedup_stmt)).scalar_one_or_none()
            if existing is not None:
                self.session.add(
                    LeadSource(
                        tenant_id=tenant_id,
                        lead_id=existing.id,
                        source_type=rl.source,
                        source_url=rl.source_url,
                        raw_data=rl.raw or {},
                    )
                )
                continue

            lead = Lead(
                tenant_id=tenant_id,
                sector_id=sector_id,
                campaign_id=campaign_id,
                company_name=rl.company_name[:255],
                normalized_name=norm[:255],
                website=(rl.website or None) and rl.website[:500],
                domain=domain,
                country=(rl.country or None) and rl.country[:2].upper(),
                city=(rl.city or None) and rl.city[:120],
                address=rl.address,
                source=rl.source,
                source_url=rl.source_url,
                status=LeadStatus.DISCOVERED,
                discovered_at=datetime.now(UTC),
            )
            self.session.add(lead)
            await self.session.flush()
            self.session.add(
                LeadSource(
                    tenant_id=tenant_id,
                    lead_id=lead.id,
                    source_type=rl.source,
                    source_url=rl.source_url,
                    raw_data=rl.raw or {},
                )
            )
            inserted += 1
        await self.session.commit()
        return inserted
