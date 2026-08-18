"""Seed fake leads for a tenant so the dashboard/leads UI has something to show
without real connector API keys (Google Places/SerpAPI/Bing/WhatsApp).

Usage:
    DATABASE_URL=postgresql+asyncpg://leadpulse:leadpulse_dev@localhost:5434/leadpulse \
    python scripts/seed_mock_leads.py <tenant-slug>
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from src import models_registry  # noqa: F401 - populates Base.metadata
from src.core.db import get_sessionmaker, set_tenant_context
from src.modules.auth.models import Tenant
from src.modules.discovery.models import (
    Campaign,
    CampaignStatus,
    ContactType,
    Lead,
    LeadContact,
    LeadPriority,
    LeadStatus,
)
from src.modules.sectors.models import Sector

MOCK_COMPANIES = [
    ("Yildiz Asansor Sanayi", "TR", "Istanbul", "yildizasansor.com.tr", LeadStatus.WON, 92),
    ("Kuzey Asansor Servis", "TR", "Ankara", "kuzeyasansor.com", LeadStatus.INTERESTED, 88),
    ("Marmara Yukselti Sistemleri", "TR", "Bursa", "marmarayukselti.com", LeadStatus.REPLIED, 84),
    ("Ege Lift Teknik", "TR", "Izmir", "egelift.com.tr", LeadStatus.CONTACTED, 81),
    ("Anadolu Asansor Bakim", "TR", "Konya", "anadoluasansor.com", LeadStatus.QUALIFIED, 90),
    ("Karadeniz Yukselti A.S.", "TR", "Trabzon", "karadenizyukselti.com", LeadStatus.QUALIFIED, 86),
    ("Berlin Aufzugstechnik GmbH", "DE", "Berlin", "aufzugstechnik-berlin.de", LeadStatus.ENRICHED, 79),
    ("Munchen Lift Service", "DE", "Munich", "muenchenlift.de", LeadStatus.ENRICHED, 75),
    ("London Elevator Solutions", "GB", "London", "londonelevator.co.uk", LeadStatus.DISCOVERED, 70),
    ("Gulf Vertical Transport LLC", "AE", "Dubai", "gulfvertical.ae", LeadStatus.DISCOVERED, 68),
    ("Riyadh Lift Maintenance Co", "SA", "Riyadh", "riyadhlift.sa", LeadStatus.NOT_INTERESTED, 55),
    ("Moscow Podyom Servis", "RU", "Moscow", "podyomservis.ru", LeadStatus.LOST, 60),
    ("Istanbul Kule Asansor", "TR", "Istanbul", "istanbulkule.com.tr", LeadStatus.DISCARDED, 40),
    ("Hamburg Fahrstuhl Team", "DE", "Hamburg", "fahrstuhl-hamburg.de", LeadStatus.QUALIFIED, 83),
    ("Antalya Asansor Otomasyon", "TR", "Antalya", "antalyaasansor.com", LeadStatus.CONTACTED, 77),
]


async def main(tenant_slug: str) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one_or_none()
        if tenant is None:
            print(f"No tenant with slug '{tenant_slug}'")
            return

        await set_tenant_context(session, tenant.id)

        sector = (
            await session.execute(select(Sector).where(Sector.tenant_id == tenant.id))
        ).scalars().first()
        if sector is None:
            print("Tenant has no sector yet — import a sector preset first.")
            return

        campaign = (
            await session.execute(
                select(Campaign).where(Campaign.tenant_id == tenant.id, Campaign.sector_id == sector.id)
            )
        ).scalars().first()
        if campaign is None:
            campaign = Campaign(
                tenant_id=tenant.id,
                sector_id=sector.id,
                name=f"{sector.name} — mock seed",
                status=CampaignStatus.COMPLETED,
                started_at=datetime.now(UTC) - timedelta(days=1),
                completed_at=datetime.now(UTC),
            )
            session.add(campaign)
            await session.flush()
        else:
            campaign.status = CampaignStatus.COMPLETED
            campaign.completed_at = datetime.now(UTC)

        now = datetime.now(UTC)
        for i, (name, country, city, domain, status, fit) in enumerate(MOCK_COMPANIES):
            lead = Lead(
                id=uuid4(),
                tenant_id=tenant.id,
                sector_id=sector.id,
                campaign_id=campaign.id,
                company_name=name,
                normalized_name=name.lower(),
                website=f"https://{domain}",
                domain=domain,
                country=country,
                city=city,
                source="mock_seed",
                status=status,
                fit_score=fit,
                priority=LeadPriority.HIGH if fit >= 85 else LeadPriority.MEDIUM,
                discovered_at=now - timedelta(days=len(MOCK_COMPANIES) - i),
                enriched_at=now if status != LeadStatus.DISCOVERED else None,
                contacted_at=now if status in {
                    LeadStatus.CONTACTED, LeadStatus.REPLIED, LeadStatus.INTERESTED,
                    LeadStatus.NOT_INTERESTED, LeadStatus.WON, LeadStatus.LOST,
                } else None,
            )
            session.add(lead)
            await session.flush()
            session.add(
                LeadContact(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    lead_id=lead.id,
                    type=ContactType.PHONE,
                    raw_value=f"+90 5{30 + i:02d} 000 00 {i:02d}",
                    normalized_value=f"9053{i:07d}",
                    country_code=country,
                    is_whatsapp=fit >= 70,
                )
            )
            session.add(
                LeadContact(
                    id=uuid4(),
                    tenant_id=tenant.id,
                    lead_id=lead.id,
                    type=ContactType.EMAIL,
                    raw_value=f"info@{domain}",
                    normalized_value=f"info@{domain}",
                    country_code=country,
                )
            )

        await session.commit()
        print(f"Seeded {len(MOCK_COMPANIES)} mock leads for tenant '{tenant_slug}' (campaign {campaign.id}).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/seed_mock_leads.py <tenant-slug>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
