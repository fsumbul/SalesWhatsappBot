"""Asynchronous, tenant-scoped parsing and retention for outreach imports."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, delete, or_, select

from src.core.celery_app import celery_app
from src.core.db import session_scope
from src.modules.admin_chat import campaign_imports
from src.modules.admin_chat.campaign_imports import (
    CampaignImport,
    CampaignImportRow,
    CampaignImportStatus,
    CampaignImportStorageError,
    CampaignImportValidationError,
    build_campaign_import_rows,
    get_campaign_import,
    get_campaign_import_storage,
    parse_import_bytes,
    validate_upload,
)
from src.modules.admin_chat.models import AdminChatSession, AdminChatTurn
from src.modules.admin_chat.workflow_models import Workflow, WorkflowAction
from src.modules.auth.models import Tenant, TenantStatus

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)

# Celery's hard task timeout is 10 minutes. A parsing row older than this
# buffer belongs to a crashed/lost task rather than an in-flight parser.
PARSING_RECOVERY_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class _ParseClaim:
    """Immutable parser input captured under the import-row lease."""

    token: UUID
    object_key: str
    sha256: str
    original_filename: str
    mime_type: str
    country_code: str
    phone_column: str | None


async def _scrub_expired_import_replicas(
    db: Any, imported: CampaignImport, *, workflow: Workflow | None
) -> None:
    """Remove file-card replicas while preserving the durable outbound receipt.

    Workflow/cards/turns store JSON snapshots for optimistic revisions and
    idempotency. They are not legal delivery receipts, so they must follow the
    source file's 30-day retention period as well.
    """

    session_id: UUID | None = None
    if workflow is not None:
        session_id = workflow.session_id
        if str(workflow.state.get("import_id") or "") == str(imported.id):
            workflow.fields = campaign_imports.redact_expired_import_fields(workflow.fields)
            state = campaign_imports.redact_expired_import_artifacts(workflow.state, imported.id)
            workflow.state = {
                **state,
                "import_status": "expired",
                "campaign_import": campaign_imports.expired_import_view(),
            }
            workflow.result = campaign_imports.redact_expired_import_artifacts(
                workflow.result, imported.id
            )

    actions = list(
        (
            await db.scalars(
                select(WorkflowAction).where(
                    WorkflowAction.tenant_id == imported.tenant_id,
                    WorkflowAction.workflow_id == imported.workflow_id,
                )
            )
        ).all()
    )
    for action in actions:
        response = campaign_imports.redact_expired_import_artifacts(action.response, imported.id)
        request = campaign_imports.redact_expired_import_request(action.request)
        if response != action.response:
            action.response = response
        if request != action.request:
            action.request = request

    if session_id is None:
        return
    turns = list(
        (
            await db.scalars(
                select(AdminChatTurn).where(
                    AdminChatTurn.tenant_id == imported.tenant_id,
                    AdminChatTurn.session_id == session_id,
                )
            )
        ).all()
    )
    for turn in turns:
        response = campaign_imports.redact_expired_import_artifacts(turn.response, imported.id)
        audit = campaign_imports.redact_expired_import_artifacts(turn.audit, imported.id)
        if response != turn.response:
            turn.response = response
        if audit != turn.audit:
            turn.audit = audit


@celery_app.task(name="src.workers.campaign_imports.parse_campaign_import")
def parse_campaign_import(
    tenant_id: str, import_id: str, *, phone_column: str | None = None
) -> dict[str, Any]:
    return run_async(_parse_campaign_import(UUID(tenant_id), UUID(import_id), phone_column))


async def _claimed_import(
    db: Any, tenant_id: UUID, import_id: UUID, token: UUID
) -> CampaignImport | None:
    """Return an import only while this worker still owns the parse lease."""

    imported = await get_campaign_import(
        db, tenant_id=tenant_id, import_id=import_id, lock=True
    )
    if (
        imported is None
        or imported.status != CampaignImportStatus.PARSING.value
        or imported.parse_token != token
    ):
        return None
    return imported


async def _claim_parse(
    tenant_id: UUID, import_id: UUID, requested_phone_column: str | None
) -> tuple[_ParseClaim | None, dict[str, Any]]:
    """Atomically claim one queued import and snapshot non-secret metadata."""

    async with session_scope(tenant_id) as db:
        imported = await get_campaign_import(
            db, tenant_id=tenant_id, import_id=import_id, lock=True
        )
        if imported is None:
            # A delayed task can run after an aborted upload transaction. It is
            # safe to stop because recovery only scans committed queued rows.
            return None, {"ok": False, "reason": "not_found"}
        if imported.status == CampaignImportStatus.PARSING.value:
            return None, {"ok": True, "status": imported.status}
        if imported.status == CampaignImportStatus.READY.value:
            # Celery may deliver an enqueue message more than once. A ready
            # import is already the desired durable state.
            return None, {"ok": True, "status": imported.status}
        if imported.status in {
            CampaignImportStatus.AWAITING_MAPPING.value,
            CampaignImportStatus.FAILED.value,
        } and not requested_phone_column:
            return None, {"ok": True, "status": imported.status}
        if imported.expires_at <= datetime.now(UTC):
            imported.status = CampaignImportStatus.FAILED.value
            imported.parse_token = None
            imported.failure_reason = "Dosyanın saklama süresi dolmuş."
            await db.commit()
            return None, {"ok": False, "status": imported.status}

        if requested_phone_column:
            selected = requested_phone_column.strip()
            if not selected or (imported.columns and selected not in imported.columns):
                imported.status = CampaignImportStatus.FAILED.value
                imported.parse_token = None
                imported.failure_reason = "Telefon sütunu dosyada bulunamadı."
                await db.commit()
                return None, {"ok": False, "status": imported.status}
            # The selected mapping is persisted before the task is enqueued.
            # A stale broker message must not silently substitute a different
            # header into a newer manager decision.
            if imported.phone_column and imported.phone_column != selected:
                return None, {"ok": True, "status": "superseded"}
            imported.phone_column = selected

        if imported.status != CampaignImportStatus.QUEUED.value:
            return None, {"ok": True, "status": imported.status}
        token = uuid4()
        imported.status = CampaignImportStatus.PARSING.value
        imported.parse_token = token
        imported.failure_reason = None
        claim = _ParseClaim(
            token=token,
            object_key=imported.object_key,
            sha256=imported.sha256,
            original_filename=imported.original_filename,
            mime_type=imported.mime_type,
            country_code=imported.country_code,
            phone_column=imported.phone_column,
        )
        await db.commit()
        return claim, {"ok": True, "status": CampaignImportStatus.PARSING.value}


async def _mark_parse_failure(
    tenant_id: UUID, import_id: UUID, token: UUID, reason: str
) -> str:
    """Persist a parser failure only if a recovered worker did not replace it."""

    async with session_scope(tenant_id) as db:
        imported = await _claimed_import(db, tenant_id, import_id, token)
        if imported is None:
            return "superseded"
        imported.status = CampaignImportStatus.FAILED.value
        imported.parse_token = None
        imported.failure_reason = reason
        await db.commit()
        return imported.status


async def _parse_campaign_import(
    tenant_id: UUID, import_id: UUID, phone_column: str | None = None
) -> dict[str, Any]:
    """Parse one private artifact behind a renewable, token-checked lease.

    The database transaction only claims/finalizes a parse. Object I/O and
    workbook decoding run outside it. A beat task may safely requeue a stale
    ``parsing`` record: an older worker no longer owns its token and therefore
    cannot overwrite the newer parser's result or failure state.
    """

    claim, immediate = await _claim_parse(tenant_id, import_id, phone_column)
    if claim is None:
        return immediate

    try:
        data = await get_campaign_import_storage().get_bytes(claim.object_key)
        if hashlib.sha256(data).hexdigest() != claim.sha256:
            raise CampaignImportValidationError("Dosya bütünlüğü doğrulanamadı.")
        # Validate again after retrieval; storage must not turn a harmless
        # upload into a different content type between the two boundaries.
        validate_upload(claim.original_filename, claim.mime_type, data)
        parsed = parse_import_bytes(data, claim.mime_type, claim.phone_column)
    except CampaignImportStorageError:
        status = await _mark_parse_failure(
            tenant_id, import_id, claim.token, "Dosya depodan okunamadı."
        )
        logger.warning("campaign_import_storage_read_failed", import_id=str(import_id))
        return {"ok": False, "status": status}
    except CampaignImportValidationError as exc:
        status = await _mark_parse_failure(tenant_id, import_id, claim.token, str(exc))
        logger.info("campaign_import_invalid", import_id=str(import_id))
        return {"ok": False, "status": status}
    except Exception:  # pragma: no cover - provider/parser failure boundary
        status = await _mark_parse_failure(
            tenant_id, import_id, claim.token, "Dosya ayrıştırılamadı."
        )
        logger.exception("campaign_import_parse_failed", import_id=str(import_id))
        return {"ok": False, "status": status}

    try:
        async with session_scope(tenant_id) as db:
            imported = await _claimed_import(db, tenant_id, import_id, claim.token)
            if imported is None:
                return {"ok": True, "status": "superseded"}
            imported.columns = parsed.columns
            if parsed.phone_column is None:
                imported.status = CampaignImportStatus.AWAITING_MAPPING.value
                imported.parse_token = None
                imported.phone_column = None
                imported.total_rows = 0
                imported.eligible_count = 0
                imported.invalid_count = 0
                imported.duplicate_count = 0
                imported.blocked_count = 0
                imported.summary = {
                    "message": "Telefon sütununu seçin; dosya henüz alıcı listesi olarak işlenmedi.",
                    "examples": [],
                }
                await db.commit()
                return {
                    "ok": True,
                    "status": CampaignImportStatus.AWAITING_MAPPING.value,
                    "columns": parsed.columns,
                }

            rows, counts, examples = await build_campaign_import_rows(
                db,
                tenant_id=tenant_id,
                campaign_import_id=imported.id,
                country_code=claim.country_code,
                phone_values=parsed.phone_values,
            )
            await db.execute(
                delete(CampaignImportRow).where(
                    CampaignImportRow.tenant_id == tenant_id,
                    CampaignImportRow.campaign_import_id == imported.id,
                )
            )
            db.add_all(rows)
            imported.phone_column = parsed.phone_column
            imported.total_rows = len(parsed.phone_values)
            imported.eligible_count = counts["eligible"]
            imported.invalid_count = counts["invalid"]
            imported.duplicate_count = counts["duplicate"]
            imported.blocked_count = counts["blocked"]
            imported.summary = {
                "message": "Dosya incelemeye hazır. Gönderimden önce izin, şablon ve kapasite tekrar doğrulanır.",
                "examples": examples,
            }
            imported.status = CampaignImportStatus.READY.value
            imported.parse_token = None
            imported.failure_reason = None
            await db.commit()
            total, eligible = imported.total_rows, imported.eligible_count
    except Exception:  # pragma: no cover - database failure boundary
        logger.exception("campaign_import_persist_failed", import_id=str(import_id))
        status = await _mark_parse_failure(
            tenant_id, import_id, claim.token, "Dosya sonuçları kaydedilemedi."
        )
        return {"ok": False, "status": status}

    logger.info("campaign_import_ready", import_id=str(import_id), total=total, eligible=eligible)
    return {
        "ok": True,
        "status": CampaignImportStatus.READY.value,
        "total": total,
        "eligible": eligible,
    }


@celery_app.task(name="src.workers.campaign_imports.dispatch_queued_campaign_imports")
def dispatch_queued_campaign_imports() -> dict[str, Any]:
    """Recover committed imports if an enqueue message was lost around commit."""

    return run_async(_dispatch_queued_campaign_imports())


async def _tenant_ids(*, active_only: bool) -> list[UUID]:
    async with session_scope() as db:
        stmt = select(Tenant.id)
        if active_only:
            stmt = stmt.where(Tenant.status == TenantStatus.ACTIVE)
        return list(
            (await db.scalars(stmt)).all()
        )


async def _locked_expired_import_for_cleanup(
    db: Any, tenant_id: UUID, import_id: UUID
) -> tuple[CampaignImport, Workflow | None] | None:
    """Lock cleanup in the same order as manager workflow requests.

    Manager routes first serialize an admin-chat session and then access its
    import row. Retention follows ``session -> workflow -> import`` too. This
    prevents a poll from re-projecting a source card just after cleanup has
    removed it, without holding a database lock during object-storage I/O.
    """

    candidate = await get_campaign_import(db, tenant_id=tenant_id, import_id=import_id)
    if candidate is None:
        return None
    workflow = await db.scalar(
        select(Workflow).where(
            Workflow.tenant_id == tenant_id,
            Workflow.id == candidate.workflow_id,
        )
    )
    if workflow is not None:
        # This is the same parent session lock used by upload, polling and
        # workflow actions. Fetching the workflow once without a lock only
        # gives us its parent id; both mutable records are re-read below.
        await db.scalar(
            select(AdminChatSession)
            .where(
                AdminChatSession.tenant_id == tenant_id,
                AdminChatSession.id == workflow.session_id,
            )
            .with_for_update()
        )
        workflow = await db.scalar(
            select(Workflow)
            .where(Workflow.tenant_id == tenant_id, Workflow.id == candidate.workflow_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    imported = await get_campaign_import(
        db, tenant_id=tenant_id, import_id=import_id, lock=True
    )
    if imported is None or imported.expires_at > datetime.now(UTC):
        return None
    return imported, workflow


async def _dispatch_queued_campaign_imports() -> dict[str, Any]:
    processed = 0
    recovered_stale_parsing = 0
    stale_before = datetime.now(UTC) - PARSING_RECOVERY_AFTER
    for tenant_id in await _tenant_ids(active_only=True):
        async with session_scope(tenant_id) as db:
            recoverable = list(
                (
                    await db.scalars(
                        select(CampaignImport)
                        .where(
                            CampaignImport.tenant_id == tenant_id,
                            or_(
                                CampaignImport.status == CampaignImportStatus.QUEUED.value,
                                and_(
                                    CampaignImport.status == CampaignImportStatus.PARSING.value,
                                    CampaignImport.updated_at <= stale_before,
                                ),
                            ),
                        )
                        .order_by(CampaignImport.created_at)
                        .limit(5)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            ids = []
            for imported in recoverable:
                if imported.status == CampaignImportStatus.PARSING.value:
                    # Requeue before invoking the idempotent parser. If this
                    # process dies here, the next beat sees a durable queued
                    # row rather than leaving the UI in "parsing" forever.
                    # Clearing the lease makes any old parser's final write a
                    # no-op rather than a late overwrite of this retry.
                    imported.status = CampaignImportStatus.QUEUED.value
                    imported.parse_token = None
                    imported.failure_reason = None
                    recovered_stale_parsing += 1
                ids.append(imported.id)
            await db.commit()
        for import_id in ids:
            await _parse_campaign_import(tenant_id, import_id)
            processed += 1
    return {"processed": processed, "recovered_stale_parsing": recovered_stale_parsing}


@celery_app.task(name="src.workers.campaign_imports.cleanup_expired_campaign_imports")
def cleanup_expired_campaign_imports() -> dict[str, Any]:
    return run_async(_cleanup_expired_campaign_imports())


async def _cleanup_expired_campaign_imports() -> dict[str, Any]:
    removed = 0
    try:
        storage = get_campaign_import_storage()
    except CampaignImportStorageError:
        logger.warning("campaign_import_cleanup_storage_unconfigured")
        storage = None
    # Retention applies even after a tenant is suspended; otherwise private
    # phone data could live indefinitely merely because delivery was disabled.
    for tenant_id in await _tenant_ids(active_only=False):
        # Work in small committed batches, but drain every currently expired
        # import in this run. A fixed single ``LIMIT 100`` would silently keep
        # the 101st raw phone file for another day in a busy tenant.
        while True:
            async with session_scope(tenant_id) as db:
                expired = list(
                    (
                        await db.scalars(
                            select(CampaignImport)
                            .where(
                                CampaignImport.tenant_id == tenant_id,
                                CampaignImport.expires_at <= datetime.now(UTC),
                            )
                            .order_by(CampaignImport.expires_at)
                            .limit(100)
                        )
                    ).all()
                )
                if not expired:
                    break
                for candidate in expired:
                    # The object key is immutable and expiry can only move in
                    # one direction. Deleting it before taking chat locks
                    # avoids holding a manager's workflow open on MinIO I/O.
                    if storage is not None:
                        try:
                            await storage.delete(candidate.object_key)
                        except CampaignImportStorageError:
                            # The dedicated bucket's lifecycle policy was applied
                            # at upload time. Do not retain import-row E.164 data
                            # merely because an immediate object-delete retry is
                            # unavailable; storage expiry provides the second
                            # independent cleanup path for that object.
                            logger.warning(
                                "campaign_import_cleanup_storage_failed", import_id=str(candidate.id)
                            )
                    locked = await _locked_expired_import_for_cleanup(db, tenant_id, candidate.id)
                    if locked is None:
                        continue
                    imported, workflow = locked
                    await _scrub_expired_import_replicas(db, imported, workflow=workflow)
                    await db.delete(imported)
                    removed += 1
                await db.commit()
    return {"removed": removed, "storage_available": storage is not None}
