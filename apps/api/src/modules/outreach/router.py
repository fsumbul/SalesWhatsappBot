"""Outreach HTTP routers."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from src.core.deps import ClaimsDep, DBSessionDep
from src.core.rbac import RequireAgent, RequireManager

from .schemas import (
    ConversationOut,
    MessageOut,
    MessageSendIn,
    OutreachEnqueueIn,
    OutreachJobOut,
    SenderIn,
    SenderOut,
    SenderPatchIn,
    TemplateIn,
    TemplateOut,
    TemplatePatchIn,
)
from .service import (
    ConversationService,
    OutreachService,
    SenderService,
    TemplateService,
)

templates_router = APIRouter(prefix="/templates", tags=["templates"])
senders_router = APIRouter(prefix="/senders", tags=["senders"])
outreach_router = APIRouter(prefix="/outreach", tags=["outreach"])
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


def _tid(claims: dict[str, Any]) -> UUID:
    return UUID(claims["tid"])


# --- Templates ---


@templates_router.get("", response_model=list[TemplateOut])
async def list_templates(db: DBSessionDep, claims: ClaimsDep) -> list[TemplateOut]:
    items = await TemplateService(db).list(_tid(claims))
    return [TemplateOut.model_validate(t) for t in items]


@templates_router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateIn, db: DBSessionDep, claims: RequireManager
) -> TemplateOut:
    t = await TemplateService(db).create(_tid(claims), payload)
    return TemplateOut.model_validate(t)


@templates_router.patch("/{template_id}", response_model=TemplateOut)
async def patch_template(
    template_id: UUID,
    payload: TemplatePatchIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> TemplateOut:
    t = await TemplateService(db).patch(_tid(claims), template_id, payload)
    return TemplateOut.model_validate(t)


# --- Senders ---


@senders_router.get("", response_model=list[SenderOut])
async def list_senders(db: DBSessionDep, claims: ClaimsDep) -> list[SenderOut]:
    items = await SenderService(db).list(_tid(claims))
    return [SenderOut.model_validate(s) for s in items]


@senders_router.post("", response_model=SenderOut, status_code=status.HTTP_201_CREATED)
async def create_sender(
    payload: SenderIn, db: DBSessionDep, claims: RequireManager
) -> SenderOut:
    s = await SenderService(db).create(_tid(claims), payload)
    return SenderOut.model_validate(s)


@senders_router.patch("/{sender_id}", response_model=SenderOut)
async def patch_sender(
    sender_id: UUID,
    payload: SenderPatchIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> SenderOut:
    s = await SenderService(db).patch(_tid(claims), sender_id, payload)
    return SenderOut.model_validate(s)


# --- Outreach jobs ---


@outreach_router.post(
    "/enqueue", response_model=list[OutreachJobOut], status_code=status.HTTP_202_ACCEPTED
)
async def enqueue_outreach(
    payload: OutreachEnqueueIn, db: DBSessionDep, claims: RequireManager
) -> list[OutreachJobOut]:
    jobs = await OutreachService(db).enqueue(_tid(claims), payload)
    return [OutreachJobOut.model_validate(j) for j in jobs]


@outreach_router.get("/jobs", response_model=list[OutreachJobOut])
async def list_jobs(db: DBSessionDep, claims: ClaimsDep) -> list[OutreachJobOut]:
    items = await OutreachService(db).list_jobs(_tid(claims))
    return [OutreachJobOut.model_validate(j) for j in items]


# --- Conversations / Inbox ---


@conversations_router.get("", response_model=list[ConversationOut])
async def list_conversations(db: DBSessionDep, claims: ClaimsDep) -> list[ConversationOut]:
    items = await ConversationService(db).list_conversations(_tid(claims))
    return [ConversationOut.model_validate(c) for c in items]


@conversations_router.get("/{conv_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conv_id: UUID, db: DBSessionDep, claims: ClaimsDep
) -> list[MessageOut]:
    items = await ConversationService(db).messages(_tid(claims), conv_id)
    return [MessageOut.model_validate(m) for m in items]


@conversations_router.post(
    "/{conv_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED
)
async def send_message(
    conv_id: UUID,
    payload: MessageSendIn,
    db: DBSessionDep,
    claims: RequireAgent,
) -> MessageOut:
    msg = await ConversationService(db).send_free_form(_tid(claims), conv_id, payload.body)
    return MessageOut.model_validate(msg)
