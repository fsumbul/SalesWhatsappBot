"""Authenticated private admin chat API, with atomic retries and turn ordering."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text

from src.core.deps import ClaimsDep, ClientIPDep, DBSessionDep
from src.core.rbac import RequireAgent, RequireManager
from src.core.request_rate_limit import enforce_request_rate_limit
from src.integrations.llm import LLMCompletionError, LLMNotConfiguredError
from src.modules.conversation_language import respond
from src.modules.selection.review import authorize

from . import (
    campaign_imports,
    language,
    outbound,
    planner,
    service,
    task_runner,
    workflow_outreach,
    workflow_requests,
)
from .models import AdminChatSession, AdminChatTurn
from .workflow_models import Workflow

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


def safe_campaign_import_context(view: dict[str, Any]) -> dict[str, Any] | None:
    """Project only server-owned import progress across the LLM boundary.

    Headers, filenames, selected columns, examples and counts originate from a
    user-supplied file or from UI input derived from that file.  They remain in
    the manager card, never in an LLM prompt or evidence memory.
    """

    output = view.get("output")
    imported = output.get("campaign_import") if isinstance(output, dict) else None
    if not isinstance(imported, dict):
        return None
    return {
        "kind": "outreach",
        "step": view.get("step"),
        "status": view.get("status"),
        "import_status": imported.get("status"),
    }


def planner_workflow_context(view: dict[str, Any]) -> dict[str, Any]:
    """Keep file-derived data out of the planner, even when stored in fields."""

    if imported := safe_campaign_import_context(view):
        return imported
    return {
        **{k: v for k, v in view.items() if k in {"kind", "step", "status", "fields"}},
        "template_choices": next(
            (choice["options"] for choice in view.get("controls", []) if choice["key"] == "template"),
            {},
        ),
    }


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
async def turn(
    session_id: UUID, payload: TurnInput, claims: ClaimsDep, db: DBSessionDep, ip: ClientIPDep
) -> Any:
    user = await account(db, claims)
    await enforce_request_rate_limit("chat_turn", f"{user.tenant_id}:{user.id}:{ip}")
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
    import_contexts = [
        context
        for view in current_views
        if (context := safe_campaign_import_context(view)) is not None
    ]
    history, memory = await language.recall(db, user, session)
    session.context = {**session.context, "language": {"result_scope": memory.get("result_scope", [])}}
    try:
        intent, source = await planner.plan(
            payload.text,
            history=history,
            conversation_context={"result_scope": memory.get("result_scope", [])},
            workflow_context=[
                planner_workflow_context(view)
                for view in current_views
                if view["status"] not in workflows.TERMINAL
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
            reply, cards, action, audit = "", [], None, {
                "tool": intent.tool,
                **(
                    {
                        "conversation_evidence": [
                            {
                                "id": "campaign_import_status",
                                "summary": "Doğrulanmış dosya içe aktarma durumu.",
                                "data": import_contexts,
                            }
                        ]
                    }
                    if import_contexts
                    else {}
                ),
            }
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
    batch_id: UUID,
    payload: BatchAction,
    claims: RequireAgent,
    db: DBSessionDep,
    ip: ClientIPDep,
) -> Any:
    from sqlalchemy import update

    from src.modules.compliance.models import AuditLog

    from .outbound_models import OutboundRecipient

    user = await account(db, claims)
    # This legacy v1 card can still trigger Meta template/capacity checks for
    # manual batches. Keep it inside the same narrow expensive-action bucket
    # as the workflow route instead of leaving an alternate unbounded path.
    await enforce_request_rate_limit("workflow_action", f"{user.tenant_id}:{user.id}:{ip}")
    outbound.manager(user)
    batch = await outbound.own_batch(db, user, batch_id, lock=True)
    if outbound.is_campaign_import_batch(batch) and payload.action != "cancel":
        raise HTTPException(
            409,
            "Dosya kampanyası yalnız bağlı gönderim kartından değiştirilebilir veya kuyruğa alınabilir.",
        )
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
    session_id: UUID,
    payload: WorkflowStart,
    claims: ClaimsDep,
    db: DBSessionDep,
    ip: ClientIPDep,
) -> Any:
    user = await account(db, claims)
    await enforce_request_rate_limit("workflow_action", f"{user.tenant_id}:{user.id}:{ip}")
    session = await owned(db, user, session_id, lock=True)
    result = await workflows.start(db, user, session, payload)
    await db.commit()
    return result


@router.post(
    "/sessions/{session_id}/workflows/{workflow_id}/imports",
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_campaign_import(
    session_id: UUID,
    workflow_id: UUID,
    file: Annotated[UploadFile, File()],
    country_code: Annotated[str, Form()],
    client_operation_id: Annotated[UUID, Form()],
    expected_revision: Annotated[int, Form(ge=0)],
    claims: RequireManager,
    db: DBSessionDep,
    ip: ClientIPDep,
) -> Any:
    """Accept one bounded private CSV/XLSX source for an outreach workflow.

    The BFF keeps the browser on the authenticated application path. This
    route validates bytes before private MinIO storage and queues a worker only
    after the database transaction is committed.
    """

    user = await account(db, claims)
    if not campaign_imports.campaign_imports_enabled_for(user.tenant_id, user.id):
        # Do not advertise a live campaign surface outside the one validated
        # canary manager. Existing import status remains readable for audit.
        raise HTTPException(404, "Dosya kaynaklı kampanya bu hesap için henüz etkin değil.")
    await enforce_request_rate_limit("campaign_import", f"{user.tenant_id}:{user.id}:{ip}")
    session = await owned(db, user, session_id, lock=True)
    workflow = await db.scalar(
        select(Workflow)
        .where(
            Workflow.id == workflow_id,
            Workflow.session_id == session.id,
            Workflow.tenant_id == user.tenant_id,
            Workflow.user_id == user.id,
        )
        .with_for_update()
    )
    if workflow is None or workflow.kind != "outreach":
        raise HTTPException(404, "WhatsApp gönderim işlemi bulunamadı.")
    same_operation = await db.scalar(
        select(campaign_imports.CampaignImport).where(
            campaign_imports.CampaignImport.tenant_id == user.tenant_id,
            campaign_imports.CampaignImport.workflow_id == workflow.id,
            campaign_imports.CampaignImport.client_operation_id == client_operation_id,
        )
    )
    if same_operation is None:
        if workflow.status in workflows.TERMINAL or workflow.state.get("batch_id"):
            raise HTTPException(409, "Bu gönderim işleminin dosya kaynağı artık değiştirilemez.")
        if workflow.revision != expected_revision:
            raise HTTPException(
                409,
                {
                    "message": "İşlem başka bir yerde güncellendi. Güncel kartı açın.",
                    "workflow": workflows.view(workflow),
                },
            )
        current_id = workflow.state.get("import_id")
        if current_id:
            current = await campaign_imports.get_campaign_import(
                db,
                tenant_id=user.tenant_id,
                import_id=UUID(current_id),
                workflow_id=workflow.id,
            )
            if (
                current is not None
                and current.client_operation_id != client_operation_id
                and current.status != campaign_imports.CampaignImportStatus.FAILED.value
            ):
                raise HTTPException(
                    409,
                    "Bu kartta halen bir dosya içe aktarımı var. Durumunu bekleyin veya yeni işlem başlatın.",
                )

    try:
        data = await file.read(campaign_imports.MAX_IMPORT_BYTES + 1)
        imported, created = await campaign_imports.create_campaign_import(
            db,
            tenant_id=user.tenant_id,
            workflow_id=workflow.id,
            uploader_id=user.id,
            client_operation_id=client_operation_id,
            country_code=country_code,
            filename=file.filename or "",
            claimed_mime=file.content_type,
            data=data,
        )
    except campaign_imports.CampaignImportConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except campaign_imports.CampaignImportValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except campaign_imports.CampaignImportStorageError as exc:
        raise HTTPException(503, str(exc)) from exc
    finally:
        await file.close()

    if not created:
        # Same operation IDs are durable receipts. Verify the bytes above, but
        # do not rewind a workflow that has progressed while the client retried
        # after losing its original 202 response.
        response = {
            "import": campaign_imports.public_import_view(imported),
            "workflow": workflows.view(workflow),
        }
        await db.commit()
        if imported.status == campaign_imports.CampaignImportStatus.QUEUED.value:
            try:
                campaign_imports.queue_import(imported)
            except Exception as exc:  # pragma: no cover - broker availability boundary
                raise HTTPException(
                    503,
                    "İçe aktarma kuyruğuna ulaşılamadı; aynı işlemi yeniden deneyin.",
                ) from exc
        return response

    workflow.fields = {
        **workflow.fields,
        "recipient_source": "file",
        "country_code": imported.country_code,
    }
    workflow.result = {}
    workflow.step = "details"
    workflow.status = "running"
    workflow.state = {
        **workflow.state,
        "import_id": str(imported.id),
        "import_status": imported.status,
    }
    await workflow_outreach.sync_import(db, user, workflow)
    if created:
        workflow.revision += 1
        from src.modules.compliance.models import AuditLog

        db.add(
            AuditLog(
                tenant_id=user.tenant_id,
                actor_id=user.id,
                action="campaign_import_uploaded",
                entity="campaign_import",
                entity_id=str(imported.id),
                ip=ip,
                meta={
                    "source_hash": imported.sha256,
                    "mime_type": imported.mime_type,
                    "country_code": imported.country_code,
                },
            )
        )
    response = {
        "import": campaign_imports.public_import_view(imported),
        "workflow": workflows.view(workflow),
    }
    await db.commit()
    if imported.status == campaign_imports.CampaignImportStatus.QUEUED.value:
        # A retry of the same operation is also allowed to repair a broker
        # failure after the durable DB commit. Duplicate parse tasks are safe:
        # the worker locks the import and treats an already-parsing row as a
        # no-op. Do not report 202 unless the primary enqueue succeeded.
        try:
            campaign_imports.queue_import(imported)
        except Exception as exc:  # pragma: no cover - broker availability boundary
            raise HTTPException(
                503,
                "İçe aktarma kuyruğuna ulaşılamadı; aynı işlemi yeniden deneyin.",
            ) from exc
    return response


@router.get("/imports/{import_id}")
async def campaign_import_status(
    import_id: UUID, claims: RequireManager, db: DBSessionDep
) -> Any:
    """Managers receive only their tenant's masked import aggregates."""

    user = await account(db, claims)
    imported = await campaign_imports.get_campaign_import(
        db, tenant_id=user.tenant_id, import_id=import_id
    )
    if imported is None:
        raise HTTPException(404, "İçe aktarma bulunamadı.")
    return campaign_imports.public_import_view(imported)


@router.post("/sessions/{session_id}/workflows/{workflow_id}/actions")
async def workflow_action(
    session_id: UUID,
    workflow_id: UUID,
    payload: WorkflowCommand,
    claims: ClaimsDep,
    db: DBSessionDep,
    ip: ClientIPDep,
) -> Any:
    user = await account(db, claims)
    await enforce_request_rate_limit("workflow_action", f"{user.tenant_id}:{user.id}:{ip}")
    session = await owned(db, user, session_id, lock=True)
    result = await workflows.act(db, user, session, workflow_id, payload)
    # Mapping a previously ambiguous file is the one import transition that
    # happens inside the existing generic workflow-action transaction. Queue
    # only after that durable revision commits; retries of this idempotent
    # action safely repair the narrow commit-to-broker gap.
    queued_import: tuple[UUID, str | None] | None = None
    if payload.fields.get("phone_column"):
        workflow = await db.scalar(
            select(Workflow).where(
                Workflow.id == workflow_id,
                Workflow.session_id == session.id,
                Workflow.tenant_id == user.tenant_id,
                Workflow.user_id == user.id,
            )
        )
        import_id = workflow.state.get("import_id") if workflow is not None else None
        if import_id:
            imported = await campaign_imports.get_campaign_import(
                db,
                tenant_id=user.tenant_id,
                import_id=UUID(import_id),
                workflow_id=workflow_id,
            )
            if imported is not None and imported.status == campaign_imports.CampaignImportStatus.QUEUED.value:
                queued_import = (imported.id, imported.phone_column)
    await db.commit()
    if queued_import is not None:
        import_id, phone_column = queued_import
        try:
            campaign_imports.queue_import(
                import_id,
                user.tenant_id,
                phone_column=phone_column,
            )
        except Exception as exc:  # pragma: no cover - broker availability boundary
            raise HTTPException(
                503,
                "İçe aktarma kuyruğuna ulaşılamadı; aynı işlemi yeniden deneyin.",
            ) from exc
    return result
