"""Enrichment worker: phone normalization, website analysis, sector fit scoring."""

# Comments below reference real Turkish sector keywords (asansör, kasnağı) —
# not typos.
# ruff: noqa: RUF003

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import phonenumbers
import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from src.core.celery_app import celery_app
from src.core.db import get_sessionmaker, set_tenant_context
from src.core.text_extract import CONTACT_PATHS, DATE_RE, EMAIL_RE, PHONE_RE, strip_html
from src.modules.discovery.models import (
    CampaignStatus,
    ContactType,
    Lead,
    LeadContact,
    LeadEnrichment,
    LeadPriority,
    LeadStatus,
)
from src.modules.sectors.models import KeywordType, Sector

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)

# How many leads to enrich in parallel per campaign run (bounded by the DB pool).
_ENRICH_CONCURRENCY = 12
# Only leads scoring at or above this are persisted; the rest are purged.
_QUALIFY_THRESHOLD = 80


@celery_app.task(name="src.workers.enrichment.enrich_campaign")
def enrich_campaign(tenant_id: str, campaign_id: str) -> dict[str, Any]:
    return run_async(_enrich_campaign(UUID(tenant_id), UUID(campaign_id)))


@celery_app.task(name="src.workers.enrichment.enrich_lead")
def enrich_lead(tenant_id: str, lead_id: str) -> dict[str, Any]:
    return run_async(_enrich_lead(UUID(tenant_id), UUID(lead_id)))


async def _enrich_campaign(tenant_id: UUID, campaign_id: UUID) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as session:
        await set_tenant_context(session, tenant_id)
        stmt = select(Lead.id).where(
            Lead.tenant_id == tenant_id,
            Lead.campaign_id == campaign_id,
            Lead.status == LeadStatus.DISCOVERED,
        )
        lead_ids = list((await session.execute(stmt)).scalars().all())

    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)
    counters = {"processed": 0, "qualified": 0}

    async def _run_one(lid: UUID) -> None:
        async with sem:
            try:
                res = await _enrich_lead(tenant_id, lid)
            except Exception as e:
                logger.warning("enrich_lead_failed", lead_id=str(lid), error=str(e))
                return
            counters["processed"] += 1
            if res.get("status") == LeadStatus.QUALIFIED.value:
                counters["qualified"] += 1

    # Fan out enrichment concurrently instead of one lead at a time.
    await asyncio.gather(*(_run_one(lid) for lid in lead_ids))

    # Persist only >=80% matches: bulk-purge everything below the bar
    # (FK cascade removes their contacts / sources / enrichment rows too).
    async with sm() as session:
        await set_tenant_context(session, tenant_id)
        await session.execute(
            delete(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.campaign_id == campaign_id,
                Lead.fit_score < _QUALIFY_THRESHOLD,
            )
        )
        await session.commit()

    # Flag the campaign as done so the dashboard stops polling.
    try:
        from src.modules.discovery.service import DiscoveryService

        async with sm() as session:
            await set_tenant_context(session, tenant_id)
            await DiscoveryService(session).set_status(
                tenant_id, campaign_id, CampaignStatus.READY
            )
    except Exception as e:
        logger.warning(
            "campaign_finalize_failed", campaign_id=str(campaign_id), error=str(e)
        )

    logger.info(
        "enrich_campaign_done",
        campaign_id=str(campaign_id),
        processed=counters["processed"],
        qualified=counters["qualified"],
    )
    return counters


async def _enrich_lead(tenant_id: UUID, lead_id: UUID) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as session:
        await set_tenant_context(session, tenant_id)
        lead = await session.get(
            Lead, lead_id, options=[selectinload(Lead.sources)]
        )
        if lead is None or lead.tenant_id != tenant_id:
            return {"ok": False, "reason": "not_found"}
        lead.status = LeadStatus.ENRICHING
        await session.commit()

        # 1. Fetch website + parse contacts + keyword hits
        website_text = ""
        contact_url: str | None = None
        found_phones: list[str] = list(_extract_phones_from_sources(lead))
        found_emails: list[str] = []

        if lead.website:
            website_text, contact_url = await _fetch_site_text(lead.website)
            found_phones.extend(PHONE_RE.findall(website_text))
            found_emails.extend(EMAIL_RE.findall(website_text))

        # 2. Sector fit scoring
        sector = (
            await session.get(
                Sector, lead.sector_id, options=[selectinload(Sector.keywords)]
            )
            if lead.sector_id
            else None
        )
        fit_score = _score_fit(website_text, sector, lead.website)

        # 3. Persist LeadContact rows with E.164 normalization
        seen_phone_norm: set[str] = set()
        seen_email_norm: set[str] = set()
        for raw in set(found_phones):
            if DATE_RE.match(raw.strip()):
                continue
            e164, country = _normalize_phone(raw, lead.country)
            if not e164:
                continue
            if e164 in seen_phone_norm:
                continue
            seen_phone_norm.add(e164)
            exists_stmt = select(LeadContact).where(
                LeadContact.lead_id == lead.id,
                LeadContact.type == ContactType.PHONE,
                LeadContact.normalized_value == e164,
            )
            if (await session.execute(exists_stmt)).scalar_one_or_none():
                continue
            session.add(
                LeadContact(
                    tenant_id=tenant_id,
                    lead_id=lead.id,
                    type=ContactType.PHONE,
                    raw_value=raw[:255],
                    normalized_value=e164,
                    country_code=country,
                    is_valid=True,
                )
            )
        for em in set(found_emails):
            norm_em = em.lower()[:255]
            if norm_em in seen_email_norm:
                continue
            seen_email_norm.add(norm_em)
            exists_stmt = select(LeadContact).where(
                LeadContact.lead_id == lead.id,
                LeadContact.type == ContactType.EMAIL,
                LeadContact.normalized_value == norm_em,
            )
            if (await session.execute(exists_stmt)).scalar_one_or_none():
                continue
            session.add(
                LeadContact(
                    tenant_id=tenant_id,
                    lead_id=lead.id,
                    type=ContactType.EMAIL,
                    raw_value=em[:255],
                    normalized_value=norm_em,
                    is_valid=True,
                )
            )

        # 4. Upsert LeadEnrichment
        enr_stmt = select(LeadEnrichment).where(LeadEnrichment.lead_id == lead.id)
        enr = (await session.execute(enr_stmt)).scalar_one_or_none()
        if enr is None:
            enr = LeadEnrichment(tenant_id=tenant_id, lead_id=lead.id)
            session.add(enr)
        enr.fit_score = fit_score
        enr.contact_page_url = contact_url
        enr.meta = {
            "phones_found": len(found_phones),
            "emails_found": len(found_emails),
        }
        enr.enriched_at = datetime.now(UTC)

        # 5. Lead status + priority
        lead.fit_score = fit_score
        lead.enriched_at = datetime.now(UTC)
        if fit_score >= _QUALIFY_THRESHOLD:
            lead.status = LeadStatus.QUALIFIED
            lead.priority = LeadPriority.HIGH if fit_score >= 90 else LeadPriority.MEDIUM
        else:
            # Below the 80% bar -> discarded, then bulk-purged after the run.
            lead.status = LeadStatus.DISCARDED
            lead.priority = LeadPriority.LOW

        await session.commit()
        return {"ok": True, "fit_score": fit_score, "status": lead.status.value}


def _extract_phones_from_sources(lead: Lead) -> list[str]:
    out: list[str] = []
    for src in lead.sources or []:
        for k in ("internationalPhoneNumber", "phone", "phoneNumber"):
            if v := (src.raw_data or {}).get(k):
                out.append(str(v))
    return out


def _normalize_phone(raw: str, country: str | None) -> tuple[str | None, str | None]:
    try:
        parsed = phonenumbers.parse(raw, country)
    except phonenumbers.NumberParseException:
        return None, None
    if not phonenumbers.is_valid_number(parsed):
        return None, None
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    cc = phonenumbers.region_code_for_number(parsed)
    return e164, cc


async def _fetch_site_text(url: str) -> tuple[str, str | None]:
    normalized = url if url.startswith(("http://", "https://")) else f"http://{url}"
    root = normalized.rstrip("/")
    contact_url: str | None = None
    text_all = ""
    # Short, split timeouts so one dead host can't stall the whole batch.
    timeout = httpx.Timeout(6.0, connect=4.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": "LeadPulseBot/1.0"}
        ) as client:
            try:
                base = await client.get(normalized)
                text_all += strip_html(base.text)
            except httpx.HTTPError:
                return "", None
            # Probe contact pages but stop at the FIRST hit to stay fast.
            for path in CONTACT_PATHS:
                try:
                    r = await client.get(f"{root}{path}")
                except httpx.HTTPError:
                    continue
                if r.status_code == 200 and len(r.text) > 100:
                    text_all += "\n" + strip_html(r.text)
                    contact_url = str(r.url)
                    break
    except httpx.HTTPError:
        pass
    return text_all[:30_000], contact_url


# Distinctive, sector-specific anchor stems. A page must contain at least one
# of these to be considered genuinely relevant — this filters out generic
# pages that merely share common business words (company/service/parts…).
_ANCHOR_STEMS = (
    "asans",  # asansör
    "kasna",  # kasnak / kasnağı
    "elevator",  # elevator / elevators (full word: avoids elevation/elevate)
    "aufzu",  # Aufzug / Aufzüge
    "seilr",  # Seilrolle
    "sheave",  # sheave (full word: avoids 'shave' etc.)
    "лифт",  # лифт (RU)
    "مصعد",  # مصعد (AR)
    "مصاعد",  # مصاعد (AR plural)
)

# Reference / non-commercial hosts that publish sector words but are never
# sales leads (dictionaries, encyclopedias, government & education portals,
# trade magazines). Matched as substrings of the lead website host.
_NONCOMMERCIAL_HOSTS = (
    "leo.org",
    "tureng",
    "dict.",
    "wikipedia",
    "wiktionary",
    "agrarheute",
    ".gov",
    ".hamburg.de",
    "-portal.",
    "overnetdata",  # Edulink school-management SaaS
    "edulink",
)


def _is_noncommercial(website: str | None) -> bool:
    if not website:
        return False
    host = website.lower()
    return any(bad in host for bad in _NONCOMMERCIAL_HOSTS)


def _phrase_anchor_hit(phrase: str, lower: str) -> bool:
    """A positive keyword counts as matched when its full phrase appears, or
    when a distinctive anchor stem contained in the phrase is also present in
    the text (robust to inflection / word order across languages)."""
    if phrase in lower:
        return True
    return any(anchor in phrase and anchor in lower for anchor in _ANCHOR_STEMS)


def _score_fit(text: str, sector: Sector | None, website: str | None = None) -> int:
    if not sector or not text:
        return 0
    if _is_noncommercial(website):
        return 0  # dictionary / gov / media host → never a sales lead
    lower = text.lower()
    if not any(a in lower for a in _ANCHOR_STEMS):
        return 0  # no sector anchor → not a relevant business
    matched = 0
    negative_hits = 0
    for kw in sector.keywords:
        phrase = kw.keyword.lower()
        if kw.keyword_type == KeywordType.POSITIVE:
            if _phrase_anchor_hit(phrase, lower):
                matched += 1
        elif phrase in lower:
            # Negatives are precise exclusions → require full phrase match.
            negative_hits += 1
    raw = matched * 20 - negative_hits * 25
    return max(0, min(100, raw))
