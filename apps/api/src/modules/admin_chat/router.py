# ruff: noqa: RUF001
"""Authenticated private admin chat API, with atomic retries and turn ordering."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text

from src.core.deps import ClaimsDep, DBSessionDep
from src.core.rbac import RequireAgent
from src.integrations.llm import LLMCompletionError, LLMNotConfiguredError
from src.modules.conversation_language import respond
from src.modules.selection.review import authorize

from . import language, outbound, planner, service, task_runner, workflow_requests
from .models import AdminChatSession, AdminChatTurn

router = APIRouter(prefix="/admin-chat", tags=["admin chat"])


async def account(db: Any, claims: Any) -> Any:
    if claims.get("role") == "viewer":
        from src.modules.auth.models import User

        user = await db.get(User, UUID(claims["sub"]))
        if user is None or not user.is_active or str(user.tenant_id) != claims["tid"]:
            raise HTTPException(403, "Active account required")
    else:
        user = await authorize(db, claims)
    # Transaction-local, never leaks to pooled connections. Every route sets this after auth.
    await db.execute(
        text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(user.id)}
    )
    return user


def public_session(row: Any) -> Any:
    return {"id": row.id, "title": row.title, "updated_at": row.updated_at}


async def owned(db: Any, user: Any, session_id: Any, *, lock: Any = False) -> Any:
    stmt = select(AdminChatSession).where(
        AdminChatSession.id == session_id,
        AdminChatSession.tenant_id == user.tenant_id,
        AdminChatSession.user_id == user.id,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = await db.scalar(stmt)
    if row is None:
        raise HTTPException(404, "Chat session not found")
    return row


@router.get("/sessions")
async def sessions(claims: ClaimsDep, db: DBSessionDep) -> Any:
    user = await account(db, claims)
    rows = await db.scalars(
        select(AdminChatSession)
        .where(
            AdminChatSession.tenant_id == user.tenant_id,
            AdminChatSession.user_id == user.id,
        )
        .order_by(AdminChatSession.updated_at.desc())
        .limit(100)
    )
    return [public_session(r) for r in rows]


@router.post("/sessions")
async def create_session(claims: ClaimsDep, db: DBSessionDep) -> Any:
    user = await account(db, claims)
    row = AdminChatSession(tenant_id=user.tenant_id, user_id=user.id)
    db.add(row)
    await db.flush()
    response = {**public_session(row), "suggestions": service.suggestions({})}
    await db.commit()
    return response


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: UUID, claims: ClaimsDep, db: DBSessionDep) -> Any:
    user = await account(db, claims)
    await owned(db, user, session_id)
    rows = await db.scalars(
        select(AdminChatTurn)
        .where(
            AdminChatTurn.session_id == session_id,
            AdminChatTurn.tenant_id == user.tenant_id,
            AdminChatTurn.user_id == user.id,
        )
        .order_by(AdminChatTurn.sequence)
    )
    result = []
    for row in rows:
        result.extend(
            [
                {
                    "id": str(row.client_message_id),
                    "role": "user",
                    "text": row.text,
                    "display_text": row.response.get("user_display_text", row.text),
                    "cards": [],
                    "created_at": row.created_at,
                    "sequence": row.sequence,
                },
                {
                    "id": str(row.id),
                    "role": "assistant",
                    "text": row.response["reply"],
                    "action_result": row.response.get("action_result"),
                    "suggestions": row.response.get("suggestions", []),
                    "selected_request_id": row.response.get("selected_request_id"),
                    "cards": row.response["cards"],
                    "response_source": row.response.get(
                        "response_source", row.audit.get("planner")
                    ),
                    "answer_origin": row.response.get("answer_origin", "legacy"),
                    "answer_verified": row.response.get("answer_verified", False),
                    "result_scope": row.response.get("result_scope", []),
                    "technical_error": row.response.get("technical_error"),
                    "created_at": row.created_at,
                    "sequence": row.sequence,
                },
            ]
        )
    return result


class TurnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    client_message_id: UUID

    @field_validator("text")
    @classmethod
    def nonempty(cls, value: Any) -> Any:
        if not value.strip():
            raise ValueError("Empty message")
        return value


@router.post("/sessions/{session_id}/turns")
async def turn(session_id: UUID, payload: TurnInput, claims: ClaimsDep, db: DBSessionDep) -> Any:
    user = await account(db, claims)
    session = await owned(db, user, session_id, lock=True)
    previous = await db.scalar(
        select(AdminChatTurn).where(
            AdminChatTurn.session_id == session_id,
            AdminChatTurn.client_message_id == payload.client_message_id,
            AdminChatTurn.user_id == user.id,
            AdminChatTurn.tenant_id == user.tenant_id,
        )
    )
    if previous:
        if previous.text != payload.text:
            raise HTTPException(409, "client_message_id already used for another message")
        return previous.response
    current_views = await workflows.list_views(db, user, session)
    history, memory = await language.recall(db, user, session)
    session.context = {**session.context, "language": {"result_scope": memory.get("result_scope", [])}}
    try:
        intent, source = await planner.plan(
            payload.text,
            history=history,
            conversation_context={"result_scope": memory.get("result_scope", [])},
            workflow_context=[
                {
                    **{k: v for k, v in w.items() if k in {"kind", "step", "status", "fields"}},
                    "template_choices": next(
                        (c["options"] for c in w.get("controls", []) if c["key"] == "template"), {}
                    ),
                }
                for w in current_views
                if w["status"] not in workflows.TERMINAL
            ],
            pending_operation=(
                session.context.get("workspace", {}).get("pending_operation", {}).get("operation")
            ),
        )
    except (TimeoutError, LLMCompletionError, LLMNotConfiguredError) as exc:
        raise HTTPException(
            503, "Model unavailable; retry with the same client_message_id"
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            502, "Model intent failed validation; retry with the same client_message_id"
        ) from exc
    intent = workflow_intents.normalize(intent)
    audit: Any
    try:
        intent, request_message = await workflow_requests.route_intent(db, user, session, intent)
        if intent.tool in {"reply", "clarify"}:
            reply, cards, action, audit = "", [], None, {"tool": intent.tool}
        elif intent.tool == "task":
            reply, cards, action, audit = await task_runner.execute(
                db, claims, user, session, intent, payload.text, payload.client_message_id, current_views
            )
        elif workflow_intents.ambiguous_approval(
            payload.text,
            bool(session.context.get("workspace", {}).get("pending_operation")),
            current_views,
            intent,
        ):
            reply, cards, action, audit = (
                "Birden fazla onay hedefi var. Uygulamak istediğiniz kartın onay düğmesini kullanın.",
                [],
                None,
                {"tool": "workflow", "status": "selection_required"},
            )
        elif intent.tool == "workflow":
            reply, cards, action, audit = await workflows.execute_intent(
                db, user, session, intent, payload.client_message_id
            )
        else:
            reply, cards, action, audit = await service.execute(db, claims, user, session, intent)
    except (TimeoutError, LLMCompletionError, LLMNotConfiguredError) as exc:
        raise HTTPException(503, "Model kullanılamıyor; hiçbir gönderim başlatılmadı.") from exc
    except ValueError as exc:
        raise HTTPException(502, "Model araç seçimi doğrulanamadı; gönderim yapılmadı.") from exc
    if request_message:
        reply = request_message
    updated_views = await workflows.list_views(db, user, session)
    memory = language.observe(audit, updated_views, reply, action, memory)
    answer_origin = "model_generated"
    answer_verified = bool(audit.get("verified"))
    if intent.tool != "task":
        generated = await respond(
            planner.get_llm_client(), payload.text, history=history,
            evidence=memory["evidence"],
            context={"channel": "admin", "result_scope": memory["result_scope"],
                     "write_authority": "Only current explicitly requested and validated operations."},
        )
        reply = generated.text
        answer_origin, answer_verified = generated.source, generated.verified
        audit["language"] = {"calls": generated.calls, "verified": generated.verified,
                            "reason": generated.reason, "evidence_ids": list(generated.evidence_ids)}
    elif intent.tool == "task" and not answer_verified:
        answer_origin = "verification_failed"
    audit["language_memory"] = memory
    if intent.workflow_kind == "create_template" and intent.workflow_action == "complete":
        # Template submission releases/reacquires the lock around its single Meta POST.
        # A concurrent retry may have already recorded this turn from the durable receipt.
        completed_retry = await db.scalar(
            select(AdminChatTurn).where(
                AdminChatTurn.session_id == session_id,
                AdminChatTurn.client_message_id == payload.client_message_id,
                AdminChatTurn.user_id == user.id,
                AdminChatTurn.tenant_id == user.tenant_id,
            )
        )
        if completed_retry:
            await db.commit()
            return completed_retry.response
    turn_id = uuid4()
    response = {
        "session_id": str(session.id),
        "turn_id": str(turn_id),
        "reply": reply,
        "response_source": source,
        "answer_origin": answer_origin,
        "answer_verified": answer_verified,
        "technical_error": None if reply else "Yanıt üretilemedi. İşlem durumu varsa kartlarında gösteriliyor.",
        "result_scope": memory["result_scope"],
        "cards": cards,
        "workflows": updated_views,
        "selected_request_id": session.context.get("selected_request_id"),
        "action_result": action,
        "suggestions": [] if intent.tool in {"reply", "clarify"} else service.suggestions(session.context, intent),
    }
    response["user_display_text"] = payload.text.removeprefix("action:")
    if payload.text.startswith("action:workspace:"):
        response["user_display_text"] = {
            "confirm": "Önizlemeyi uygula",
            "cancel": "Önizlemeden vazgeç",
            "select_agent": "Asistanı seç",
        }.get(intent.operation or "", "İşlemi aç")
    # Preserve the exact input for idempotency/audit while displaying a human selection label.
    try:
        selected_id = str(UUID(payload.text.removeprefix("action:").strip()))
    except ValueError:
        selected_id = None
    if selected_id:
        selected = next((card for card in cards if card.get("request_id") == selected_id), None)
        response["user_display_text"] = (
            f"{selected['title']} adlı müşterinin talebini aç" if selected else "Seçilen talebi aç"
        )
    session.sequence += 1
    session.updated_at = datetime.now(UTC)
    if session.sequence == 1:
        session.title = response.get("user_display_text", payload.text).strip()[:120]
    audit.update(planner=source, action_result=action)
    db.add(
        AdminChatTurn(
            id=turn_id,
            tenant_id=user.tenant_id,
            user_id=user.id,
            session_id=session.id,
            client_message_id=payload.client_message_id,
            sequence=session.sequence,
            text=payload.text,
            response=response,
            audit=audit,
        )
    )
    await db.commit()
    return response


@router.get("/capacity")
async def whatsapp_capacity(claims: RequireAgent, db: DBSessionDep) -> Any:
    user = await account(db, claims)
    return await outbound.capacity(db, user.tenant_id)


@router.get("/batches/{batch_id}")
async def outbound_batch(batch_id: UUID, claims: RequireAgent, db: DBSessionDep) -> Any:
    user = await account(db, claims)
    batch = await outbound.own_batch(db, user, batch_id)
    return await outbound.batch_card(db, batch)


class BatchAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["save", "send", "cancel"]
    variables: dict[str, str] = Field(default_factory=dict, max_length=30)
    consent_evidence: str | None = Field(default=None, min_length=10, max_length=2000)

    @field_validator("variables")
    @classmethod
    def validate_values(cls, values: dict[str, str]) -> dict[str, str]:
        if any(len(v) > 500 or not v.strip() for v in values.values()):
            raise ValueError("Alanlar boş olamaz ve 500 karakteri geçemez.")
        return values


@router.post("/batches/{batch_id}/actions")
async def outbound_action(
    batch_id: UUID, payload: BatchAction, claims: RequireAgent, db: DBSessionDep
) -> Any:
    from sqlalchemy import update

    from src.modules.compliance.models import AuditLog

    from .outbound_models import OutboundRecipient

    user = await account(db, claims)
    outbound.manager(user)
    batch = await outbound.own_batch(db, user, batch_id, lock=True)
    if payload.action == "cancel":
        await db.execute(
            update(OutboundRecipient)
            .where(
                OutboundRecipient.batch_id == batch.id,
                OutboundRecipient.tenant_id == user.tenant_id,
                OutboundRecipient.status.in_(["draft", "queued"]),
            )
            .values(status="cancelled")
        )
        batch.status = "cancelled"
    elif batch.status == "draft":
        if set(payload.variables) - set(batch.template["variables"]):
            raise HTTPException(422, "Bilinmeyen şablon alanı.")
        batch.variables = payload.variables
        batch.consent_evidence = (
            payload.consent_evidence.strip() if payload.consent_evidence else None
        )
        if batch.consent_evidence and len(batch.consent_evidence) < 10:
            raise HTTPException(422, "İzin kaynağını ve tarihini belirtin.")
        if payload.action == "send":
            await outbound.queue_batch(db, user, batch)
    db.add(
        AuditLog(
            tenant_id=user.tenant_id,
            actor_id=user.id,
            action="chat_outbound_" + payload.action,
            entity="outbound_batch",
            entity_id=str(batch.id),
            meta={"status": batch.status, "consent_recorded": bool(batch.consent_evidence)},
        )
    )
    await db.flush()
    card = await outbound.batch_card(db, batch)
    await db.commit()
    return card


# Every workflow mutation shares the chat-turn session lock. Forms and language
# tools therefore cannot race each other or create two foreground operations.
from . import workflow_intents, workflows  # noqa: E402
from .workflow_schema import WorkflowCommand, WorkflowStart  # noqa: E402


@router.get("/sessions/{session_id}/workflows")
async def workflow_list(session_id: UUID, claims: ClaimsDep, db: DBSessionDep) -> Any:
    user = await account(db, claims)
    session = await owned(db, user, session_id, lock=True)
    result = await workflows.list_views(db, user, session)
    await db.commit()
    return result


@router.post("/sessions/{session_id}/workflows")
async def workflow_start(
    session_id: UUID, payload: WorkflowStart, claims: ClaimsDep, db: DBSessionDep
) -> Any:
    user = await account(db, claims)
    session = await owned(db, user, session_id, lock=True)
    result = await workflows.start(db, user, session, payload)
    await db.commit()
    return result


@router.post("/sessions/{session_id}/workflows/{workflow_id}/actions")
async def workflow_action(
    session_id: UUID,
    workflow_id: UUID,
    payload: WorkflowCommand,
    claims: ClaimsDep,
    db: DBSessionDep,
) -> Any:
    user = await account(db, claims)
    session = await owned(db, user, session_id, lock=True)
    result = await workflows.act(db, user, session, workflow_id, payload)
    await db.commit()
    return result
