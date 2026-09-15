"""Chunked, durable staging for file-backed outreach batches.

The workflow request only creates the reviewed batch state.  This worker turns
the already parsed import rows into the existing outbox, then performs the
single batch-level queue validation.  ``chat_outbound.send_one`` remains the
last line of defence immediately before every Meta POST.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select, update

from src.core.celery_app import celery_app
from src.core.db import session_scope
from src.core.errors import ConflictError
from src.core.rbac import Role, role_at_least
from src.modules.admin_chat import campaign_imports, outbound
from src.modules.admin_chat.campaign_imports import (
    CampaignImport,
    CampaignImportRow,
    CampaignImportRowStatus,
    CampaignImportStatus,
)
from src.modules.admin_chat.outbound_models import OutboundBatch, OutboundRecipient
from src.modules.auth.models import Tenant, TenantStatus, User
from src.modules.outreach.channel import resolve_channel
from src.modules.outreach.models import SenderProfile

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)

# Keep the transaction/identity map bounded while allowing one 10,000-recipient
# batch to remain atomic: a crash rolls all chunks back to ``preparing``.
MATERIALIZE_CHUNK_SIZE = 500
MAX_BATCHES_PER_TENANT = 2


@celery_app.task(name="src.workers.campaign_outbox.progress_campaign_outbox")
def progress_campaign_outbox() -> dict[str, int]:
    """Advance committed file batches; safe to run repeatedly or concurrently."""

    return run_async(_progress_campaign_outbox())


async def _active_tenant_ids() -> list[UUID]:
    async with session_scope() as db:
        return list(
            (
                await db.scalars(select(Tenant.id).where(Tenant.status == TenantStatus.ACTIVE))
            ).all()
        )


async def _batch_ids(tenant_id: UUID, status: str) -> list[UUID]:
    async with session_scope(tenant_id) as db:
        return list(
            (
                await db.scalars(
                    select(OutboundBatch.id)
                    .where(
                        OutboundBatch.tenant_id == tenant_id,
                        OutboundBatch.status == status,
                    )
                    .order_by(OutboundBatch.created_at)
                    .limit(MAX_BATCHES_PER_TENANT)
                )
            ).all()
        )


async def _progress_campaign_outbox() -> dict[str, int]:
    prepared = queued = 0
    tenant_ids = await _active_tenant_ids()
    for tenant_id in tenant_ids:
        preparing = await _batch_ids(tenant_id, "preparing")
        results = await asyncio.gather(
            *(_materialize_import_batch(tenant_id, batch_id) for batch_id in preparing),
            return_exceptions=True,
        )
        for result in results:
            if result == "prepared":
                prepared += 1
            elif isinstance(result, Exception):  # pragma: no cover - defensive task boundary
                logger.exception("campaign_outbox_materialize_crashed", tenant_id=str(tenant_id))

        queueing = await _batch_ids(tenant_id, "queueing")
        results = await asyncio.gather(
            *(_queue_import_batch(tenant_id, batch_id) for batch_id in queueing),
            return_exceptions=True,
        )
        for result in results:
            if result == "queued":
                queued += 1
            elif isinstance(result, Exception):  # pragma: no cover - defensive task boundary
                logger.exception("campaign_outbox_queue_crashed", tenant_id=str(tenant_id))
    return {"tenants": len(tenant_ids), "prepared": prepared, "queued": queued}


async def _mark_batch_failed(db: Any, batch: OutboundBatch, reason: str) -> str:
    """Terminally stop a batch only for a deterministic policy/data failure."""

    await db.execute(
        update(OutboundRecipient)
        .where(
            OutboundRecipient.tenant_id == batch.tenant_id,
            OutboundRecipient.batch_id == batch.id,
            OutboundRecipient.status == "draft",
        )
        .values(status="blocked", reason=reason)
    )
    batch.status = "failed"
    await db.commit()
    logger.warning(
        "campaign_outbox_batch_failed", batch_id=str(batch.id), tenant_id=str(batch.tenant_id)
    )
    return "failed"


def _active_manager(user: User | None) -> bool:
    return bool(user and user.is_active and role_at_least(user.role, Role.SALES_MANAGER))


async def _materialize_import_batch(tenant_id: UUID, batch_id: UUID) -> str:
    """Create recipients in chunks without exposing a 10k loop to HTTP.

    The batch row remains locked until all chunks commit.  A second Celery
    delivery therefore skips it, and an interrupted transaction leaves no
    partial recipient set behind for a later recovery run.
    """

    try:
        async with session_scope(tenant_id) as db:
            batch = await db.scalar(
                select(OutboundBatch)
                .where(OutboundBatch.id == batch_id, OutboundBatch.tenant_id == tenant_id)
                .with_for_update(skip_locked=True)
            )
            if batch is None or batch.status != "preparing":
                return "skipped"
            if not outbound.is_campaign_import_batch(batch) or not batch.campaign_import_id:
                return await _mark_batch_failed(
                    db, batch, "Dosya kaynak kaydı artık gönderime hazırlanamaz."
                )
            user = await db.get(User, batch.user_id)
            if not _active_manager(user) or not campaign_imports.campaign_imports_enabled_for(
                tenant_id, batch.user_id
            ):
                return await _mark_batch_failed(
                    db, batch, "Dosya kampanyası yetkisi artık geçerli değil."
                )
            imported = await db.scalar(
                select(CampaignImport)
                .where(
                    CampaignImport.id == batch.campaign_import_id,
                    CampaignImport.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if imported is None or imported.status != CampaignImportStatus.READY.value:
                return await _mark_batch_failed(db, batch, "İçe aktarma incelemeye hazır değil.")
            if imported.expires_at <= datetime.now(UTC):
                return await _mark_batch_failed(db, batch, "İçe aktarmanın saklama süresi doldu.")
            expected = int(imported.eligible_count)
            if not 1 <= expected <= campaign_imports.campaign_import_recipient_limit():
                return await _mark_batch_failed(db, batch, "İçe aktarma canlı alıcı limitini aşıyor.")
            existing = await db.scalar(
                select(func.count())
                .select_from(OutboundRecipient)
                .where(
                    OutboundRecipient.tenant_id == tenant_id,
                    OutboundRecipient.batch_id == batch.id,
                )
            )
            if int(existing or 0):
                return await _mark_batch_failed(
                    db, batch, "Kampanya alıcıları tutarsız bir hazırlık durumunda."
                )
            available = await db.scalar(
                select(func.count())
                .select_from(CampaignImportRow)
                .where(
                    CampaignImportRow.tenant_id == tenant_id,
                    CampaignImportRow.campaign_import_id == imported.id,
                    CampaignImportRow.status == CampaignImportRowStatus.ELIGIBLE.value,
                    CampaignImportRow.phone_e164.is_not(None),
                )
            )
            if int(available or 0) != expected:
                return await _mark_batch_failed(
                    db, batch, "İçe aktarma alıcı sayısı değişti; yeniden inceleyin."
                )

            last_row_number = 0
            created = 0
            while True:
                chunk = (
                    await db.execute(
                        select(CampaignImportRow.row_number, CampaignImportRow.phone_e164)
                        .where(
                            CampaignImportRow.tenant_id == tenant_id,
                            CampaignImportRow.campaign_import_id == imported.id,
                            CampaignImportRow.status == CampaignImportRowStatus.ELIGIBLE.value,
                            CampaignImportRow.phone_e164.is_not(None),
                            CampaignImportRow.row_number > last_row_number,
                        )
                        .order_by(CampaignImportRow.row_number)
                        .limit(MATERIALIZE_CHUNK_SIZE)
                    )
                ).all()
                if not chunk:
                    break
                phones = [str(phone) for _row_number, phone in chunk if phone]
                if len(phones) != len(chunk):
                    raise RuntimeError("eligible campaign import row has no phone")
                db.add_all(
                    [
                        OutboundRecipient(
                            tenant_id=tenant_id,
                            batch_id=batch.id,
                            phone=phone,
                            status="draft",
                        )
                        for phone in phones
                    ]
                )
                created += len(phones)
                last_row_number = int(chunk[-1].row_number)
                await db.flush()
            if created != expected:  # defensive: count above should make this unreachable.
                raise RuntimeError("campaign import materialization count changed during preparation")
            batch.status = "draft"
            await db.commit()
            logger.info(
                "campaign_outbox_batch_prepared",
                batch_id=str(batch.id),
                tenant_id=str(tenant_id),
                recipients=created,
            )
            return "prepared"
    except Exception:  # partial chunks roll back; the durable preparing state is retried.
        logger.exception(
            "campaign_outbox_materialize_failed", batch_id=str(batch_id), tenant_id=str(tenant_id)
        )
        return "retry"


async def _queue_import_batch(tenant_id: UUID, batch_id: UUID) -> str:
    """Perform bounded queue-time checks, then update recipient state set-wise.

    Per-recipient opt-out, compliance, capacity and template checks still run
    in ``chat_outbound.send_one`` immediately before its durable Meta POST.
    This stage intentionally validates provider/sender/capacity once per batch
    rather than serially locking and querying every imported phone in HTTP.
    """

    try:
        async with session_scope(tenant_id) as db:
            batch = await db.scalar(
                select(OutboundBatch)
                .where(OutboundBatch.id == batch_id, OutboundBatch.tenant_id == tenant_id)
                .with_for_update(skip_locked=True)
            )
            if (
                batch is None
                or batch.status != "queueing"
                or not outbound.is_campaign_import_batch(batch)
            ):
                return "skipped"
            # A source row is required at queue time. Retention deliberately
            # NULLs this FK after 30 days, so provenance alone is not enough to
            # resurrect a stale, unreviewable phone list for delivery.
            if not batch.campaign_import_id:
                return await _mark_batch_failed(
                    db, batch, "İçe aktarma kaydı artık gönderim için saklanmıyor."
                )
            imported = await db.scalar(
                select(CampaignImport)
                .where(
                    CampaignImport.id == batch.campaign_import_id,
                    CampaignImport.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if imported is None or imported.status != CampaignImportStatus.READY.value:
                return await _mark_batch_failed(db, batch, "İçe aktarma incelemeye hazır değil.")
            if imported.expires_at <= datetime.now(UTC):
                return await _mark_batch_failed(db, batch, "İçe aktarmanın saklama süresi doldu.")
            user = await db.get(User, batch.user_id)
            if not _active_manager(user) or not campaign_imports.campaign_imports_enabled_for(
                tenant_id, batch.user_id
            ):
                return await _mark_batch_failed(
                    db, batch, "Dosya kampanyası yetkisi artık geçerli değil."
                )
            if not batch.consent_evidence:
                return await _mark_batch_failed(db, batch, "İzin beyanı bulunamadı.")
            if any(not str(batch.variables.get(key, "")).strip() for key in batch.template["variables"]):
                return await _mark_batch_failed(db, batch, "Şablondaki tüm alanları doldurun.")
            try:
                sender = await resolve_channel(db, tenant_id)
            except ConflictError:
                return await _mark_batch_failed(
                    db, batch, "WhatsApp bağlantısı artık geçerli değil."
                )
            await db.execute(
                select(SenderProfile)
                .where(SenderProfile.id == sender.id, SenderProfile.tenant_id == tenant_id)
                .with_for_update()
            )
            if sender.id != batch.sender_id:
                return await _mark_batch_failed(
                    db, batch, "WhatsApp bağlantısı değişti; yeni bir önizleme hazırlayın."
                )
            try:
                templates = await outbound.meta_templates(sender)
            except Exception:
                # A provider failure is retryable.  Keep the durable queueing
                # state untouched; the periodic task repairs broker/provider gaps.
                logger.warning(
                    "campaign_outbox_template_check_deferred",
                    batch_id=str(batch.id),
                    tenant_id=str(tenant_id),
                )
                return "retry"
            if batch.template not in templates:
                return await _mark_batch_failed(
                    db, batch, "Şablon değişmiş veya Meta onayı kaldırılmış."
                )
            recipient_count = int(
                await db.scalar(
                    select(func.count())
                    .select_from(OutboundRecipient)
                    .where(
                        OutboundRecipient.tenant_id == tenant_id,
                        OutboundRecipient.batch_id == batch.id,
                        OutboundRecipient.status == "draft",
                    )
                )
                or 0
            )
            if not 1 <= recipient_count <= campaign_imports.campaign_import_recipient_limit():
                return await _mark_batch_failed(
                    db, batch, "Dosya kampanyası canlı alıcı limitini aşıyor."
                )
            capacity = await outbound.capacity(db, tenant_id)
            if not capacity.get("connected"):
                return await _mark_batch_failed(
                    db, batch, "WhatsApp bağlantısı artık geçerli değil."
                )
            if not capacity.get("meta_available"):
                logger.warning(
                    "campaign_outbox_capacity_check_deferred",
                    batch_id=str(batch.id),
                    tenant_id=str(tenant_id),
                )
                return "retry"
            if recipient_count > int(capacity.get("local_remaining", 0)):
                return await _mark_batch_failed(
                    db, batch, "Şirketin yerel gönderim kapasitesi yetersiz."
                )
            if (
                capacity.get("meta_limit") is not None
                and recipient_count > int(capacity["meta_limit"])
            ):
                return await _mark_batch_failed(
                    db, batch, "Alıcı listesi Meta portföy limitinden büyük."
                )
            await db.execute(
                update(OutboundRecipient)
                .where(
                    OutboundRecipient.tenant_id == tenant_id,
                    OutboundRecipient.batch_id == batch.id,
                    OutboundRecipient.status == "draft",
                )
                .values(status="queued", reason=None)
            )
            batch.status = "queued"
            await db.commit()
            logger.info(
                "campaign_outbox_batch_queued",
                batch_id=str(batch.id),
                tenant_id=str(tenant_id),
                recipients=recipient_count,
            )
            return "queued"
    except Exception:
        logger.exception(
            "campaign_outbox_queue_failed", batch_id=str(batch_id), tenant_id=str(tenant_id)
        )
        return "retry"
