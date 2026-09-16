# ruff: noqa: RUF001
"""Reviewed Meta template creation; approved send content is never edited here."""

import re
import time
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select, text

from src.core.db import set_tenant_context
from src.integrations.whatsapp import WhatsAppClient
from src.modules.outreach.channel import resolve_channel

from .models import AdminChatSession
from .workflow_schema import WorkflowField

CONTROLS = [
    WorkflowField(key="name", label="Şablon adı (küçük harf ve alt çizgi)", required=True),
    WorkflowField(
        key="language",
        label="Dil",
        control="select",
        required=True,
        options={
            "tr": "Türkçe",
            "en": "İngilizce",
            "en_US": "İngilizce (ABD)",
            "de": "Almanca",
            "ar": "Arapça",
        },
    ),
    WorkflowField(
        key="template_category",
        label="Kategori",
        control="select",
        required=True,
        options={"MARKETING": "Tanıtım / pazarlama", "UTILITY": "İşlem bilgilendirmesi"},
    ),
    WorkflowField(key="header", label="Başlık (isteğe bağlı)"),
    WorkflowField(
        key="body",
        label="Mesaj metni; değişkenler için {{1}}, {{2}}",
        control="textarea",
        required=True,
    ),
    WorkflowField(key="footer", label="Alt bilgi (isteğe bağlı)"),
    WorkflowField(
        key="buttons",
        label="Hızlı yanıt düğmeleri (isteğe bağlı, her satıra bir düğme)",
        control="textarea",
    ),
]
STATUS_LABELS = {
    "APPROVED": "Meta onayladı",
    "PENDING": "Meta onayı bekleniyor",
    "REJECTED": "Meta reddetti",
    "PAUSED": "Meta duraklattı",
    "DISABLED": "Meta devre dışı bıraktı",
}


def variables(fields: dict[str, str]) -> list[str]:
    return sorted(set(re.findall(r"{{([1-9][0-9]*)}}", fields.get("body", ""))), key=int)


def controls(row: Any) -> list[WorkflowField]:
    return [
        *CONTROLS,
        *[
            WorkflowField(
                key="example_" + key,
                label="Meta incelemesi için örnek {{" + key + "}}",
                required=True,
            )
            for key in variables(row.fields)
        ],
    ]


def errors(fields: dict[str, str]) -> dict[str, str]:
    result = {}
    if not re.fullmatch(r"[a-z0-9_]{1,512}", fields.get("name", "")):
        result["name"] = "Yalnız küçük harf, rakam ve alt çizgi kullanın."
    body = fields.get("body", "")
    keys = variables(fields)
    if not body or len(body) > 1024:
        result["body"] = "Mesaj metni 1–1024 karakter olmalı."
    remaining = re.sub(r"{{[1-9][0-9]*}}", "", body)
    if (
        "{" in remaining
        or "}" in remaining
        or list(map(int, keys)) != list(range(1, len(keys) + 1))
    ):
        result["body"] = "Değişkenleri {{1}}, {{2}} şeklinde, atlamadan numaralandırın."
    for key in keys:
        if not fields.get("example_" + key, "").strip():
            result["example_" + key] = "Meta incelemesi için örnek değer gerekli."
    for key in ("header", "footer"):
        if len(fields.get(key, "")) > 60 or "{" in fields.get(key, ""):
            result[key] = "En fazla 60 karakter sabit metin yazın."
    buttons = fields.get("buttons", "").splitlines()
    if len(buttons) > 3 or any(not b.strip() or len(b.strip()) > 25 or "{" in b for b in buttons):
        result["buttons"] = "En fazla 3 sabit hızlı yanıt düğmesi; her biri 1–25 karakter."
    return result


def payload(fields: dict[str, str]) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    if fields.get("header"):
        components.append({"type": "HEADER", "format": "TEXT", "text": fields["header"]})
    body: dict[str, Any] = {"type": "BODY", "text": fields["body"]}
    keys = variables(fields)
    if keys:
        body["example"] = {"body_text": [[fields["example_" + key] for key in keys]]}
    components.append(body)
    if fields.get("footer"):
        components.append({"type": "FOOTER", "text": fields["footer"]})
    if fields.get("buttons"):
        components.append(
            {
                "type": "BUTTONS",
                "buttons": [
                    {"type": "QUICK_REPLY", "text": label.strip()}
                    for label in fields["buttons"].splitlines()
                ],
            }
        )
    return {
        "name": fields["name"],
        "language": fields["language"],
        "category": fields["template_category"],
        "components": components,
    }


def preview(fields: dict[str, str]) -> dict[str, Any]:
    body = fields.get("body", "")
    for key in variables(fields):
        body = body.replace("{{" + key + "}}", fields.get("example_" + key) or "{{" + key + "}}")
    return {
        "template_preview": {
            "name": fields.get("name", ""),
            "language": fields.get("language", ""),
            "category": fields.get("template_category", ""),
            "header": fields.get("header", ""),
            "body": body,
            "footer": fields.get("footer", ""),
            "buttons": fields.get("buttons", "").splitlines(),
            "status": "Taslak · henüz Meta’ya gönderilmedi",
        },
        "summary": "Örnek değerlerle önizleme. Onayınızdan sonra şablon Meta incelemesine gönderilir.",
    }


async def catalog(sender: Any) -> list[dict[str, Any]]:
    client = WhatsAppClient(phone_number_id=sender.phone_number_id)
    result = []
    after = None
    for _ in range(10):
        data = await client.business_read(
            sender.business_account_id,
            "id,name,language,status,category,components",
            edge="message_templates",
            after=after,
        )
        result.extend(data.get("data", []))
        if not data.get("paging", {}).get("next"):
            return result
        after = data.get("paging", {}).get("cursors", {}).get("after")
        if not after:
            break
    raise ValueError("Template catalog incomplete")


async def prepare(db: Any, user: Any, row: Any) -> None:
    sender = await resolve_channel(db, user.tenant_id)
    current = await catalog(sender)
    if any(
        t.get("name") == row.fields["name"] and t.get("language") == row.fields["language"]
        for t in current
    ):
        raise HTTPException(
            409, "Bu ad ve dilde bir Meta şablonu zaten var. Yeni şablon için başka ad seçin."
        )
    row.state = {
        "sender_id": str(sender.id),
        "waba_id": sender.business_account_id,
        "submission": payload(row.fields),
        "output": preview(row.fields),
    }


async def submit(
    db: Any, user: Any, session: Any, row: Any, command: Any, request: Any
) -> dict[str, Any]:
    from . import workflows

    sender = await resolve_channel(db, user.tenant_id)
    if sender.business_account_id != row.state.get("waba_id") or payload(
        row.fields
    ) != row.state.get("submission"):
        raise HTTPException(409, "Önizleme veya WhatsApp bağlantısı değişti. Yeniden inceleyin.")
    waba_id = sender.business_account_id
    if not waba_id:
        raise HTTPException(409, "WhatsApp iş hesabı bağlantısı eksik. Yeniden inceleyin.")
    submission = row.state["submission"]
    row.status, row.step = "running", "result"
    row.result = {
        "outcome": "submitting",
        "message": "Şablon Meta’ya iletiliyor; tekrar denemek ikinci başvuru oluşturmaz.",
    }
    row.revision += 1
    receipt = workflows.record(db, user, session, row, command.client_operation_id, request)
    await db.commit()  # Durable receipt BEFORE the only external POST.
    outcome: dict[str, str]
    try:
        response = await WhatsAppClient(
            phone_number_id=sender.phone_number_id
        ).create_template_once(waba_id, submission)
        if not str(response.get("id", "")).isdigit():
            raise ValueError("Missing Meta template ID")
        status = str(response.get("status", "PENDING"))
        outcome = {
            "outcome": "submitted",
            "template_id": str(response["id"]),
            "meta_status": status,
            "message": STATUS_LABELS.get(status, "Şablon Meta incelemesine iletildi."),
        }
    except httpx.HTTPStatusError as exc:
        if 400 <= exc.response.status_code < 500 and exc.response.status_code not in {408, 429}:
            try:
                meta_error = exc.response.json().get("error", {})
                explanation = str(meta_error.get("error_user_msg", ""))[:600]
            except (ValueError, AttributeError):
                explanation = ""
            outcome = {
                "outcome": "rejected",
                "meta_status": "REJECTED",
                "message": explanation or "Meta başvuruyu kabul etmedi. Şablon bilgilerini ve hesap izinlerini kontrol edin.",
            }
        else:
            outcome = {
                "outcome": "ambiguous",
                "message": "Meta yanıtı alınamadı. Başvuru tekrar gönderilmeden Meta’daki durum kontrol edilecek.",
            }
    except (httpx.HTTPError, ValueError, TimeoutError):
        outcome = {
            "outcome": "ambiguous",
            "message": "Meta yanıtı alınamadı. Başvuru tekrar gönderilmeden Meta’daki durum kontrol edilecek.",
        }
    await set_tenant_context(db, user.tenant_id)
    await db.execute(
        text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(user.id)}
    )
    await db.execute(
        select(AdminChatSession.id)
        .where(
            AdminChatSession.id == session.id,
            AdminChatSession.user_id == user.id,
            AdminChatSession.tenant_id == user.tenant_id,
        )
        .with_for_update()
    )
    await db.refresh(row)
    await db.refresh(session)
    row.result = outcome
    if outcome.get("meta_status") in {"APPROVED", "REJECTED"}:
        row.status = "completed"
    row.state = {
        **row.state,
        "checked_at": time.time(),
        "output": {
            **row.state["output"],
            "summary": outcome["message"],
            "template_preview": {
                **row.state["output"]["template_preview"],
                "status": outcome["message"],
            },
        },
    }
    row.revision += 1
    await db.flush()
    return receipt


async def sync(db: Any, user: Any, row: Any) -> None:
    if row.status not in {"running", "paused"} or not row.result.get("outcome") or time.time() - row.state.get("checked_at", 0) < 30:
        return
    sender = await resolve_channel(db, user.tenant_id)
    if sender.business_account_id != row.state.get("waba_id"):
        return
    try:
        candidates = await catalog(sender)
    except (httpx.HTTPError, ValueError, TimeoutError):
        return
    found = next(
        (
            t
            for t in candidates
            if (
                str(t.get("id")) == row.result.get("template_id")
                if row.result.get("template_id")
                else t.get("name") == row.fields["name"]
                and t.get("language") == row.fields["language"]
                and t.get("components") == row.state["submission"]["components"]
            )
        ),
        None,
    )
    row.state = {**row.state, "checked_at": time.time()}
    if not found:
        return
    status = str(found.get("status", "PENDING"))
    row.result = {
        "outcome": "submitted",
        "template_id": str(found["id"]),
        "meta_status": status,
        "message": STATUS_LABELS.get(status, "Meta durumu: " + status),
    }
    if status in {"APPROVED", "REJECTED", "PAUSED", "DISABLED"}:
        row.status = "completed"
    row.state = {
        **row.state,
        "output": {
            **row.state["output"],
            "summary": row.result["message"],
            "template_preview": {
                **row.state["output"]["template_preview"],
                "status": row.result["message"],
            },
        },
    }
