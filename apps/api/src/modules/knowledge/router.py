# ruff: noqa: RUF001
"""Tenant self-service knowledge API: sources, documents, candidates, media."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import UUID

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select

from src.core.config import get_settings
from src.core.db import session_scope
from src.core.deps import DBSessionDep
from src.core.errors import ConflictError, NotFoundError
from src.core.rbac import RequireManager, Role, role_at_least
from src.modules.agents.models import AgentVersion, AgentVersionStatus
from src.modules.agents.service import AgentService
from src.modules.compliance.models import AuditLog

from .crawler import normalize_url
from .extract import UnsupportedDocumentError, sniff_mime
from .models import (
    KnowledgeCandidate,
    KnowledgeDocument,
    KnowledgeMedia,
    KnowledgeSource,
)
from .publisher import KnowledgePublisher, public_media_url
from .schemas import (
    CandidateDecisionIn,
    CandidateOut,
    DocumentOut,
    MediaDecisionIn,
    MediaOut,
    SourceCreateIn,
    SourceOut,
    SourceUpdateIn,
    SummaryOut,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/knowledge", tags=["knowledge"])
public_router = APIRouter(tags=["knowledge-media"])


def _ids(claims: dict[str, Any]) -> tuple[UUID, UUID]:
    return UUID(claims["tid"]), UUID(claims["sub"])


def _enqueue_sync(tenant_id: UUID, source_id: UUID) -> bool:
    try:
        from src.workers.knowledge import sync_knowledge_source

        sync_knowledge_source.delay(str(tenant_id), str(source_id))
        return True
    except Exception as exc:  # broker down: the admin can retry "sync now"
        logger.warning("knowledge.sync.enqueue_failed", error=type(exc).__name__)
        return False


async def _source(db: DBSessionDep, tenant_id: UUID, source_id: UUID) -> KnowledgeSource:
    source = await db.get(KnowledgeSource, source_id)
    if source is None or source.tenant_id != tenant_id:
        raise NotFoundError("Knowledge source")
    return source


def _source_out(source: KnowledgeSource) -> SourceOut:
    return SourceOut.model_validate(source)


def _media_out(tenant_id: UUID, media: KnowledgeMedia) -> MediaOut:
    out = MediaOut.model_validate(media)
    out.public_url = (
        public_media_url(get_settings(), tenant_id, media) if media.status == "published" else None
    )
    return out


# --- sources ---------------------------------------------------------------------------


@router.post("/sources", status_code=201)
async def create_source(
    payload: SourceCreateIn, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, uid = _ids(claims)
    await AgentService(db).get_agent(tid, payload.agent_id)
    parsed = urlparse(payload.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, "Geçerli bir web adresi girin (https://...).")
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith((".local", ".internal")):
        raise HTTPException(422, "Yalnızca herkese açık web siteleri taranabilir.")
    canonical = normalize_url(payload.url)
    existing = (
        await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.tenant_id == tid,
                KnowledgeSource.agent_id == payload.agent_id,
                KnowledgeSource.canonical_uri == canonical,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Bu web sitesi zaten kayıtlı")
    source = KnowledgeSource(
        tenant_id=tid,
        agent_id=payload.agent_id,
        kind="website",
        display_name=(payload.display_name or parsed.netloc)[:180],
        canonical_uri=canonical,
        sync_policy=payload.sync_policy,
        auto_publish=payload.auto_publish,
        status="queued" if payload.sync_now else "idle",
        settings={"max_pages": payload.max_pages} if payload.max_pages else {},
    )
    db.add(source)
    await db.flush()
    db.add(
        AuditLog(
            tenant_id=tid,
            actor_id=uid,
            action="knowledge_source_create",
            entity="knowledge_source",
            entity_id=str(source.id),
            meta={"kind": "website", "uri": canonical, "agent_id": str(payload.agent_id)},
        )
    )
    await db.commit()
    queued = _enqueue_sync(tid, source.id) if payload.sync_now else False
    return {**_source_out(source).model_dump(mode="json"), "queued": queued}


@router.post("/documents", status_code=201)
async def upload_document(
    db: DBSessionDep,
    claims: RequireManager,
    agent_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
    auto_publish: Annotated[bool, Form()] = True,
    sync_now: Annotated[bool, Form()] = True,
) -> dict[str, Any]:
    tid, uid = _ids(claims)
    settings = get_settings()
    await AgentService(db).get_agent(tid, agent_id)
    data = await file.read(settings.knowledge_document_max_bytes + 1)
    if len(data) > settings.knowledge_document_max_bytes:
        raise HTTPException(413, "Dosya çok büyük.")
    if not data:
        raise HTTPException(422, "Boş dosya.")
    filename = (file.filename or "dosya").replace("\\", "/").split("/")[-1][:180]
    try:
        mime = sniff_mime(filename, file.content_type, data)
    except UnsupportedDocumentError as exc:
        raise HTTPException(422, str(exc)) from exc
    digest = hashlib.sha256(data).hexdigest()
    source = KnowledgeSource(
        tenant_id=tid,
        agent_id=agent_id,
        kind="document",
        display_name=filename,
        canonical_uri=f"upload://{digest}",
        auto_publish=auto_publish,
        status="queued" if sync_now else "idle",
    )
    existing = (
        await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.tenant_id == tid,
                KnowledgeSource.agent_id == agent_id,
                KnowledgeSource.canonical_uri == source.canonical_uri,
            )
        )
    ).scalar_one_or_none()
    created = existing is None
    if existing is not None:
        source = existing
    else:
        db.add(source)
        await db.flush()
        db.add(
            KnowledgeDocument(
                tenant_id=tid,
                source_id=source.id,
                filename=filename,
                mime_type=mime,
                sha256=digest,
                size_bytes=len(data),
                content=data,
            )
        )
        db.add(
            AuditLog(
                tenant_id=tid,
                actor_id=uid,
                action="knowledge_document_upload",
                entity="knowledge_source",
                entity_id=str(source.id),
                meta={
                    "filename": filename,
                    "mime": mime,
                    "size": len(data),
                    "agent_id": str(agent_id),
                },
            )
        )
    await db.commit()
    queued = _enqueue_sync(tid, source.id) if (sync_now and created) else False
    return {**_source_out(source).model_dump(mode="json"), "created": created, "queued": queued}


@router.get("/sources")
async def list_sources(
    db: DBSessionDep, claims: RequireManager, agent_id: Annotated[UUID, Query()]
) -> list[SourceOut]:
    tid, _ = _ids(claims)
    rows = (
        await db.execute(
            select(KnowledgeSource)
            .where(KnowledgeSource.tenant_id == tid, KnowledgeSource.agent_id == agent_id)
            .order_by(KnowledgeSource.created_at.desc())
        )
    ).scalars()
    return [_source_out(row) for row in rows]


@router.get("/sources/{source_id}")
async def get_source(source_id: UUID, db: DBSessionDep, claims: RequireManager) -> dict[str, Any]:
    tid, _ = _ids(claims)
    source = await _source(db, tid, source_id)
    documents = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.source_id == source.id))
    ).scalars()
    counts: dict[str, int] = {
        str(status): int(total)
        for status, total in (
            await db.execute(
                select(KnowledgeCandidate.review_status, func.count())
                .where(KnowledgeCandidate.source_id == source.id)
                .group_by(KnowledgeCandidate.review_status)
            )
        ).all()
    }
    return {
        **_source_out(source).model_dump(mode="json"),
        "documents": [DocumentOut.model_validate(d).model_dump(mode="json") for d in documents],
        "candidate_counts": counts,
    }


@router.patch("/sources/{source_id}")
async def update_source(
    source_id: UUID, payload: SourceUpdateIn, db: DBSessionDep, claims: RequireManager
) -> SourceOut:
    tid, _ = _ids(claims)
    source = await _source(db, tid, source_id)
    for field_name, value in payload.model_dump(exclude_none=True).items():
        setattr(source, field_name, value)
    await db.commit()
    return _source_out(source)


@router.post("/sources/{source_id}/sync")
async def sync_source(source_id: UUID, db: DBSessionDep, claims: RequireManager) -> dict[str, Any]:
    tid, _ = _ids(claims)
    source = await _source(db, tid, source_id)
    if source.status == "running":
        raise ConflictError("Senkronizasyon zaten çalışıyor")
    source.status = "queued"
    for document in (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.source_id == source.id))
    ).scalars():
        if document.status == "failed":
            document.status = "pending"
    await db.commit()
    return {"queued": _enqueue_sync(tid, source.id), "status": source.status}


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: UUID, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, uid = _ids(claims)
    source = await _source(db, tid, source_id)
    report = await KnowledgePublisher(db).revoke_source(
        tid, source, actor_id=uid, reason="source deleted"
    )
    from src.modules.knowledge.service import build_evidence_graph

    graph = build_evidence_graph()
    if graph is not None:
        try:
            await graph.delete_source(tid, source.id)
        except Exception as exc:
            logger.warning("knowledge.graph.delete_failed", error=type(exc).__name__)
    await db.delete(source)
    await db.commit()
    return {"deleted": True, **report.as_dict()}


# --- candidates ---------------------------------------------------------------------


@router.get("/candidates")
async def list_candidates(
    db: DBSessionDep,
    claims: RequireManager,
    agent_id: Annotated[UUID, Query()],
    status: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    source_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CandidateOut]:
    tid, _ = _ids(claims)
    query = (
        select(KnowledgeCandidate)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeCandidate.source_id)
        .where(KnowledgeCandidate.tenant_id == tid, KnowledgeSource.agent_id == agent_id)
    )
    if status:
        query = query.where(KnowledgeCandidate.review_status == status)
    if kind:
        query = query.where(KnowledgeCandidate.kind == kind)
    if source_id:
        query = query.where(KnowledgeCandidate.source_id == source_id)
    rows = (
        await db.execute(query.order_by(KnowledgeCandidate.created_at.desc()).limit(limit))
    ).scalars()
    return [CandidateOut.model_validate(row) for row in rows]


async def _candidate(db: DBSessionDep, tenant_id: UUID, candidate_id: UUID) -> KnowledgeCandidate:
    candidate = await db.get(KnowledgeCandidate, candidate_id)
    if candidate is None or candidate.tenant_id != tenant_id:
        raise NotFoundError("Knowledge candidate")
    return candidate


@router.post("/candidates/{candidate_id}/accept")
async def accept_candidate(
    candidate_id: UUID, payload: CandidateDecisionIn, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, uid = _ids(claims)
    candidate = await _candidate(db, tid, candidate_id)
    if candidate.review_status in {"revoked", "rejected"}:
        raise ConflictError("Bu aday geri alınmış veya reddedilmiş")
    if payload.customer_text:
        candidate.payload = {**candidate.payload, "customer_text": payload.customer_text.strip()}
    if payload.subject_id:
        candidate.subject_id = payload.subject_id
    if payload.category:
        candidate.category = payload.category
    force: set[UUID] = set()
    if payload.customer_visible is not False:
        if candidate.protected and not role_at_least(claims.get("role"), Role.TENANT_OWNER):
            raise HTTPException(
                403,
                "Korumalı konular (fiyat, stok, teslimat, garanti...) yalnızca şirket sahibi tarafından görünür yapılabilir.",
            )
        force.add(candidate.id)
    candidate.review_status = "accepted"
    await db.commit()
    source = await _source(db, tid, candidate.source_id)
    ids = {candidate.id}
    if candidate.kind == "offering" and candidate.subject_id:
        # Facts extracted for this product waited for the catalogue decision.
        waiting = (
            await db.execute(
                select(KnowledgeCandidate.id).where(
                    KnowledgeCandidate.tenant_id == tid,
                    KnowledgeCandidate.kind == "fact",
                    KnowledgeCandidate.subject_id == candidate.subject_id,
                    KnowledgeCandidate.review_status == "pending",
                )
            )
        ).scalars()
        ids.update(waiting)
    report = await KnowledgePublisher(db).publish_pending(
        tid, source.agent_id, candidate_ids=ids, force_visible=force, actor_id=uid
    )
    return report.as_dict()


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: UUID, payload: CandidateDecisionIn, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, uid = _ids(claims)
    candidate = await _candidate(db, tid, candidate_id)
    if candidate.published_ref:
        report = await KnowledgePublisher(db).revoke_candidate(
            tid, candidate, actor_id=uid, reason=payload.reason or "rejected by administrator"
        )
        candidate.review_status = "rejected"
        await db.commit()
        return report.as_dict()
    candidate.review_status = "rejected"
    candidate.error = (payload.reason or "rejected by administrator")[:240]
    await db.commit()
    return {"revoked": [], "skipped": {}, "live_version_id": None, "draft_updated": False}


@router.post("/candidates/{candidate_id}/revoke")
async def revoke_candidate(
    candidate_id: UUID, payload: CandidateDecisionIn, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, uid = _ids(claims)
    candidate = await _candidate(db, tid, candidate_id)
    report = await KnowledgePublisher(db).revoke_candidate(
        tid, candidate, actor_id=uid, reason=payload.reason or "revoked by administrator"
    )
    return report.as_dict()


# --- media -----------------------------------------------------------------------------


@router.get("/media")
async def list_media(
    db: DBSessionDep,
    claims: RequireManager,
    agent_id: Annotated[UUID, Query()],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MediaOut]:
    tid, _ = _ids(claims)
    query = (
        select(KnowledgeMedia)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeMedia.source_id)
        .where(KnowledgeMedia.tenant_id == tid, KnowledgeSource.agent_id == agent_id)
    )
    if status:
        query = query.where(KnowledgeMedia.status == status)
    rows = (await db.execute(query.order_by(KnowledgeMedia.score.desc()).limit(limit))).scalars()
    return [_media_out(tid, row) for row in rows]


async def _media(db: DBSessionDep, tenant_id: UUID, media_id: UUID) -> KnowledgeMedia:
    media = await db.get(KnowledgeMedia, media_id)
    if media is None or media.tenant_id != tenant_id:
        raise NotFoundError("Knowledge media")
    return media


@router.get("/media/{media_id}/preview")
async def preview_media(media_id: UUID, db: DBSessionDep, claims: RequireManager) -> Response:
    tid, _ = _ids(claims)
    media = await _media(db, tid, media_id)
    content = await db.run_sync(lambda s: media.content)
    return Response(
        content,
        media_type=media.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/media/{media_id}/accept")
async def accept_media(
    media_id: UUID, payload: MediaDecisionIn, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, uid = _ids(claims)
    media = await _media(db, tid, media_id)
    if payload.subject_id:
        media.subject_id = payload.subject_id
    if not media.subject_id:
        raise HTTPException(422, "Görselin hangi ürüne ait olduğunu seçin.")
    media.status = "accepted"
    await db.commit()
    source = await _source(db, tid, media.source_id)
    report = await KnowledgePublisher(db).publish_pending(
        tid,
        source.agent_id,
        candidate_ids=set(),
        media_ids={media.id},
        force_visible={media.id},
        actor_id=uid,
    )
    return report.as_dict()


@router.post("/media/{media_id}/revoke")
async def revoke_media(
    media_id: UUID, payload: MediaDecisionIn, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, uid = _ids(claims)
    media = await _media(db, tid, media_id)
    report = await KnowledgePublisher(db).revoke_media(
        tid, media, actor_id=uid, reason=payload.reason or "revoked by administrator"
    )
    return report.as_dict()


# --- summary ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/summary")
async def summary(agent_id: UUID, db: DBSessionDep, claims: RequireManager) -> SummaryOut:
    tid, _ = _ids(claims)
    settings = get_settings()
    source_ids = list(
        (
            await db.execute(
                select(KnowledgeSource.id).where(
                    KnowledgeSource.tenant_id == tid, KnowledgeSource.agent_id == agent_id
                )
            )
        ).scalars()
    )
    candidates: dict[str, int] = {}
    media: dict[str, int] = {}
    documents = 0
    if source_ids:
        candidates = {
            str(k): int(v)
            for k, v in (
                await db.execute(
                    select(KnowledgeCandidate.review_status, func.count())
                    .where(KnowledgeCandidate.source_id.in_(source_ids))
                    .group_by(KnowledgeCandidate.review_status)
                )
            ).all()
        }
        media = {
            str(k): int(v)
            for k, v in (
                await db.execute(
                    select(KnowledgeMedia.status, func.count())
                    .where(KnowledgeMedia.source_id.in_(source_ids))
                    .group_by(KnowledgeMedia.status)
                )
            ).all()
        }
        documents = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(KnowledgeDocument)
                    .where(KnowledgeDocument.source_id.in_(source_ids))
                )
            ).scalar_one()
        )
    versions: dict[AgentVersionStatus, int] = dict(
        (
            await db.execute(
                select(AgentVersion.status, AgentVersion.version).where(
                    AgentVersion.tenant_id == tid,
                    AgentVersion.agent_id == agent_id,
                    AgentVersion.status.in_([AgentVersionStatus.LIVE, AgentVersionStatus.DRAFT]),
                )
            )
        ).all()  # type: ignore[arg-type]
    )
    return SummaryOut(
        agent_id=agent_id,
        sources=len(source_ids),
        documents=documents,
        candidates=candidates,
        media=media,
        live_version=versions.get(AgentVersionStatus.LIVE),
        draft_version=versions.get(AgentVersionStatus.DRAFT),
        auto_publish=settings.knowledge_auto_publish,
        threshold=settings.knowledge_auto_publish_threshold,
    )


# --- public media (Meta fetches session images from here) -----------------------------


@public_router.get("/media/k/{tenant_hex}/{filename}")
async def public_media(tenant_hex: str, filename: str) -> Response:
    try:
        tenant_id = UUID(hex=tenant_hex)
    except ValueError as exc:
        raise HTTPException(404, "Not found") from exc
    sha, _, extension = filename.partition(".")
    if len(sha) != 64 or extension not in {"jpg", "png"}:
        raise HTTPException(404, "Not found")
    async with session_scope(tenant_id) as session:
        media = (
            await session.execute(
                select(KnowledgeMedia).where(
                    KnowledgeMedia.tenant_id == tenant_id,
                    KnowledgeMedia.sha256 == sha,
                    KnowledgeMedia.status == "published",
                )
            )
        ).scalar_one_or_none()
        if media is None:
            raise HTTPException(404, "Not found")
        content = await session.run_sync(lambda s: media.content)
        mime = media.mime_type
    return Response(
        content,
        media_type=mime,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
