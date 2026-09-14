"""Authenticated team review; file bytes never appear in request JSON."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select

from src.core.deps import DBSessionDep
from src.core.rbac import RequireAgent
from src.modules.auth.models import User

from .models import SelectionFile, SelectionRequest
from .review import ROLES, ReviewUpdate, apply_review, authorize, public

router = APIRouter(prefix="/selection-requests", tags=["selection review"])


@router.get("")
async def requests(
    claims: RequireAgent, db: DBSessionDep, offset: int = Query(0, ge=0), status: str | None = None
) -> Any:
    await authorize(db, claims)
    stmt = (
        select(SelectionRequest)
        .order_by(SelectionRequest.created_at.desc())
        .offset(offset)
        .limit(50)
    )
    if status:
        stmt = stmt.where(SelectionRequest.status == status)
    return [public(r) for r in (await db.execute(stmt)).scalars()]


@router.get("/reviewers")
async def reviewers(claims: RequireAgent, db: DBSessionDep) -> Any:
    await authorize(db, claims)
    users = (
        await db.execute(select(User).where(User.is_active.is_(True), User.role.in_(ROLES)))
    ).scalars()
    return [{"id": u.id, "email": u.email} for u in users]


@router.get("/{request_id}")
async def detail(request_id: UUID, claims: RequireAgent, db: DBSessionDep) -> Any:
    await authorize(db, claims)
    row = await db.get(SelectionRequest, request_id)
    if row is None:
        raise HTTPException(404, "Request not found")
    files = (
        await db.execute(select(SelectionFile).where(SelectionFile.request_id == row.id))
    ).scalars()
    return {
        **public(row),
        "files": [
            {
                "id": f.id,
                "filename": f.filename,
                "mime_type": f.mime_type,
                "size_bytes": f.size_bytes,
            }
            for f in files
        ],
    }


@router.patch("/{request_id}")
async def update(
    request_id: UUID, payload: ReviewUpdate, claims: RequireAgent, db: DBSessionDep
) -> Any:
    row = await apply_review(db, claims, request_id, payload)
    await db.commit()
    return public(row)


@router.get("/{request_id}/files/{file_id}")
async def download(request_id: UUID, file_id: UUID, claims: RequireAgent, db: DBSessionDep) -> Any:
    await authorize(db, claims)
    result = (
        await db.execute(
            select(SelectionFile.content, SelectionFile.mime_type).where(
                SelectionFile.id == file_id, SelectionFile.request_id == request_id
            )
        )
    ).one_or_none()
    if result is None:
        raise HTTPException(404, "File not found")
    ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}[result.mime_type]
    return Response(
        result.content,
        media_type=result.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="drawing-{file_id}.{ext}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
