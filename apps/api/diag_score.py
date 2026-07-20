"""Diagnostic: discover TR elevator leads and print the fit-score distribution
WITHOUT purging, so we can judge whether the 80% bar is right or whether the
website fetch/scoring is under-scoring real companies."""

import asyncio
from uuid import UUID

from sqlalchemy import delete, select

from src.core.db import get_sessionmaker, set_tenant_context
from src.modules.discovery.models import Campaign, Lead, LeadStatus
from src.modules.discovery.query_generator import generate_queries
from src.modules.discovery.schemas import CampaignIn
from src.modules.discovery.service import DiscoveryService
from src.modules.sectors.repository import SectorRepo
from src.workers.discovery import CONNECTOR_MAP, _collect
from src.workers.enrichment import _enrich_lead

TENANT = UUID("f40cfc51-c496-4049-bf3f-8bcbbd98436e")
SECTOR = UUID("a1a6f766-190f-4a1f-bd78-ab8097b17e75")


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        await set_tenant_context(s, TENANT)
        svc = DiscoveryService(s)
        camp = await svc.create_campaign(
            TENANT, CampaignIn(sector_id=SECTOR, name="DIAG_SCORE")
        )
        cid = camp.id
        sector = await SectorRepo(s).get(TENANT, SECTOR)

    queries = [
        q
        for q in generate_queries(sector, per_country_limit=15)
        if (q.country or "").upper() == "TR"
    ]
    conns = {n: c() for n, c in CONNECTOR_MAP.items()}
    raw = []
    tasks = [
        _collect(conns[q.source_hint], q.text, q.country, q.language)
        for q in queries
        if q.source_hint in conns
    ]
    for t in asyncio.as_completed(tasks):
        raw.extend(await t)

    async with sm() as s:
        await set_tenant_context(s, TENANT)
        ins = await DiscoveryService(s).ingest_raw_leads(TENANT, cid, SECTOR, raw)

    async with sm() as s:
        await set_tenant_context(s, TENANT)
        ids = list(
            (
                await s.execute(
                    select(Lead.id).where(
                        Lead.campaign_id == cid,
                        Lead.status == LeadStatus.DISCOVERED,
                    )
                )
            )
            .scalars()
            .all()
        )

    sem = asyncio.Semaphore(12)

    async def one(lid):
        async with sem:
            try:
                await _enrich_lead(TENANT, lid)
            except Exception as e:  # noqa: BLE001
                print("err", e)

    await asyncio.gather(*(one(x) for x in ids))

    async with sm() as s:
        await set_tenant_context(s, TENANT)
        rows = (
            await s.execute(
                select(Lead.company_name, Lead.fit_score, Lead.website)
                .where(Lead.campaign_id == cid)
                .order_by(Lead.fit_score.desc())
            )
        ).all()

    print(f"RAW={len(raw)} INSERTED={ins} TOTAL={len(rows)}")
    ge80 = sum(1 for _, sc, _ in rows if sc >= 80)
    b6079 = sum(1 for _, sc, _ in rows if 60 <= sc < 80)
    b4059 = sum(1 for _, sc, _ in rows if 40 <= sc < 60)
    print(f">=80: {ge80}   60-79: {b6079}   40-59: {b4059}   <40: {len(rows)-ge80-b6079-b4059}")
    for name, score, site in rows:
        print(f"{score:3d}  {(name or '')[:42]:42s}  {site or ''}")

    async with sm() as s:
        await set_tenant_context(s, TENANT)
        await s.execute(delete(Lead).where(Lead.campaign_id == cid))
        await s.execute(delete(Campaign).where(Campaign.id == cid))
        await s.commit()


asyncio.run(main())
