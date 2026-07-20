"""Discovery HTTP router."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.core.deps import ClaimsDep, DBSessionDep
from src.core.rbac import RequireManager

from .models import CampaignStatus
from .schemas import (
    CampaignIn,
    CampaignOut,
    CampaignPatchIn,
    DiscoverIn,
    DiscoveryStatusOut,
    LeadDetailOut,
    LeadFilters,
    LeadOut,
)
from .service import DiscoveryService

router = APIRouter(tags=["discovery"])
campaigns_router = APIRouter(prefix="/campaigns", tags=["campaigns"])
leads_router = APIRouter(prefix="/leads", tags=["leads"])


def _tid(claims: dict) -> UUID:
    return UUID(claims["tid"])


@campaigns_router.get("", response_model=list[CampaignOut])
async def list_campaigns(db: DBSessionDep, claims: ClaimsDep) -> list[CampaignOut]:
    items = await DiscoveryService(db).list_campaigns(_tid(claims))
    return [CampaignOut.model_validate(c) for c in items]


@campaigns_router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignIn, db: DBSessionDep, claims: RequireManager
) -> CampaignOut:
    camp = await DiscoveryService(db).create_campaign(_tid(claims), payload)
    return CampaignOut.model_validate(camp)


@campaigns_router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: UUID, db: DBSessionDep, claims: ClaimsDep) -> CampaignOut:
    camp = await DiscoveryService(db).get_campaign(_tid(claims), campaign_id)
    return CampaignOut.model_validate(camp)


@campaigns_router.patch("/{campaign_id}", response_model=CampaignOut)
async def patch_campaign(
    campaign_id: UUID,
    payload: CampaignPatchIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> CampaignOut:
    camp = await DiscoveryService(db).patch_campaign(_tid(claims), campaign_id, payload)
    return CampaignOut.model_validate(camp)


@campaigns_router.post("/{campaign_id}/discover", response_model=CampaignOut)
async def start_discovery(
    campaign_id: UUID,
    db: DBSessionDep,
    claims: RequireManager,
    payload: DiscoverIn | None = None,
) -> CampaignOut:
    # Enqueue Celery job (lazy import to avoid circular)
    from src.workers.discovery import run_discovery_for_campaign

    tenant_id = _tid(claims)
    camp = await DiscoveryService(db).set_status(
        tenant_id, campaign_id, CampaignStatus.DISCOVERING
    )
    countries = payload.countries if payload else None
    run_discovery_for_campaign.delay(str(tenant_id), str(campaign_id), countries)
    return CampaignOut.model_validate(camp)


@campaigns_router.get("/{campaign_id}/discovery-status", response_model=DiscoveryStatusOut)
async def discovery_status(
    campaign_id: UUID, db: DBSessionDep, claims: ClaimsDep
) -> DiscoveryStatusOut:
    summary = await DiscoveryService(db).status_summary(_tid(claims), campaign_id)
    return DiscoveryStatusOut(**summary)


@leads_router.get("", response_model=list[LeadOut])
async def list_leads(
    db: DBSessionDep,
    claims: ClaimsDep,
    filters: LeadFilters = Depends(),
) -> list[LeadOut]:
    items = await DiscoveryService(db).list_leads(_tid(claims), filters)
    return [LeadOut.model_validate(l) for l in items]


@leads_router.get("/{lead_id}", response_model=LeadDetailOut)
async def get_lead(lead_id: UUID, db: DBSessionDep, claims: ClaimsDep) -> LeadDetailOut:
    lead = await DiscoveryService(db).get_lead(_tid(claims), lead_id)
    return LeadDetailOut.model_validate(lead)


@leads_router.post("/{lead_id}/enrich", response_model=LeadDetailOut)
async def trigger_enrich(
    lead_id: UUID, db: DBSessionDep, claims: RequireManager
) -> LeadDetailOut:
    from src.workers.enrichment import enrich_lead

    tenant_id = _tid(claims)
    lead = await DiscoveryService(db).get_lead(tenant_id, lead_id)
    enrich_lead.delay(str(tenant_id), str(lead_id))
    return LeadDetailOut.model_validate(lead)
