"""Shared authorization and review mutation rules for panel and admin tools."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.core.db import set_tenant_context
from src.modules.auth.models import User

from .models import SelectionRequest

ROLES = {"super_admin", "tenant_owner", "sales_manager", "sales_agent"}


async def authorize(db: Any, claims: Any) -> Any:
    if not claims.get("tid"):
        raise HTTPException(403, "Tenant required")
    await set_tenant_context(db, UUID(claims["tid"]))
    user = await db.get(User, UUID(claims["sub"]))
    if (
        user is None
        or not user.is_active
        or user.tenant_id != UUID(claims["tid"])
        or user.role.value not in ROLES
    ):
        raise HTTPException(403, "Active review account required")
    return user


def public(row: Any) -> Any:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "status": row.status,
        "revision": row.revision,
        "created_at": row.created_at,
        "confirmed_at": row.confirmed_at,
        "assigned_to": row.assigned_to,
        "answers": row.answers,
        "snapshot": row.confirmed_snapshot,
        "internal_notes": row.internal_notes,
        "fields": [{"id": s["id"], "label": s["label"]} for s in row.definition["steps"]],
    }


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    status: Literal["waiting_review", "in_review", "completed", "cancelled"] | None = None
    assigned_to: UUID | None = None
    note: str | None = Field(default=None, min_length=1, max_length=2000)


async def apply_review(db: Any, claims: Any, request_id: UUID, payload: ReviewUpdate) -> Any:
    user = await authorize(db, claims)
    row = await db.scalar(
        select(SelectionRequest)
        .where(SelectionRequest.id == request_id, SelectionRequest.tenant_id == user.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Request not found")
    if row.revision != payload.revision:
        raise HTTPException(409, "Request changed; reload")
    if row.status == "draft":
        raise HTTPException(409, "Customer has not confirmed this request")
    before = (row.status, row.assigned_to)
    allowed = {
        "waiting_review": {"in_review", "cancelled"},
        "in_review": {"waiting_review", "completed", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }
    if payload.status and payload.status != row.status:
        if payload.status not in allowed[row.status]:
            raise HTTPException(409, "Invalid status transition")
        row.status = payload.status
    if "assigned_to" in payload.model_fields_set:
        if payload.assigned_to:
            assignee = await db.get(User, payload.assigned_to)
            if (
                assignee is None
                or not assignee.is_active
                or assignee.tenant_id != user.tenant_id
                or assignee.role.value not in ROLES
            ):
                raise HTTPException(422, "Invalid assignee")
        row.assigned_to = payload.assigned_to
    if not payload.note and before == (row.status, row.assigned_to):
        return row
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "user_id": str(user.id),
        "status": row.status,
        "assigned_to": str(row.assigned_to) if row.assigned_to else None,
    }
    if payload.note:
        entry["text"] = payload.note
    row.internal_notes = [*row.internal_notes, entry]
    row.revision += 1
    await db.flush()
    return row
