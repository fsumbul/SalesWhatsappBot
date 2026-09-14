# ruff: noqa: RUF001
"""Request review cards use the canonical role, transition and revision boundary."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from src.modules.auth.models import User
from src.modules.selection.models import SelectionRequest
from src.modules.selection.review import ROLES, ReviewUpdate, apply_review

from .workflow_schema import WorkflowField

STATUS = {
    "waiting_review": "İnceleme bekliyor",
    "in_review": "İnceleniyor",
    "completed": "Tamamlandı",
    "cancelled": "İptal edildi",
}
OPERATIONS = {"status": "Durumu değiştir", "assign": "Sorumlu ata", "note": "İç not ekle"}
CONTROLS = [
    WorkflowField(key="request", label="Talep", required=True),
    WorkflowField(
        key="operation", label="Talep işlemi", control="select", required=True, options=OPERATIONS
    ),
    WorkflowField(key="status", label="Yeni durum", control="select", options=STATUS),
    WorkflowField(key="assignee", label="Sorumlu", control="select"),
    WorkflowField(key="note", label="İç not", control="textarea"),
]


async def own(db: Any, user: Any, value: str) -> Any:
    try:
        rid = UUID(value)
    except ValueError as exc:
        raise HTTPException(422, "Listeden bir talep seçin.") from exc
    request = await db.scalar(
        select(SelectionRequest)
        .where(SelectionRequest.id == rid, SelectionRequest.tenant_id == user.tenant_id)
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise HTTPException(404, "Talep bulunamadı.")
    return request


def controls(row: Any) -> list[WorkflowField]:
    key = {"status": "status", "assign": "assignee", "note": "note"}.get(
        row.fields.get("operation")
    )
    return [
        c.model_copy(
            update={
                "required": True,
                **({"options": row.state.get("assignees", {})} if c.key == "assignee" else {}),
            }
        )
        for c in CONTROLS
        if c.key == "operation" or c.key == key
    ]


async def initialize(db: Any, user: Any, row: Any) -> None:
    request = await own(db, user, row.fields.get("request", ""))
    users = await db.scalars(
        select(User).where(User.tenant_id == user.tenant_id, User.is_active.is_(True))
    )
    choices = {str(u.id): u.full_name or u.email for u in users if u.role.value in ROLES}
    row.state = {
        **row.state,
        "assignees": {"none": "Sorumlu atamasını kaldır", **choices},
        "output": {
            "summary": f"Talep {request.id} · {STATUS.get(request.status, request.status)}",
            "delivery_note": "Bu işlem müşteriye mesaj göndermez.",
        },
    }


def errors(row: Any) -> dict[str, str]:
    operation = row.fields.get("operation")
    key = {"status": "status", "assign": "assignee", "note": "note"}.get(operation)
    if key and not row.fields.get(key):
        return {key: "Bu alanı doldurun."}
    return {}


async def prepare(db: Any, user: Any, row: Any) -> None:
    request = await own(db, user, row.fields["request"])
    await initialize(db, user, row)
    operation = row.fields["operation"]
    if operation == "assign" and row.fields["assignee"] not in row.state["assignees"]:
        raise HTTPException(422, "Etkin bir ekip üyesi seçin.")
    before = (
        STATUS.get(request.status, request.status)
        if operation == "status"
        else (
            row.state["assignees"].get(str(request.assigned_to), "Atanmamış")
            if operation == "assign"
            else ""
        )
    )
    after = (
        STATUS[row.fields["status"]]
        if operation == "status"
        else (
            row.state["assignees"][row.fields["assignee"]]
            if operation == "assign"
            else row.fields["note"]
        )
    )
    row.state = {
        **row.state,
        "request_revision": request.revision,
        "changes": [{"label": OPERATIONS[operation], "before": before, "after": after}],
    }


async def complete(db: Any, user: Any, row: Any) -> None:
    data = {"revision": row.state["request_revision"]}
    operation = row.fields["operation"]
    if operation == "assign":
        data["assigned_to"] = None if row.fields["assignee"] == "none" else row.fields["assignee"]
    else:
        data[operation] = row.fields[operation]
    request = await apply_review(
        db,
        {"tid": str(user.tenant_id), "sub": str(user.id)},
        UUID(row.fields["request"]),
        ReviewUpdate.model_validate(data),
    )
    changed = request.revision != row.state["request_revision"]
    row.result = {
        "outcome": "request_updated" if changed else "no_change",
        "request_id": str(request.id),
        "message": "Talep güncellendi. Müşteriye mesaj gönderilmedi."
        if changed
        else "Talep zaten istenen durumda; değişiklik yapılmadı.",
    }


async def route_intent(db: Any, user: Any, session: Any, intent: Any) -> tuple[Any, str | None]:
    """Resolve identities from current tenant data; a natural command only opens review input."""
    from src.modules.selection.engine import normalize

    from .planner import Intent
    from .service import find_requests
    from .workflow_models import Workflow

    reading = intent.tool in {"select", "summary", "missing", "files", "conversation", "delivery"}
    if intent.tool not in OPERATIONS and not reading:
        return intent, None
    target = None
    if intent.target:
        matches = await find_requests(db, user, intent)
        if len(matches) == 1:
            target = matches[0]
    else:
        active = list(
            await db.scalars(
                select(Workflow).where(
                    Workflow.session_id == session.id,
                    Workflow.user_id == user.id,
                    Workflow.tenant_id == user.tenant_id,
                    Workflow.status.in_(["awaiting_input", "ready", "failed"]),
                )
            )
        )
        if len(active) == 1:
            current = active[0]
            if intent.tool == "delivery" and current.kind == "conversation":
                return Intent(
                    tool="workflow",
                    workflow_kind="conversation",
                    workflow_action="start",
                    workflow_fields={"conversation": current.fields["conversation"]},
                ), None
            rid = (
                current.fields.get("request")
                if (
                    current.kind == "request_update"
                    or current.fields.get("category") == "request_details"
                )
                else None
            )
            if current.kind == "records" and current.fields.get("category") in {
                "requests",
                "quotes",
            }:
                records = current.state.get("records", [])
                if current.state.get("total") == 1 and len(records) == 1:
                    rid = records[0]["id"]
            if rid:
                target = await own(db, user, rid)
    if target is None:
        return Intent(
            tool="workflow",
            workflow_kind="records",
            workflow_action="start",
            workflow_fields={"category": "requests", "q": intent.target or ""},
        ), ("İşlem yapılmadı. Listeden bir talep ve yapmak istediğiniz işlemi seçin.")
    if reading:
        if intent.tool == "conversation":
            return Intent(
                tool="workflow",
                workflow_kind="conversation",
                workflow_action="start",
                workflow_fields={"conversation": str(target.conversation_id)},
            ), None
        return Intent(
            tool="workflow",
            workflow_kind="records",
            workflow_action="start",
            workflow_fields={"category": "request_details", "request": str(target.id)},
        ), None
    fields = {"request": str(target.id), "operation": intent.tool}
    message = None
    if intent.tool == "assign":
        users = list(
            await db.scalars(
                select(User).where(User.tenant_id == user.tenant_id, User.is_active.is_(True))
            )
        )
        matches = [
            u
            for u in users
            if u.role.value in ROLES
            and normalize(intent.assignee) in {normalize(u.email), normalize(u.full_name or "")}
        ]
        if len(matches) == 1:
            fields["assignee"] = str(matches[0].id)
        else:
            message = "Sorumlu tekil bulunamadı. Karttan etkin bir ekip üyesi seçin."
    else:
        fields[intent.tool] = getattr(intent, intent.tool)
    return Intent(
        tool="workflow",
        workflow_kind="request_update",
        workflow_action="start",
        workflow_fields=fields,
    ), message


def missing_fields(request: Any) -> list[str]:
    answers = (request.confirmed_snapshot or {}).get("answers", request.answers)
    missing = []
    for step in request.definition.get("steps", []):
        if any(
            isinstance(answers.get(key), dict) and answers[key].get("value") in values
            for key, values in step.get("skip_if", {}).items()
        ):
            continue
        value = answers.get(step["id"])
        if not value or (
            isinstance(value, dict)
            and (value.get("status") in {"unknown", "drawing"} or value.get("value") == "unknown")
        ):
            missing.append(step["label"])
    return missing


async def details(db: Any, user: Any, rid: str, page: int) -> tuple[int, list[dict[str, Any]]]:
    from src.modules.selection.models import SelectionFile

    from .service import request_card
    from .workflow_inbox import delivery_summary

    request = await own(db, user, rid)
    card = request_card(request)
    missing = missing_fields(request)
    records = [
        {
            "id": str(request.id),
            "title": card["title"] or "Teknik talep",
            "subtitle": str(request.id),
            "details": {
                "Durum": STATUS.get(request.status, request.status),
                "Son mesaj": await delivery_summary(db, user, request.conversation_id),
                "Müşteri bilgileri": card["summary"],
                "Eksik bilgiler": ", ".join(missing)
                if missing
                else "Kayıtlı zorunlu alanlarda eksik görünmüyor; teknik uygunluk ayrıca incelenmelidir.",
                "İç notlar": "\n".join(
                    str(n["text"]) for n in request.internal_notes[-20:] if n.get("text")
                )
                or "İç not yok.",
                "Kapsam": "Müşterinin teknik talebi; fiyat veya uygunluk onayı değildir.",
            },
            "actions": [
                {"operation": "conversation", "label": "Konuşmayı aç"},
                *(
                    [{"operation": key, "label": label} for key, label in OPERATIONS.items()]
                    if user.role.value in ROLES and request.status != "draft"
                    else []
                ),
            ],
        }
    ]
    files = await db.scalars(
        select(SelectionFile)
        .where(SelectionFile.tenant_id == user.tenant_id, SelectionFile.request_id == request.id)
        .order_by(SelectionFile.created_at, SelectionFile.id)
    )
    for file in files:
        records.append(
            {
                "id": str(file.id),
                "title": file.filename,
                "subtitle": "Talep dosyası",
                "details": {"Tür": file.mime_type, "Boyut": f"{file.size_bytes} bayt"},
                "actions": [],
                **(
                    {"file": {"request_id": str(request.id), "file_id": str(file.id)}}
                    if user.role.value in ROLES
                    else {}
                ),
            }
        )
    return len(records), records[(page - 1) * 20 : page * 20]
