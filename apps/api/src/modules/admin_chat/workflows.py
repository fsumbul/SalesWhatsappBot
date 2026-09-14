# ruff: noqa: RUF001
"""Shared form/chat workflow engine. Callers hold the owning session lock.

Database effects and action receipts commit together. External message sending
must use the existing durable outbox, never the deferred transaction adapter.
"""

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

import httpx
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from pydantic.networks import EmailStr
from sqlalchemy import or_, select, text

from src.core.db import set_tenant_context
from src.core.rbac import Role, role_at_least
from src.modules.agents.schemas import AgentIn
from src.modules.agents.service import AgentService
from src.modules.auth import platform
from src.modules.auth.schemas import InviteIn
from src.modules.auth.service import AuthService
from src.modules.discovery.models import ContactType, Lead, LeadContact

from . import (
    workflow_agents,
    workflow_inbox,
    workflow_members,
    workflow_outreach,
    workflow_owners,
    workflow_records,
    workflow_requests,
    workflow_templates,
)
from .workflow_models import Workflow, WorkflowAction
from .workflow_schema import WorkflowCommand, WorkflowField, WorkflowStart, WorkflowView
from .workspace_tools import TurnTransaction

TERMINAL = {"completed", "cancelled"}
ACTIVE = {"awaiting_input", "ready", "running", "failed"}
TITLES = {
    "create_template": "Yeni WhatsApp şablonu",
    "request_update": "Talebi güncelle",
    "conversation": "Müşteri konuşması",
    "reply": "Müşteriye yanıt hazırla",
    "resume_bot": "Botu devam ettir",
    "outreach": "WhatsApp gönderimi hazırla",
    "member": "Ekip erişimini değiştir",
    "records": "Kayıtlar",
    "configure": "Şirket bilgilerini değiştir",
    "owner_invite": "Şirket sahibini davet et",
    "rollback": "Önceki sürüme dön",
    "publish": "Sürümü yayınla",
    "test": "Müşteri testi",
    "create_company": "Şirket oluştur",
    "create_agent": "Asistan oluştur",
    "person": "Kişi ekle",
    "contact": "Müşteri / irtibat ekle",
    "invite": "Ekip üyesi davet et",
}
CONTROLS = {
    "create_template": workflow_templates.CONTROLS,
    "owner_invite": workflow_owners.CONTROLS,
    **workflow_inbox.CONTROLS,
    "request_update": workflow_requests.CONTROLS,
    "outreach": workflow_outreach.CONTROLS,
    "member": workflow_members.CONTROLS,
    "records": workflow_records.CONTROLS,
    **workflow_agents.CONTROLS,
    "create_company": [
        WorkflowField(key="name", label="Şirket adı", required=True),
        WorkflowField(key="slug", label="Şirket kodu", required=True),
        WorkflowField(
            key="email", label="Şirket sahibinin e-postası", control="email", required=True
        ),
    ],
    "create_agent": [
        WorkflowField(key="name", label="Asistan adı", required=True),
        WorkflowField(key="slug", label="Asistan kodu", required=True),
    ],
    "person": [
        WorkflowField(
            key="person_type",
            label="Kişi türü",
            control="select",
            required=True,
            options={"contact": "Müşteri / irtibat", "invite": "Ekip üyesi"},
        )
    ],
    "contact": [
        WorkflowField(key="name", label="Ad soyad", required=True),
        WorkflowField(key="email", label="E-posta", control="email"),
        WorkflowField(key="phone", label="Telefon (+ülke kodu)", control="tel"),
        WorkflowField(key="company", label="Şirket (isteğe bağlı)"),
    ],
    "invite": [
        WorkflowField(key="email", label="E-posta", control="email", required=True),
        WorkflowField(
            key="role",
            label="Yetki",
            control="select",
            required=True,
            options={
                "tenant_owner": "Şirket sahibi",
                "sales_manager": "Satış yöneticisi",
                "sales_agent": "Satış temsilcisi",
                "viewer": "İzleyici",
            },
        ),
    ],
}


def authorize(user: Any, kind: str) -> None:
    required = {
        "create_template": Role.SALES_MANAGER,
        "request_update": Role.SALES_AGENT,
        "conversation": Role.VIEWER,
        "reply": Role.SALES_AGENT,
        "resume_bot": Role.SALES_AGENT,
        "outreach": Role.SALES_MANAGER,
        "member": Role.TENANT_OWNER,
        "records": Role.VIEWER,
        "configure": Role.SALES_MANAGER,
        "rollback": Role.SALES_MANAGER,
        "publish": Role.SALES_MANAGER,
        "test": Role.SALES_MANAGER,
        "invite": Role.TENANT_OWNER,
        "owner_invite": Role.SUPER_ADMIN,
        "create_company": Role.SUPER_ADMIN,
        "create_agent": Role.SALES_MANAGER,
    }.get(kind, Role.SALES_AGENT)
    if not role_at_least(user.role, required):
        raise HTTPException(403, "Bu işlem için güncel hesap yetkiniz yeterli değil.")


def validate(kind: str, fields: dict[str, str]) -> dict[str, str]:
    errors = workflow_templates.errors(fields) if kind == "create_template" else {}
    for control in CONTROLS[kind]:
        value = fields.get(control.key, "")
        if control.required and not value:
            errors[control.key] = f"{control.label} gerekli."
        elif control.options and value and value not in control.options:
            errors[control.key] = "Listeden geçerli bir seçenek seçin."
    if fields.get("email"):
        try:
            TypeAdapter(EmailStr).validate_python(fields["email"])
        except ValidationError:
            errors["email"] = "Geçerli bir e-posta adresi yazın."
    if fields.get("phone") and not re.fullmatch(r"\+[1-9][0-9]{7,14}", fields["phone"]):
        errors["phone"] = "Telefonu +ülke kodu ile yazın."
    if kind == "contact" and not (fields.get("email") or fields.get("phone")):
        errors["email"] = "E-posta veya telefon bilgilerinden en az biri gerekli."
    if (kind == "test" or (kind == "configure" and fields.get("format") == "text")) and len(
        fields.get("content", "")
    ) > 4000:
        errors["content"] = "Mesaj en fazla 4000 karakter olabilir."
    if kind in {"create_company", "create_agent"}:
        max_name = 120 if kind == "create_company" else 160
        if not 2 <= len(fields.get("name", "")) <= max_name:
            errors["name"] = f"Ad 2–{max_name} karakter olmalı."
        if not re.fullmatch(r"[a-z0-9-]{2,80}", fields.get("slug", "")):
            errors["slug"] = "Kod 2–80 küçük harf, rakam veya tire içermeli."
    return errors


def view(row: Any) -> dict[str, Any]:
    errors = (
        (
            workflow_outreach.errors(row)
            if row.kind == "outreach"
            else validate(row.kind, row.fields)
        )
        if row.status not in TERMINAL
        else {}
    )
    if row.kind == "request_update" and row.status not in TERMINAL:
        errors.update(workflow_requests.errors(row))
    errors.update((row.state or {}).get("errors", {}))
    primary = None
    label = None
    if row.status == "paused":
        primary, label = "resume", "Kaldığım yerden devam et"
    elif row.status in ACTIVE and row.status != "running":
        primary = "complete" if row.step == "review" else "continue"
        label = (
            {
                "create_template": "Meta onayına gönder",
                "request_update": "Talep değişikliğini uygula",
                "reply": "Yanıtı gönder",
                "resume_bot": "Botu devam ettir",
                "outreach": "Uygun alıcıları gönderim kuyruğuna al",
                "member": "Ekip erişimini güncelle",
                "configure": "Taslağa kaydet",
                "owner_invite": "Sahip davet bağlantısını hazırla",
                "rollback": "Seçilen sürümü yeniden yayınla",
                "publish": "Sürümü yayınla",
                "test": "Müşteri testini çalıştır",
                "invite": "Davet bağlantısı oluştur",
                "contact": "Kişiyi kaydet",
                "create_company": "Şirket ve sahip daveti oluştur",
                "create_agent": "Asistanı oluştur",
            }.get(row.kind, "Devam et")
            if primary == "complete"
            else "Devam et"
        )
    if row.kind in {"records", "conversation"} and row.status != "paused":
        primary, label = (
            "continue",
            ("Konuşmayı yenile" if row.kind == "conversation" else "Kayıtları ara"),
        )
    return WorkflowView(
        id=row.id,
        session_id=row.session_id,
        kind=row.kind,
        anchor_sequence=getattr(row, "anchor_sequence", 0),
        revision=row.revision,
        title=workflow_records.CATEGORIES.get(row.fields.get("category"), "Kayıtlar")
        if row.kind == "records"
        else TITLES[row.kind],
        step=row.step,
        steps=[]
        if row.kind in {"records", "conversation"}
        else (
            ["details", "compose", "review", "result"]
            if row.kind == "outreach"
            else ["details", "review", "result"]
        ),
        status=row.status,
        fields=row.fields,
        records=(row.state or {}).get("records", []),
        record_actions=(row.state or {}).get("record_actions", []),
        page=(row.state or {}).get("page", 1),
        has_more=(row.state or {}).get("has_more", False),
        scope=(row.state or {}).get("scope"),
        controls=(
            workflow_templates.controls(row)
            if row.kind == "create_template"
            else
            workflow_owners.controls(row)
            if row.kind == "owner_invite"
            else workflow_requests.controls(row)
            if row.kind == "request_update"
            else workflow_inbox.controls(row)
            if row.kind in workflow_inbox.KINDS
            else workflow_outreach.controls(row)
            if row.kind == "outreach"
            else workflow_members.controls(row)
            if row.kind == "member"
            else (
                workflow_records.controls(row)
                if row.kind == "records"
                else (
                    workflow_agents.controls(row)
                    if row.kind in workflow_agents.KINDS
                    else CONTROLS[row.kind]
                )
            )
        ),
        changes=(row.state or {}).get("changes", []),
        output=workflow_templates.preview(row.fields)
        if row.kind == "create_template" and row.step != "result"
        else workflow_outreach.output(row)
        if row.kind == "outreach"
        else (row.state or {}).get("output", {}),
        errors=errors,
        primary_action=primary,
        primary_label=label,
        result=row.result,
    ).model_dump(mode="json")


async def list_views(db: Any, user: Any, session: Any) -> list[dict[str, Any]]:
    rows = await db.scalars(
        select(Workflow)
        .where(
            Workflow.session_id == session.id,
            Workflow.user_id == user.id,
            Workflow.tenant_id == user.tenant_id,
        )
        .order_by(Workflow.created_at)
    )
    result = []
    for row in rows:
        if row.kind == "outreach" and row.status in ACTIVE and "templates" not in row.state and not row.state.get("batch_id"):
            await workflow_outreach.initialize(db, user, row)
            row.revision += 1
        if row.kind == "outreach" and row.state.get("batch_id"):
            before = (row.state, row.status, row.result)
            await workflow_outreach.sync(db, user, row)
            if before != (row.state, row.status, row.result):
                row.revision += 1
        if row.kind == "reply" and row.state.get("message_id"):
            before = (row.state, row.status, row.result)
            await workflow_inbox.sync(db, user, row)
            if before != (row.state, row.status, row.result):
                row.revision += 1
        if row.kind == "conversation" and row.status == "awaiting_input":
            conversation_before = (row.state, row.status)
            await workflow_inbox.refresh(db, user, row)
            if conversation_before != (row.state, row.status):
                row.revision += 1
        if row.kind == "create_template":
            before = (row.state, row.status, row.result)
            await workflow_templates.sync(db, user, row)
            if before != (row.state, row.status, row.result):
                row.revision += 1
        result.append(view(row))
    await db.flush()
    return result


async def receipt(
    db: Any, user: Any, session: Any, client_id: UUID, request: dict[str, Any]
) -> Any:
    previous = await db.scalar(
        select(WorkflowAction).where(
            WorkflowAction.session_id == session.id,
            WorkflowAction.user_id == user.id,
            WorkflowAction.tenant_id == user.tenant_id,
            WorkflowAction.client_operation_id == client_id,
        )
    )
    if previous:
        if previous.request != request:
            raise HTTPException(409, "İşlem kimliği başka bir istek için kullanılmış.")
        return previous.response
    return None


async def pause_others(db: Any, user: Any, session: Any, except_id: UUID | None = None) -> None:
    rows = await db.scalars(
        select(Workflow).where(
            Workflow.session_id == session.id,
            Workflow.user_id == user.id,
            Workflow.tenant_id == user.tenant_id,
            Workflow.status.in_(ACTIVE),
        )
    )
    for row in rows:
        if row.id != except_id:
            if row.status == "running" and row.kind not in {"outreach", "create_template"}:
                raise HTTPException(409, "Çalışan işlemin sonucu bekleniyor.")
            if row.kind in {"outreach", "create_template"} and row.status == "running":
                row.state = {**row.state, "background_running": True}
            row.status = "paused"
            row.revision += 1
    await db.flush()


def patch(row: Any, fields: dict[str, str]) -> None:
    allowed = {c.key for c in CONTROLS[row.kind]}
    if row.kind == "create_template":
        allowed.update("example_" + key for key in workflow_templates.variables({**row.fields, **fields}))
        allowed.update(c.key for c in workflow_templates.controls(row))
    if row.kind == "outreach":
        allowed.update(c.key for c in workflow_outreach.controls(row))
        allowed.update("var_" + key for t in row.state.get("templates", []) for key in t["variables"])
    if set(fields) - allowed:
        raise HTTPException(422, "Bilinmeyen veya çok uzun işlem alanı.")
    limits = {
        "body": 1024,
        "header": 60,
        "footer": 60,
        "buttons": 80,
        "language": 10,
        "template_category": 20,
        "recipients": 2500,
        "purpose": 1000,
        "template": 160,
        "consent_evidence": 2000,
        "member": 254,
        "active": 5,
        "category": 30,
        "tenant": 36,
        "request": 36,
        "operation": 30,
        "assignee": 36,
        "note": 2000,
        "status": 30,
        "today": 5,
        "q": 160,
        "page": 5,
        "record": 160,
        "conversation": 36,
        "content": 500000,
        "columns": 10000,
        "format": 10,
        "agent": 160,
        "version": 36,
        "slug": 80,
        "name": 160,
        "company": 255,
        "email": 254,
        "phone": 20,
        "role": 30,
        "person_type": 20,
    }
    limits.update({"column_" + key: 255 for key in workflow_agents.CSV_LABELS})
    if row.kind == "reply":
        limits["content"] = 4000
    if row.kind == "create_template":
        limits["name"] = 512
    if any(len(v.strip()) > limits.get(k, 500) for k, v in fields.items()):
        raise HTTPException(422, "Alan uzunluğu sınırı aşıldı.")
    values = {**row.fields, **{k: v.strip() for k, v in fields.items()}}
    if row.kind == "create_template":
        current_examples = {"example_" + key for key in workflow_templates.variables(values)}
        values = {k: v for k, v in values.items() if not k.startswith("example_") or k in current_examples}
    if row.kind == "outreach" and "template" in fields:
        selected = next((t for t in row.state.get("templates", []) if values["template"] in {t["id"], t["name"]}), None)
        if selected:
            current_variables = {"var_" + key for key in selected["variables"]}
            values = {k: v for k, v in values.items() if not k.startswith("var_") or k in current_variables}
    if (
        row.kind == "records"
        and "page" not in fields
        and any(
            key in fields and values.get(key) != row.fields.get(key)
            for key in ("category", "q", "status", "today", "agent")
        )
    ):
        values["page"] = "1"
    if (
        "agent" in fields
        and fields["agent"] != row.fields.get("agent")
        and values.get("version") == row.fields.get("version")
    ):
        values.pop("version", None)
    if values.get("email"):
        values["email"] = values["email"].lower()
    row.fields = values
    if fields:
        row.result = {}
        row.step = (
            ("compose" if row.step in {"compose", "review"} else "details")
            if row.kind == "outreach"
            else "details"
        )
        row.status = "awaiting_input"


def record(
    db: Any, user: Any, session: Any, row: Any, client_id: UUID, request: dict[str, Any]
) -> dict[str, Any]:
    response = view(row)
    session.updated_at = datetime.now(UTC)
    if session.sequence == 0:
        session.title = TITLES[row.kind]
    db.add(
        WorkflowAction(
            tenant_id=user.tenant_id,
            user_id=user.id,
            session_id=session.id,
            workflow_id=row.id,
            client_operation_id=client_id,
            request=request,
            response=response,
        )
    )
    return response


async def start(
    db: Any, user: Any, session: Any, payload: WorkflowStart, *, anchor_sequence: int | None = None
) -> dict[str, Any]:
    authorize(user, payload.kind)
    if payload.kind == "records":
        workflow_records.authorize(user, payload.fields.get("category", "agents"))
    request = {"start": payload.model_dump(mode="json")}
    old = await receipt(db, user, session, payload.client_operation_id, request)
    if old:
        return dict(old)
    await pause_others(db, user, session)
    row = Workflow(
        tenant_id=user.tenant_id,
        user_id=user.id,
        session_id=session.id,
        kind=payload.kind,
        anchor_sequence=session.sequence if anchor_sequence is None else anchor_sequence,
        fields={},
        state={},
        result={},
        step="details",
        status="awaiting_input",
        revision=0,
    )
    patch(row, {"language": "tr", "template_category": "MARKETING", **payload.fields}
          if row.kind == "create_template" else payload.fields)
    if row.kind == "outreach":
        await workflow_outreach.initialize(db, user, row)
    if row.kind == "owner_invite":
        await workflow_owners.initialize(db, row)
    if row.kind == "request_update":
        await workflow_requests.initialize(db, user, row)
    if row.kind in workflow_inbox.KINDS:
        await workflow_inbox.refresh(db, user, row)
    if row.kind == "member":
        await workflow_members.initialize(db, user, session, row)
    if row.kind == "records":
        await workflow_records.initialize(db, user, session, row)
    if row.kind in workflow_agents.KINDS:
        await workflow_agents.initialize(db, user, session, row)
    db.add(row)
    await db.flush()
    return record(db, user, session, row, payload.client_operation_id, request)


async def save_contact(db: Any, user: Any, row: Any) -> dict[str, str]:
    # Serialize contact creation per tenant, including workflows in different chats.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "workflow-contact:" + str(user.tenant_id)},
    )
    values = [
        (ContactType.EMAIL, row.fields.get("email")),
        (ContactType.PHONE, row.fields.get("phone")),
    ]
    filters = [
        (LeadContact.type == kind) & (LeadContact.normalized_value == value)
        for kind, value in values
        if value
    ]
    matches = list(
        (
            await db.scalars(
                select(LeadContact).where(LeadContact.tenant_id == user.tenant_id, or_(*filters))
            )
        ).all()
    )
    if matches:
        ids = sorted({str(c.lead_id) for c in matches})
        existing = await db.scalars(
            select(Lead)
            .where(Lead.tenant_id == user.tenant_id, Lead.id.in_([UUID(value) for value in ids]))
            .order_by(Lead.id)
        )
        records = []
        for person in existing:
            channels = list(
                await db.scalars(
                    select(LeadContact).where(
                        LeadContact.tenant_id == user.tenant_id, LeadContact.lead_id == person.id
                    )
                )
            )
            records.append(
                {
                    "id": str(person.id),
                    "title": person.person_name or person.company_name or "Kayıtlı kişi",
                    "subtitle": person.company_name,
                    "details": {
                        "Ad soyad": person.person_name or "Belirtilmedi",
                        "Şirket": person.company_name or "Belirtilmedi",
                        **{
                            f"{c.type.value} {i + 1}": c.normalized_value
                            for i, c in enumerate(channels)
                        },
                    },
                    "actions": [],
                }
            )
        row.state = {**row.state, "records": records}
        return {
            "outcome": "duplicate",
            "message": "Bu iletişim bilgisi zaten kayıtlı. Yeni kişi oluşturulmadı; mevcut kaydı inceleyin.",
            "lead_ids": ",".join(ids),
        }
    lead = Lead(
        tenant_id=user.tenant_id,
        person_name=row.fields["name"],
        company_name=row.fields.get("company", ""),
        normalized_name=row.fields.get("company", "").casefold(),
        source="manual_workflow",
        discovered_at=datetime.now(UTC),
    )
    db.add(lead)
    await db.flush()
    for kind, value in values:
        if value:
            db.add(
                LeadContact(
                    tenant_id=user.tenant_id,
                    lead_id=lead.id,
                    type=kind,
                    raw_value=value,
                    normalized_value=value,
                )
            )
    await db.flush()
    return {
        "outcome": "created",
        "lead_id": str(lead.id),
        "message": f"{lead.person_name} kişi olarak kaydedildi.",
    }


async def act(
    db: Any, user: Any, session: Any, workflow_id: UUID, payload: WorkflowCommand
) -> dict[str, Any]:
    row = await db.scalar(
        select(Workflow).where(
            Workflow.id == workflow_id,
            Workflow.session_id == session.id,
            Workflow.user_id == user.id,
            Workflow.tenant_id == user.tenant_id,
        )
    )
    if row is None:
        raise HTTPException(404, "İşlem bulunamadı.")
    authorize(user, row.kind)
    if row.kind == "records":
        workflow_records.authorize(user, row.fields["category"])
    request = {"workflow_id": str(workflow_id), **payload.model_dump(mode="json")}
    old = await receipt(db, user, session, payload.client_operation_id, request)
    if old:
        return dict(old)
    if row.revision != payload.expected_revision:
        raise HTTPException(
            409,
            {
                "message": "İşlem başka bir yerde güncellendi. Güncel kartı açın.",
                "workflow": view(row),
            },
        )
    if (
        row.status in TERMINAL
        or (
            row.status == "running"
            and not (
                row.kind == "outreach"
                and payload.action in {"pause", "cancel"}
                and not payload.fields
            )
        )
        or (
            row.kind == "outreach"
            and row.state.get("background_running")
            and payload.action not in {"resume", "cancel"}
        )
        or (row.kind == "create_template" and row.state.get("background_running") and payload.action != "resume")
    ):
        raise HTTPException(409, "Bu işlem artık düzenlenemez.")
    action = payload.action
    if action == "launch":
        if row.kind not in {"records", "conversation", "outreach"}:
            raise HTTPException(422, "Bu işlem bu eylemi desteklemiyor.")
        if row.kind == "outreach":
            if payload.fields != {"operation": "create_template"}:
                raise HTTPException(422, "Geçersiz şablon işlemi.")
            kind, values = "create_template", {}
        else:
            kind, values = await (
                workflow_inbox.launch(db, user, row, payload.fields)
                if row.kind == "conversation"
                else workflow_records.launch(db, user, row, payload.fields)
            )
        await start(
            db,
            user,
            session,
            WorkflowStart.model_validate(
                {
                    "kind": kind,
                    "fields": values,
                    "client_operation_id": uuid5(payload.client_operation_id, "child"),
                }
            ),
            anchor_sequence=row.anchor_sequence,
        )
        return record(db, user, session, row, payload.client_operation_id, request)
    if payload.fields and action not in {"update", "continue", "pause"}:
        raise HTTPException(422, "Bu eylem alan değişikliği kabul etmez.")
    if row.status == "paused" and action not in {"resume", "cancel"}:
        raise HTTPException(409, "Önce işlemi devam ettirin.")
    if row.kind in workflow_agents.KINDS and (
        action in {"back", "cancel"} or (payload.fields and action in {"update", "continue"})
    ):
        await workflow_agents.invalidate(db, user, row)
    if row.kind == "outreach" and (action == "back" or payload.fields):
        await workflow_outreach.invalidate(db, user, row)
    if row.kind in {"reply", "resume_bot", "request_update", "owner_invite"} and (
        action == "back" or payload.fields
    ):
        row.state = {k: v for k, v in row.state.items() if k != "changes"}
    if row.kind == "member" and (action == "back" or payload.fields):
        row.state = {"member_choices": row.state.get("member_choices", {})}
    if action == "resume":
        await pause_others(db, user, session, row.id)
        if row.kind == "outreach" and not row.state.get("batch_id"):
            await workflow_outreach.initialize(db, user, row)
        row.status = (
            "running"
            if row.state.get("background_running")
            else ("ready" if row.step == "review" else "awaiting_input")
        )
    elif action == "pause":
        if row.kind == "outreach" and row.status == "running":
            row.state = {**row.state, "background_running": True}
        patch(row, payload.fields)
        row.status = "paused"
    elif action == "cancel":
        if row.kind == "outreach":
            await workflow_outreach.cancel(db, user, row)
        else:
            row.status = "cancelled"
    elif action == "back":
        row.step, row.status = (
            ("compose" if row.kind == "outreach" and row.step == "review" else "details"),
            "awaiting_input",
        )
    elif action in {"update", "continue"}:
        patch(row, payload.fields)
        if row.kind == "conversation":
            await workflow_inbox.refresh(db, user, row)
        if row.kind == "records":
            await workflow_records.refresh(db, user, row)
        if row.kind in workflow_agents.KINDS:
            await workflow_agents.refresh_versions(db, user, row)
        if row.kind == "outreach":
            row.state = {k: v for k, v in row.state.items() if k != "errors"}
            if action == "continue" and not workflow_outreach.errors(row):
                previous_step = row.step
                try:
                    async with db.begin_nested():
                        await workflow_outreach.prepare(db, user, session, row)
                except (HTTPException, ValueError, TimeoutError, httpx.HTTPError) as exc:
                    if isinstance(exc, HTTPException) and exc.status_code in {401, 403}:
                        raise
                    await db.refresh(row)
                    row.step, row.status = previous_step, "failed"
                    row.state = {
                        **row.state,
                        "errors": {
                            "template": str(exc.detail)
                            if isinstance(exc, HTTPException)
                            else "Şablonlar doğrulanamadı. Yeniden deneyin."
                        },
                    }
        if (
            action == "continue"
            and row.kind not in {"records", "outreach", "conversation"}
            and not (workflow_requests.errors(row) if row.kind == "request_update" else {})
            and not validate(row.kind, row.fields)
        ):
            if row.kind == "person":
                kind = row.fields["person_type"]
                authorize(user, kind)
                row.kind, row.fields = kind, {}
            else:
                row.step, row.status = "review", "ready"
                if row.kind == "owner_invite":
                    await workflow_owners.prepare(db, row)
                if row.kind == "request_update":
                    await workflow_requests.prepare(db, user, row)
                if row.kind in {"reply", "resume_bot"}:
                    await workflow_inbox.prepare(db, user, row)
                if row.kind == "member":
                    await workflow_members.prepare(db, user, row)
                if row.kind == "create_template":
                    await workflow_templates.prepare(db, user, row)
                if row.kind in workflow_agents.KINDS:
                    try:
                        async with db.begin_nested():
                            await workflow_agents.prepare(db, user, row)
                    except (HTTPException, ValueError, TimeoutError) as exc:
                        if isinstance(exc, HTTPException) and exc.status_code in {401, 403}:
                            raise
                        await db.refresh(row)
                        row.step, row.status = "details", "failed"
                        message = (
                            str(exc.detail)
                            if isinstance(exc, HTTPException)
                            else "Bilgiler doğrulanamadı. Alanları kontrol edip yeniden deneyin."
                        )
                        row.state = {
                            **row.state,
                            "errors": {"content": message},
                            "output": {"summary": message},
                        }
    elif action == "complete":
        if row.status != "ready" or row.step != "review" or validate(row.kind, row.fields):
            raise HTTPException(409, "Önce eksik bilgileri tamamlayıp inceleyin.")
        if row.kind == "reply":
            return await workflow_inbox.send(db, user, session, row, payload, request)
        if row.kind == "create_template":
            return await workflow_templates.submit(db, user, session, row, payload, request)
        if row.kind == "owner_invite":
            await workflow_owners.complete(db, user, row)
        elif row.kind == "request_update":
            await workflow_requests.complete(db, user, row)
        elif row.kind == "resume_bot":
            await workflow_inbox.resume(db, user, row)
        elif row.kind == "outreach":
            if workflow_outreach.errors(row):
                raise HTTPException(409, "Şablon alanlarını tamamlayın.")
            await workflow_outreach.complete(db, user, row)
        elif row.kind == "member":
            await workflow_members.complete(db, user, row)
        elif row.kind in workflow_agents.KINDS:
            await workflow_agents.complete(db, user, session, row)
        elif row.kind == "contact":
            row.result = await save_contact(db, user, row)
        elif row.kind == "invite":
            transaction: Any = TurnTransaction(db)
            invitation = await AuthService(transaction).create_invitation(
                user.tenant_id, user.id, InviteIn(**row.fields)
            )
            row.result = {
                "outcome": "invitation_ready",
                "invitation_id": str(invitation.id),
                "token": invitation.token,
                "email": invitation.email,
                "message": "Davet bağlantısı hazır. Davet henüz kabul edilmedi.",
            }
        elif row.kind == "create_agent":
            transaction = TurnTransaction(db)
            agent = await AgentService(transaction).create_agent(
                user.tenant_id, AgentIn(**row.fields), actor_id=user.id
            )
            row.result = {
                "outcome": "agent_created",
                "agent_id": str(agent.id),
                "message": f"{agent.name} asistanı oluşturuldu. Şirket bilgilerini ekleyebilirsiniz.",
            }
            session.context = {
                **session.context,
                "workspace": {**session.context.get("workspace", {}), "agent_id": str(agent.id)},
            }
        elif row.kind == "create_company":
            transaction = TurnTransaction(db)
            try:
                created = await platform.provision(
                    platform.ProvisionIn(
                        name=row.fields["name"],
                        slug=row.fields["slug"],
                        owner_email=row.fields["email"],
                    ),
                    transaction,
                    {"sub": str(user.id), "tid": str(user.tenant_id), "role": user.role.value},
                )
            finally:
                await set_tenant_context(db, user.tenant_id)
                await db.execute(
                    text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(user.id)}
                )
            row.result = {
                "outcome": "company_created",
                "tenant_id": str(created["tenant"].id),
                "token": created["invitation_token"],
                "message": f"{row.fields['name']} şirketi oluşturuldu. Sahibinin davet bağlantısı hazır; WhatsApp bağlantısı kurulmadı.",
            }
        if row.status not in {"failed", "running"}:
            row.status, row.step = "completed", "result"
    row.revision += 1
    await db.flush()
    return record(db, user, session, row, payload.client_operation_id, request)


async def execute_intent(db: Any, user: Any, session: Any, intent: Any, client_id: UUID) -> Any:
    if intent.workflow_action == "inspect":
        matches = [
            w
            for w in await list_views(db, user, session)
            if not intent.workflow_kind or w["kind"] == intent.workflow_kind
        ]
        return (
            "Kayıtlı işlemlerin güncel durumunu aşağıdaki kartlardan görebilirsiniz."
            if matches
            else "Bu sohbette kayıtlı bir işlem bulunamadı.",
            [],
            None,
            {"tool": "workflow", "status": "inspected", "count": len(matches)},
        )
    rows = list(
        (
            await db.scalars(
                select(Workflow).where(
                    Workflow.session_id == session.id,
                    Workflow.tenant_id == user.tenant_id,
                    Workflow.user_id == user.id,
                    Workflow.status.not_in(TERMINAL),
                )
            )
        ).all()
    )
    if intent.workflow_action == "start":
        result = await start(
            db,
            user,
            session,
            WorkflowStart(
                client_operation_id=client_id,
                kind=intent.workflow_kind or "person",
                fields=intent.workflow_fields,
            ),
            anchor_sequence=session.sequence + 1,
        )
    else:
        candidates = [
            r
            for r in rows
            if (r.kind == intent.workflow_kind if intent.workflow_kind else r.status in ACTIVE)
        ]
        if intent.workflow_action == "resume" and not intent.workflow_kind:
            candidates = [r for r in rows if r.status == "paused"]
        if len(candidates) != 1:
            return (
                "Devam etmek istediğiniz işlemi aşağıdaki kartlardan seçin.",
                [],
                None,
                {"tool": "workflow", "status": "selection_required"},
            )
        row = candidates[0]
        result = await act(
            db,
            user,
            session,
            row.id,
            WorkflowCommand(
                client_operation_id=client_id,
                expected_revision=row.revision,
                action=intent.workflow_action,
                fields=intent.workflow_fields,
            ),
        )
    reply = result["result"].get("message") or (
        "Bilgiler kaydedildi. Eksikleri karttan veya mesajla tamamlayabilirsiniz."
        if result["status"] == "awaiting_input"
        else "İşlem kartı güncellendi."
    )
    return (
        reply,
        [],
        {"status": result["status"], "workflow_id": result["id"]},
        {"tool": "workflow", "workflow_id": result["id"], "revision": result["revision"]},
    )
