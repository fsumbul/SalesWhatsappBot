# ruff: noqa: RUF001
"""Progressive marketing preparation over the existing durable at-most-once outbox.

This adapter never POSTs a message. It uses the real session for atomic queueing;
the existing worker commits SENDING before its one external POST.
"""

import re
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from src.modules.outreach.channel import resolve_channel

from . import outbound
from .outbound_models import OutboundBatch, OutboundRecipient
from .workflow_schema import WorkflowField

CONTROLS = [
    WorkflowField(key="recipients", label="Alıcı telefonları", control="textarea", required=True),
    WorkflowField(key="purpose", label="Gönderimin amacı"),
    WorkflowField(key="template", label="Meta onaylı şablon", control="select"),
    WorkflowField(
        key="consent_evidence", label="Tanıtım izninin kaynağı ve tarihi", control="textarea"
    ),
]
DELIVERY = {
    "draft": "Hazırlanıyor",
    "queued": "Kuyrukta",
    "sending": "Gönderiliyor",
    "accepted": "Meta kabul etti",
    "sent": "Gönderildi",
    "delivered": "Teslim edildi",
    "read": "Okundu",
    "blocked": "Engellendi",
    "failed": "Başarısız",
    "ambiguous": "Sonuç belirsiz; tekrar gönderilmez",
    "cancelled": "İptal edildi",
}


def template(row: Any) -> Any:
    return next(
        (
            t
            for t in row.state.get("templates", [])
            if row.fields.get("template") in {t["id"], t["name"]}
        ),
        None,
    )


def controls(row: Any) -> list[WorkflowField]:
    options = {t["id"]: f"{t['name']} · {t['language']}" for t in row.state.get("templates", [])}
    selected = template(row)
    return [
        CONTROLS[0],
        CONTROLS[2].model_copy(update={"options": options, "required": True}),
        *[
            WorkflowField(key="var_" + key, label=f"Şablon alanı {key}", required=True)
            for key in (selected or {}).get("variables", [])
        ],
        *([CONTROLS[3]] if row.step != "details" else []),
    ]


def output(row: Any) -> dict[str, Any]:
    result = dict(row.state.get("output", {}))
    selected = template(row)
    if selected:
        body = selected["body"]
        for key in selected["variables"]:
            body = re.sub(
                r"{{\s*" + re.escape(key) + r"\s*}}",
                lambda _, key=key: row.fields.get("var_" + key) or "{{" + key + "}}",
                body,
            )
        result["template_preview"] = {
            **{k: selected.get(k, "") for k in ("name", "language", "header", "footer", "buttons")},
            "body": body,
            "status": "Meta onaylı · şablon metni değiştirilemez",
        }
    result.setdefault(
        "summary", "Meta onaylı bir şablon seçin. Yalnız değişken alanlarını doldurabilirsiniz."
    )
    return result


async def initialize(db: Any, user: Any, row: Any) -> None:
    try:
        sender = await resolve_channel(db, user.tenant_id)
        templates = await outbound.meta_templates(sender)
    except (HTTPException, ValueError, TimeoutError, httpx.HTTPError) as exc:
        if isinstance(exc, HTTPException) and exc.status_code in {401, 403}:
            raise
        row.state = {
            "output": {
                "summary": "Meta şablon listesine ulaşılamadı. Devam et ile yeniden deneyebilirsiniz."
            }
        }
        return
    row.state = {"templates": templates, "sender_id": str(sender.id)}
    if len(templates) == 1 and not row.fields.get("template"):
        row.fields = {**row.fields, "template": templates[0]["id"]}


def errors(row: Any) -> dict[str, str]:
    result = {}
    try:
        outbound.phones(re.split(r"[\s,;]+", row.fields.get("recipients", "").strip()))
    except (ValueError, HTTPException):
        result["recipients"] = "En fazla 100 telefon numarasını +ülke koduyla yazın."
    if row.step != "details":
        selected = template(row)
        if not selected:
            result["template"] = "Onaylı bir şablon seçin."
        for key in (selected or {}).get("variables", []):
            if not row.fields.get("var_" + key, "").strip():
                result["var_" + key] = "Bu şablon alanını doldurun."
    evidence = row.fields.get("consent_evidence", "")
    if evidence and len(evidence) < 10:
        result["consent_evidence"] = "İzin kaynağını ve tarihini belirtin."
    return result


async def invalidate(db: Any, user: Any, row: Any) -> None:
    if row.state.get("batch_id"):
        batch = await outbound.own_batch(db, user, UUID(row.state["batch_id"]), lock=True)
        if batch.status != "draft":
            raise HTTPException(409, "Başlatılmış gönderimin içeriği değiştirilemez.")
        batch.status = "cancelled"
        recipients = await db.scalars(
            select(OutboundRecipient).where(
                OutboundRecipient.batch_id == batch.id,
                OutboundRecipient.tenant_id == user.tenant_id,
            )
        )
        for recipient in recipients:
            recipient.status = "cancelled"
    row.state = {k: v for k, v in row.state.items() if k in {"templates", "sender_id"}}


async def prepare(db: Any, user: Any, session: Any, row: Any) -> None:
    outbound.manager(user)
    if row.step == "details":
        sender = await resolve_channel(db, user.tenant_id)
        templates = await outbound.meta_templates(sender)
        if not templates:
            raise HTTPException(
                409,
                "Meta onaylı uygun şablon bulunamadı. Yeni şablon ekleyip Meta onayına gönderebilirsiniz.",
            )
        row.state = {
            "templates": templates,
            "sender_id": str(sender.id),
            "output": {
                "summary": "Onaylı şablonu seçin, alanları ve alıcıların izin durumunu inceleyin."
            },
        }
        if len(templates) == 1:
            row.fields = {**row.fields, "template": templates[0]["id"]}
        row.step, row.status = "compose", "awaiting_input"
        return
    if row.step != "compose":
        raise HTTPException(409, "Gönderim incelemeye hazır. Son eylemi seçin.")
    selected = template(row)
    if selected is None:
        raise HTTPException(422, "Onaylı şablon seçin.")
    numbers = outbound.phones(re.split(r"[\s,;]+", row.fields["recipients"].strip()))
    batch = OutboundBatch(
        tenant_id=user.tenant_id,
        user_id=user.id,
        session_id=session.id,
        sender_id=UUID(row.state["sender_id"]),
        template=selected,
        variables={key: row.fields["var_" + key] for key in selected["variables"]},
        consent_evidence=row.fields.get("consent_evidence") or None,
        status="draft",
    )
    db.add(batch)
    await db.flush()
    for phone in numbers:
        db.add(
            OutboundRecipient(
                tenant_id=user.tenant_id, batch_id=batch.id, phone=phone, status="draft"
            )
        )
    await db.flush()
    row.state = {**row.state, "batch_id": str(batch.id)}
    row.step, row.status = "review", "ready"
    await sync(db, user, row)


async def complete(db: Any, user: Any, row: Any) -> None:
    batch = await outbound.own_batch(db, user, UUID(row.state["batch_id"]), lock=True)
    await outbound.queue_batch(db, user, batch)
    row.status, row.step = "running", "result"
    row.result = {
        "outcome": "queued",
        "batch_id": str(batch.id),
        "message": "Uygun alıcılar gönderim kuyruğuna alındı. Henüz gönderildi veya teslim edildi sayılmaz.",
    }
    await sync(db, user, row)


async def cancel(db: Any, user: Any, row: Any) -> None:
    if row.state.get("batch_id"):
        batch = await outbound.own_batch(db, user, UUID(row.state["batch_id"]), lock=True)
        rows = await db.scalars(
            select(OutboundRecipient)
            .where(
                OutboundRecipient.batch_id == batch.id,
                OutboundRecipient.tenant_id == user.tenant_id,
            )
            .with_for_update()
        )
        for recipient in rows:
            if recipient.status in {"draft", "queued"}:
                recipient.status = "cancelled"
        batch.status = "cancelled"
        await db.flush()
    row.result = {
        "outcome": "cancelled",
        "message": "Bekleyen gönderimler iptal edildi. Başlamış gönderimler geri alınmadı.",
    }
    row.status = "cancelled"
    await sync(db, user, row)


async def sync(db: Any, user: Any, row: Any) -> None:
    if not row.state.get("batch_id"):
        return
    batch = await outbound.own_batch(db, user, UUID(row.state["batch_id"]), lock=True)
    card = await outbound.batch_card(db, batch)
    row.state = {
        **row.state,
        "output": {
            "summary": card["summary"],
            "delivery_note": "Kuyruk, Meta kabulü, gönderilme ve teslim edilme ayrı durumlardır.",
        },
        "records": [
            {
                "id": r["phone"],
                "title": r["phone"],
                "subtitle": DELIVERY.get(r["status"], r["status"]),
                "details": {"Uygunluk": r["reason"] or "Engel kaydı yok"},
                "actions": [],
            }
            for r in card["recipients"]
        ],
        "delivery_counts": card["counts"],
    }
    statuses = {r["status"] for r in card["recipients"]}
    if batch.status == "queued" and statuses and not statuses & {"queued", "sending"}:
        row.status = "completed"
        row.result = {
            "outcome": "processed_with_errors"
            if statuses & {"failed", "ambiguous", "blocked"}
            else "processed",
            "batch_id": str(batch.id),
            "message": "Gönderim kuyruğu işlendi. Alıcı bazındaki güncel durumlar aşağıda; Meta kabulü teslimat değildir.",
        }
    if row.status == "paused" and batch.status == "queued":
        row.state = {**row.state, "background_running": True}
