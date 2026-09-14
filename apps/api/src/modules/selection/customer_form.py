"""Customer-scoped editing of the same request used by the WhatsApp reducer."""

import base64
import hashlib
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import set_tenant_context
from src.core.deps import DBSessionDep
from src.modules.auth.models import Tenant, TenantStatus, User, UserRole
from src.modules.outreach.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    MessageType,
)

from .access import form_claims
from .engine import advance, choices, parse_value, visible
from .models import SelectionFile, SelectionFormAction, SelectionRequest
from .service import state_of

router = APIRouter(prefix="/customer-form", tags=["customer form"])


class Upload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=150)
    mime: Literal["application/pdf", "image/jpeg", "image/png"]
    data: str = Field(max_length=7_000_000)


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    operation_id: UUID
    action: Literal["save", "confirm", "upload"] = "save"
    fields: dict[str, str] = Field(default_factory=dict, max_length=50)
    file: Upload | None = None


async def load(db: AsyncSession, token: str, lock: bool = False) -> SelectionRequest:
    tenant_id, request_id = form_claims(token)
    await set_tenant_context(db, tenant_id)
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(404, "Form bulunamadı.")
    stmt = select(SelectionRequest).where(
        SelectionRequest.id == request_id, SelectionRequest.tenant_id == tenant_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(stmt)
    if row is None:
        raise HTTPException(404, "Form bulunamadı.")
    return row


async def view(db: Any, row: SelectionRequest) -> dict[str, Any]:
    state = state_of(row)
    files = list(
        (await db.scalars(select(SelectionFile).where(SelectionFile.request_id == row.id))).all()
    )
    return {
        "id": str(row.id),
        "title": state.definition.title,
        "revision": row.revision,
        "status": row.status,
        "answers": row.answers,
        "fields": [
            {
                **s.model_dump(),
                "choices": [
                    {"value": v, "label": label} for v, label in choices(s) if v != "@custom"
                ],
                "visible": visible(state, i),
            }
            for i, s in enumerate(state.definition.steps)
        ],
        "files": [{"id": str(f.id), "name": f.filename, "size": f.size_bytes} for f in files],
        "ready": state.step_index >= len(state.definition.steps),
        "current_step": state.step_index,
    }


@router.get("")
async def get_form(db: DBSessionDep, x_form_token: str = Header()) -> Any:
    return await view(db, await load(db, x_form_token))


@router.post("")
async def update_form(payload: Change, db: DBSessionDep, x_form_token: str = Header()) -> Any:
    row = await load(db, x_form_token, True)
    body = payload.model_dump(mode="json")
    if payload.file:
        body["file"] = {
            **body["file"],
            "data": hashlib.sha256(payload.file.data.encode()).hexdigest(),
        }
    prior = await db.scalar(
        select(SelectionFormAction).where(
            SelectionFormAction.request_id == row.id,
            SelectionFormAction.operation_id == payload.operation_id,
        )
    )
    if prior:
        if prior.payload != body:
            raise HTTPException(409, "Aynı işlem kimliği farklı bilgilerle kullanılamaz.")
        return prior.response
    if row.revision != payload.revision:
        raise HTTPException(
            409,
            {
                "message": "Talep WhatsApp veya başka bir ekranda değişti. Güncel bilgileri inceleyin.",
                "form": await view(db, row),
            },
        )
    if row.status != "draft":
        raise HTTPException(409, "Onaylanmış veya kapatılmış talep değiştirilemez.")
    if payload.file is not None and payload.action != "upload":
        raise HTTPException(422, "Dosyayı yükleme işlemiyle gönderin.")
    state = state_of(row)
    known = {s.id for s in state.definition.steps}
    if set(payload.fields) - known:
        raise HTTPException(422, "Bilinmeyen form alanı.")
    if payload.action == "upload":
        if payload.file is None or payload.fields:
            raise HTTPException(422, "Dosya gerekli.")
        files = list(
            (
                await db.scalars(select(SelectionFile).where(SelectionFile.request_id == row.id))
            ).all()
        )
        if len(files) >= 10:
            raise HTTPException(422, "En fazla 10 dosya eklenebilir.")
        try:
            data = base64.b64decode(payload.file.data, validate=True)
        except ValueError as e:
            raise HTTPException(422, "Dosya okunamadı.") from e
        signature = {
            "application/pdf": b"%PDF-",
            "image/jpeg": b"\xff\xd8\xff",
            "image/png": b"\x89PNG\r\n\x1a\n",
        }[payload.file.mime]
        if len(data) > 5 * 1024 * 1024 or not data.startswith(signature):
            raise HTTPException(422, "En fazla 5 MB PDF, JPEG veya PNG yükleyin.")
        name = payload.file.name.replace("\\", "/").split("/")[-1]
        db.add(
            SelectionFile(
                tenant_id=row.tenant_id,
                request_id=row.id,
                inbound_message_id=None,
                filename=name,
                mime_type=payload.file.mime,
                sha256=hashlib.sha256(data).hexdigest(),
                content=data,
                size_bytes=len(data),
            )
        )
    else:
        errors = {}
        for i, step in enumerate(state.definition.steps):
            if not visible(state, i):
                state.answers.pop(step.id, None)
                continue
            if step.id not in payload.fields:
                continue
            raw = payload.fields[step.id]
            answer = parse_value(step, raw) if raw.strip() else None
            if raw.strip() and answer is None:
                errors[step.id] = "Geçerli bir değer seçin veya yazın."
                continue
            if answer != state.answers.get(step.id):
                for later in state.definition.steps[i + 1 :]:
                    state.answers.pop(later.id, None)
            if answer is None:
                state.answers.pop(step.id, None)
            else:
                state.answers[step.id] = answer
        if errors:
            raise HTTPException(422, {"errors": errors})
        state.step_index = 0
        advance(state)
        files = list(
            (
                await db.scalars(select(SelectionFile).where(SelectionFile.request_id == row.id))
            ).all()
        )
        if state.answers.get("drawing", {}).get("value") == "done" and not files:
            raise HTTPException(
                422, {"errors": {"drawing": "Önce dosya ekleyin veya manuel devam seçin."}}
            )
        if payload.action == "confirm":
            if payload.fields:
                raise HTTPException(422, "Önce bilgileri kaydedip inceleyin.")
            if state.step_index < len(state.definition.steps):
                raise HTTPException(422, "Eksik alanları tamamlayın.")
            conversation = await db.get(Conversation, row.conversation_id)
            if conversation is not None:
                conversation.status = ConversationStatus.OPEN
                if conversation.assigned_to is None:
                    conversation.assigned_to = await db.scalar(
                        select(User.id)
                        .where(User.tenant_id == row.tenant_id, User.is_active.is_(True))
                        .order_by(
                            case(
                                (User.role == UserRole.TENANT_OWNER, 0),
                                (User.role == UserRole.SALES_MANAGER, 1),
                                (User.role == UserRole.SALES_AGENT, 2),
                                else_=3,
                            ),
                            User.created_at.asc(),
                        )
                        .limit(1)
                    )
                row.assigned_to = conversation.assigned_to
                db.add(
                    Message(
                        tenant_id=row.tenant_id,
                        conversation_id=conversation.id,
                        direction=MessageDirection.OUTBOUND,
                        message_type=MessageType.SYSTEM,
                        body="Müşteri formdaki bilgilerini onayladı; teknik/satış incelemesi bekliyor.",
                        raw={
                            "internal_only": True,
                            "selection_request_id": str(row.id),
                            "source": "customer_form",
                        },
                    )
                )
            state.status = "waiting_review"
            row.confirmed_at = datetime.now(UTC)
            row.confirmed_snapshot = {
                "flow_id": state.definition.id,
                "version": state.definition.version,
                "answers": state.answers,
                "fields": [
                    {"id": s.id, "label": s.label}
                    for s in state.definition.steps
                    if s.id in state.answers
                ],
                "files": [
                    {
                        "id": str(f.id),
                        "filename": f.filename,
                        "sha256": f.sha256,
                        "size_bytes": f.size_bytes,
                    }
                    for f in files
                ],
            }
    row.answers = state.answers
    row.step_index = state.step_index
    row.status = state.status
    row.revision += 1
    await db.flush()
    result = await view(db, row)
    db.add(
        SelectionFormAction(
            tenant_id=row.tenant_id,
            request_id=row.id,
            operation_id=payload.operation_id,
            payload=body,
            response=result,
        )
    )
    await db.commit()
    return result
