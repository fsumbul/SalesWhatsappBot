"""Discovery orchestrator task."""

# A comment below is partly in Turkish — not a typo.
# ruff: noqa: RUF003

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog

from src.core.celery_app import celery_app
from src.core.config import get_settings
from src.core.db import get_sessionmaker, set_tenant_context
from src.integrations.base import LeadConnector, RawLead
from src.integrations.bing import BingSearchConnector
from src.integrations.google_places import GooglePlacesConnector
from src.integrations.overpass import OverpassConnector
from src.integrations.serpapi import SerpAPIConnector
from src.modules.discovery.query_generator import generate_queries
from src.modules.discovery.service import DiscoveryService
from src.modules.sectors.repository import SectorRepo

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)

# Route serp-hinted queries to Bing when SerpAPI key is absent; Bing itself
# falls back to the free SERP scraper if no Bing key is set — so the discovery
# hattı çalışsın kâlacak without any paid credentials.
_s = get_settings()
CONNECTOR_MAP = {
    "google_places": GooglePlacesConnector,
    "serpapi": SerpAPIConnector if _s.serpapi_key else BingSearchConnector,
    "bing": BingSearchConnector,
    "overpass": OverpassConnector,
}


@celery_app.task(name="src.workers.discovery.run_discovery_for_campaign")
def run_discovery_for_campaign(
    tenant_id: str, campaign_id: str, countries: list[str] | None = None
) -> dict[str, Any]:
    return run_async(_run(UUID(tenant_id), UUID(campaign_id), countries))


async def _run(
    tenant_id: UUID, campaign_id: UUID, countries: list[str] | None = None
) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as session:
        await set_tenant_context(session, tenant_id)
        svc = DiscoveryService(session)
        campaign = await svc.get_campaign(tenant_id, campaign_id)
        sector = await SectorRepo(session).get(tenant_id, campaign.sector_id)
        if sector is None:
            logger.warning("discovery_no_sector", campaign_id=str(campaign_id))
            return {"leads": 0}

        queries = generate_queries(sector, per_country_limit=15)
        if countries:
            wanted = {c.strip().upper() for c in countries if c.strip()}
            if wanted:
                queries = [q for q in queries if (q.country or "").upper() in wanted]
        logger.info(
            "discovery_queries_generated", count=len(queries), countries=countries
        )

        connectors = {name: cls() for name, cls in CONNECTOR_MAP.items()}
        raw_leads: list[RawLead] = []

        tasks = []
        for q in queries:
            conn = connectors.get(q.source_hint)
            if conn is None:
                continue
            tasks.append(_collect(conn, q.text, q.country, q.language))

        for coro in asyncio.as_completed(tasks):
            try:
                raw_leads.extend(await coro)
            except Exception as e:
                logger.warning("connector_failed", error=str(e))

        inserted = await svc.ingest_raw_leads(
            tenant_id, campaign_id, sector.id, raw_leads
        )
        # Keep the campaign in DISCOVERING; enrichment flips it to READY when
        # the whole scan (discovery + enrichment + purge) is finished, so the
        # dashboard keeps polling the live qualified count until then.
        from .enrichment import enrich_campaign

        enrich_campaign.delay(str(tenant_id), str(campaign_id))
        return {"leads_inserted": inserted, "raw_collected": len(raw_leads)}


async def _collect(
    conn: LeadConnector, query: str, country: str, language: str
) -> list[RawLead]:
    out: list[RawLead] = []

    async def _iter() -> None:
        async for rl in conn.search(query, country, language):
            out.append(rl)

    try:
        # Hard cap per connector query so one slow source can't clog discovery.
        await asyncio.wait_for(_iter(), timeout=45.0)
    except TimeoutError:
        logger.warning("connector_timeout", connector=conn.name, query=query)
    except Exception as e:
        logger.warning("connector_iter_failed", connector=conn.name, error=str(e))
    return out
