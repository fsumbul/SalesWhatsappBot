# ruff: noqa: RUF001
"""Common inbox cards and durable manual replies over the canonical send boundary."""

from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text

from src.core.errors import ConflictError
from src.core.rbac import Role, role_at_least
from src.modules.discovery.models import Lead, LeadContact
from src.modules.outreach import inbox_control
from src.modules.outreach.models import Conversation, Message, MessageDirection
from src.modules.outreach.service import ConversationService

from .data_scope import day_window, scope
from .models import AdminChatSession
from .workflow_schema import WorkflowField
from .workspace_tools import TurnTransaction

KINDS = {"conversation", "reply", "resume_bot"}
CONTROLS = {
    "conversation": [
        WorkflowField(key="conversation", label="Konuşma", required=True),
        WorkflowField(key="page", label="Sayfa"),
        WorkflowField(key="today", label="Bugün"),
        WorkflowField(key="direction", label="Mesaj yönü"),
    ],
    "reply": [
        WorkflowField(key="conversation", label="Konuşma", required=True),
        WorkflowField(key="content", label="Gönderilecek yanıt", control="textarea", required=True),
    ],
    "resume_bot": [WorkflowField(key="conversation", label="Konuşma", required=True)],
}
DELIVERY = {
    "sending": "Gönderim sonucu bekleniyor",
    "sent": "Gönderildi",
    "delivered": "Teslim edildi",
    "read": "Okundu",
    "failed": "Başarısız",
    "ambiguous": "Sonuç belirsiz",
}


def claims(user: Any) -> dict[str, str]:
    return {"tid": str(user.tenant_id), "sub": str(user.id), "role": user.role.value}


async def own(db: Any, user: Any, value: str) -> Any:
    try:
        cid = UUID(value)
    except ValueError as exc:
        raise HTTPException(422, "Listeden bir konuşma seçin.") from exc
    row = await db.scalar(
        select(Conversation).where(Conversation.id == cid, Conversation.tenant_id == user.tenant_id)
    )
    if row is None:
        raise HTTPException(404, "Konuşma bulunamadı.")
    return row


def controls(row: Any) -> list[WorkflowField]:
    return [control for control in CONTROLS[row.kind] if control.key == "content"]


async def listing(db: Any, user: Any, pattern: str, page: int, *, today: bool = False) -> tuple[int, list[dict[str, Any]]]:
    stmt = (
        select(Conversation, Lead, LeadContact)
        .join(Lead, Lead.id == Conversation.lead_id)
        .join(LeadContact, LeadContact.id == Conversation.contact_id)
        .where(
            Conversation.tenant_id == user.tenant_id,
            Lead.tenant_id == user.tenant_id,
            LeadContact.tenant_id == user.tenant_id,
            or_(
                Lead.person_name.ilike(pattern, escape="\\"),
                Lead.company_name.ilike(pattern, escape="\\"),
                LeadContact.normalized_value.ilike(pattern, escape="\\"),
            ),
        )
    )
    if today:
        start, end = day_window(user)
        stmt = stmt.where(select(Message.id).where(
            Message.tenant_id == user.tenant_id, Message.conversation_id == Conversation.id,
            Message.created_at >= start, Message.created_at < end,
        ).exists())
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = await db.execute(
        stmt.order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id)
        .offset((page - 1) * 20)
        .limit(20)
    )
    records = [
        {
            "id": str(conv.id),
            "title": lead.person_name or lead.company_name or contact.normalized_value,
            "subtitle": contact.normalized_value,
            "details": {"Durum": str(conv.status), "Okunmamış": str(conv.unread_count)},
            "actions": [{"operation": "conversation", "label": "Konuşmayı aç"}],
        }
        for conv, lead, contact in rows
    ]
    return total, records


async def read_messages(
    db: Any, user: Any, *, query: str = "", conversation_id: str | None = None,
    today: bool = False, direction: str | None = None, page: int = 1,
) -> dict[str, Any]:
    """Read a bounded page across conversations, filtering actual message timestamps."""
    from .service import search_text

    if direction not in {None, "inbound", "outbound"}:
        raise HTTPException(422, "Geçerli mesaj yönü seçin.")
    query = search_text(query)
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    stmt = select(Message, Conversation, Lead, LeadContact).join(
        Conversation, Conversation.id == Message.conversation_id,
    ).join(Lead, Lead.id == Conversation.lead_id).join(
        LeadContact, LeadContact.id == Conversation.contact_id,
    ).where(
        Message.tenant_id == user.tenant_id, Conversation.tenant_id == user.tenant_id,
        Lead.tenant_id == user.tenant_id, LeadContact.tenant_id == user.tenant_id,
    )
    subject = None
    if conversation_id:
        conv = await own(db, user, conversation_id)
        contact = await db.get(LeadContact, conv.contact_id)
        lead = await db.get(Lead, conv.lead_id)
        subject = {"conversation_id": str(conv.id), "contact": contact.normalized_value,
                   "name": lead.person_name or lead.company_name}
        stmt = stmt.where(Message.conversation_id == conv.id)
    if query:
        stmt = stmt.where(or_(Lead.person_name.ilike(pattern, escape="\\"),
                              Lead.company_name.ilike(pattern, escape="\\"),
                              LeadContact.normalized_value.ilike(pattern, escape="\\")))
    if today:
        start, end = day_window(user)
        stmt = stmt.where(Message.created_at >= start, Message.created_at < end)
    if direction:
        stmt = stmt.where(Message.direction == direction)
    population = stmt.with_only_columns(
        Message.id.label("message_id"), Conversation.id.label("conversation_id"),
        LeadContact.id.label("contact_id"),
    ).subquery()
    total, contact_count, conversation_count = (await db.execute(select(
        func.count(), func.count(func.distinct(population.c.contact_id)),
        func.count(func.distinct(population.c.conversation_id)),
    ).select_from(population))).one()
    records = []
    for message, conv, lead, contact in await db.execute(
        stmt.order_by(Message.created_at.desc(), Message.id.desc()).offset((page - 1) * 20).limit(20)
    ):
        records.append({
            "id": str(message.id), "conversation": str(conv.id),
            "title": lead.person_name or lead.company_name or contact.normalized_value,
            "subtitle": contact.normalized_value,
            "details": {"Mesaj": message.body or "Medya mesajı",
                        "Zaman": message.created_at.astimezone(ZoneInfo(user.timezone or "Europe/Istanbul")).isoformat(),
                        "Yön": str(message.direction)},
            "actions": [],
        })
    return {
        "category": "messages", "records": records, "total": total,
        "page": page, "has_more": page * 20 < total,
        "scope": scope(user, "messages", query=query, today=today, direction=direction,
                       total=total, page=page, has_more=page * 20 < total, subject=subject,
                       contact_count=contact_count, conversation_count=conversation_count),
    }


async def refresh(db: Any, user: Any, row: Any) -> None:
    conv = await own(db, user, row.fields.get("conversation", ""))
    lead = await db.get(Lead, conv.lead_id)
    contact = await db.get(LeadContact, conv.contact_id)
    state = await inbox_control.read_state(conv.id, db, claims(user))
    row.state = {
        **(row.state or {}),
        "output": {
            "summary": (lead.person_name or lead.company_name or "Müşteri") if lead else "Müşteri",
            "delivery_note": ("Bot bekliyor. " if state["paused"] else "Bot etkin. ")
            + await delivery_summary(db, user, conv.id),
        },
        "bot_state": state,
        "contact": contact.normalized_value if contact else "",
    }
    if row.kind != "conversation":
        return
    try:
        page = max(1, min(10000, int(row.fields.get("page", "1"))))
    except ValueError as exc:
        raise HTTPException(422, "Geçerli bir sayfa seçin.") from exc
    stmt = select(Message).where(
        Message.tenant_id == user.tenant_id, Message.conversation_id == conv.id
    )
    today = row.fields.get("today") == "true"
    direction = row.fields.get("direction") or None
    if direction not in {None, "inbound", "outbound"}:
        raise HTTPException(422, "Geçerli mesaj yönü seçin.")
    if today:
        start, end = day_window(user)
        stmt = stmt.where(Message.created_at >= start, Message.created_at < end)
    if direction:
        stmt = stmt.where(Message.direction == direction)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    messages = list(
        (
            await db.scalars(
                stmt.order_by(Message.created_at.desc(), Message.id)
                .offset((page - 1) * 20)
                .limit(20)
            )
        ).all()
    )
    records = [
        {
            "id": str(message.id),
            "title": "Müşteri"
            if message.direction == MessageDirection.INBOUND
            else "Asistan / ekip",
            "subtitle": message.created_at.astimezone(ZoneInfo(user.timezone or "Europe/Istanbul")).isoformat(),
            "details": {
                "Mesaj": message.body or "Medya mesajı",
                "Durum": DELIVERY.get(
                    message.raw.get("delivery_status"),
                    "Meta kabul etti"
                    if message.raw.get("manual_send_state") == "sent"
                    else DELIVERY.get(message.raw.get("manual_send_state"), "Kayıtlı"),
                ),
            },
            "actions": [],
        }
        for message in reversed(messages)
    ]
    actions = []
    if role_at_least(user.role, Role.SALES_AGENT):
        actions.append({"operation": "reply", "label": "Yanıt hazırla"})
        if state["can_resume"]:
            actions.append({"operation": "resume_bot", "label": "Botu devam ettir"})
    row.state = {
        **row.state,
        "records": records,
        "record_actions": actions,
        "page": page,
        "has_more": page * 20 < total,
        "total": total,
        "scope": scope(user, "messages", today=today, direction=direction, total=total,
                       page=page, has_more=page * 20 < total,
                       subject={"conversation_id": str(conv.id),
                                "contact": contact.normalized_value if contact else "",
                                "name": lead.person_name if lead else ""}),
    }
    row.status, row.step = "awaiting_input", "details"


async def launch(
    db: Any, user: Any, row: Any, fields: dict[str, str]
) -> tuple[str, dict[str, str]]:
    if set(fields) != {"operation"} or fields["operation"] not in {"reply", "resume_bot"}:
        raise HTTPException(422, "Geçerli bir konuşma işlemi seçin.")
    await own(db, user, row.fields["conversation"])
    return fields["operation"], {"conversation": row.fields["conversation"]}


async def prepare(db: Any, user: Any, row: Any) -> None:
    await refresh(db, user, row)
    if row.kind == "resume_bot":
        row.state = {
            **row.state,
            "changes": [
                {
                    "label": "Bot",
                    "before": "Bekliyor" if row.state["bot_state"]["paused"] else "Etkin",
                    "after": "Yeni mesajlar için etkin",
                }
            ],
        }
    else:
        row.state = {
            **row.state,
            "changes": [
                {"label": "Alıcı", "before": "", "after": row.state["contact"]},
                {"label": "Yanıt", "before": "", "after": row.fields["content"]},
            ],
        }


async def resume(db: Any, user: Any, row: Any) -> None:
    conv = await own(db, user, row.fields["conversation"])
    transaction: Any = TurnTransaction(db)
    await inbox_control.resume(conv.id, transaction, claims(user))
    row.result = {
        "outcome": "resumed",
        "message": "Bot yeni müşteri mesajları için devam ettirildi. Eski mesaj yeniden gönderilmedi.",
    }


async def sync(db: Any, user: Any, row: Any) -> None:
    if not row.state.get("message_id"):
        return
    msg = await db.scalar(
        select(Message).where(
            Message.id == UUID(row.state["message_id"]),
            Message.tenant_id == user.tenant_id,
            Message.conversation_id == UUID(row.fields["conversation"]),
        )
    )
    if msg is None:
        raise HTTPException(404, "Gönderim kaydı bulunamadı.")
    delivery = msg.raw.get("delivery_status") or (
        "accepted"
        if msg.raw.get("manual_send_state") == "sent"
        else msg.raw.get("manual_send_state", "sending")
    )
    label = {**DELIVERY, "accepted": "Meta kabul etti; teslimat henüz doğrulanmadı."}.get(
        delivery, delivery
    )
    row.result = {"outcome": delivery, "message_id": str(msg.id), "message": label}
    row.state = {**row.state, "output": {"summary": msg.body or "", "delivery_note": label}}
    if delivery != "sending":
        row.status, row.step = "completed", "result"


async def send(
    db: Any, user: Any, session: Any, row: Any, payload: Any, request: Any
) -> dict[str, Any]:
    from . import workflows

    conv = await own(db, user, row.fields["conversation"])
    row.status, row.step = "running", "result"
    row.result = {
        "outcome": "sending",
        "message": "Gönderim sonucu bekleniyor. Aynı işlemi yeniden denemek ikinci mesaj göndermez.",
    }
    row.revision += 1
    receipt = workflows.record(db, user, session, row, payload.client_operation_id, request)

    async def prepared(message: Message) -> None:
        # Canonical service commits this workflow + immutable receipt together
        # with the SENDING message, before its single external POST.
        row.state = {**row.state, "message_id": str(message.id)}

    try:
        await ConversationService(db).send_free_form(
            user.tenant_id, conv.id, row.fields["content"], prepared=prepared
        )
    except ConflictError:
        if not row.state.get("message_id"):
            raise  # Validation failed before the sending commit; nothing was sent.
    # send_free_form commits. Restore private-user RLS and reacquire chat lock
    # before touching workflow state; no external call occurs past this point.
    await db.execute(
        text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(user.id)}
    )
    await db.execute(
        select(AdminChatSession.id)
        .where(
            AdminChatSession.id == session.id,
            AdminChatSession.tenant_id == user.tenant_id,
            AdminChatSession.user_id == user.id,
        )
        .with_for_update()
    )
    await db.refresh(row)
    await sync(db, user, row)
    row.revision += 1
    await db.flush()
    return receipt


async def delivery_summary(db: Any, user: Any, conversation_id: UUID) -> str:
    from src.modules.agents.runtime_models import AgentRuntimeJob
    from src.modules.outreach.models import OutreachJob

    message = await db.scalar(
        select(Message)
        .where(
            Message.tenant_id == user.tenant_id,
            Message.conversation_id == conversation_id,
            Message.direction == "outbound",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if message is None:
        return "Bu konuşmada gönderilmiş mesaj yok."
    status = message.raw.get("delivery_status")
    if not status:
        job = await db.scalar(
            select(AgentRuntimeJob).where(
                AgentRuntimeJob.tenant_id == user.tenant_id,
                AgentRuntimeJob.outbound_message_id == message.id,
            )
        )
        if job:
            status = job.audit.get("delivery_status")
        elif message.outreach_job_id:
            outreach = await db.scalar(
                select(OutreachJob).where(
                    OutreachJob.tenant_id == user.tenant_id,
                    OutreachJob.id == message.outreach_job_id,
                )
            )
            status = str(outreach.status) if outreach else None
    if not status:
        status = message.raw.get("manual_send_state")
        if status == "sent":
            status = "accepted"
    return {
        "read": "Son mesaj okundu.",
        "delivered": "Son mesaj teslim edildi.",
        "sent": "Son mesaj gönderildi; teslim edildi bilgisi henüz yok.",
        "accepted": "Meta son mesajı kabul etti; teslimat henüz doğrulanmadı.",
        "failed": "Son mesaj için başarısız teslimat kaydı var.",
        "ambiguous": "Son mesajın gönderim sonucu belirsiz; yeniden gönderilmez.",
        "sending": "Son mesajın gönderim sonucu bekleniyor.",
    }.get(status, "Son mesaj için doğrulanmış teslimat bilgisi yok.")
