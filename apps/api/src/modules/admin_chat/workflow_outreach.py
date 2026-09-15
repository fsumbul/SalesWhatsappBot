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
from sqlalchemy import update

from src.modules.compliance.models import AuditLog
from src.modules.outreach.channel import resolve_channel

from . import campaign_imports, outbound
from .outbound_models import OutboundBatch, OutboundRecipient
from .workflow_schema import WorkflowField

CONTROLS = [
    WorkflowField(
        key="recipient_source",
        label="Alıcı kaynağı",
        control="select",
        required=True,
        options={"manual": "Numaraları elle yaz", "file": "CSV / XLSX dosyası"},
    ),
    WorkflowField(key="recipients", label="Alıcı telefonları", control="textarea", required=True),
    WorkflowField(
        key="country_code",
        label="Ülke kodu (iki harf, ör. TR)",
        required=True,
    ),
    WorkflowField(key="campaign_file", label="Telefon listesi dosyası", control="file", required=True),
    WorkflowField(key="phone_column", label="Telefon sütunu", control="select", required=True),
    WorkflowField(key="purpose", label="Gönderimin amacı"),
    WorkflowField(key="template", label="Meta onaylı şablon", control="select"),
    WorkflowField(
        key="consent_evidence", label="Tanıtım izninin kaynağı ve tarihi", control="textarea"
    ),
    WorkflowField(
        key="consent_source",
        label="İzin kaynağı",
        control="select",
        required=True,
        options={
            "web_form": "Web formu izni",
            "written_record": "Yazılı izin kaydı",
            "customer_relationship": "Mevcut müşteri kaydı",
            "other": "Diğer kayıtlı izin",
        },
    ),
    WorkflowField(key="consent_note", label="İzin notu (isteğe bağlı)", control="textarea"),
    WorkflowField(
        key="consent_confirmed",
        label="Bu liste için tanıtım izninin geçerli olduğunu beyan ediyorum.",
        control="checkbox",
        required=True,
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
    source = row.fields.get("recipient_source", "manual")
    if row.step == "details":
        # File outreach is a concrete canary, not a new universal artifact
        # surface. New cards outside its configured tenant/manager only offer
        # the existing manual-number path. A previously uploaded file stays
        # visible for its audit/status record even if the canary is later off.
        source_control = CONTROLS[0]
        if source != "file" and not row.state.get("campaign_import_enabled", False):
            source_control = source_control.model_copy(
                update={"options": {"manual": "Numaraları elle yaz"}}
            )
        result = [source_control]
        if source == "file":
            result.extend([CONTROLS[2], CONTROLS[3]])
            imported = row.state.get("campaign_import", {})
            if imported.get("status") == "awaiting_mapping":
                result.append(
                    CONTROLS[4].model_copy(
                        update={
                            "options": {
                                str(column): str(column)
                                for column in imported.get("columns", [])
                            }
                        }
                    )
                )
        else:
            result.append(CONTROLS[1])
        result.append(CONTROLS[5])
        return result
    if row.step == "compose":
        return [
            CONTROLS[6].model_copy(update={"options": options, "required": True}),
            *[
                WorkflowField(key="var_" + key, label=f"Şablon alanı {key}", required=True)
                for key in (selected or {}).get("variables", [])
            ],
            *(
                [CONTROLS[7]]
                if source != "file"
                else [CONTROLS[8], CONTROLS[9], CONTROLS[10]]
            ),
        ]
    # Review/result controls are only used to label saved values; keep them
    # specific to the chosen path instead of exposing a generic file schema.
    return [
        CONTROLS[0],
        *( [CONTROLS[1]] if source != "file" else [CONTROLS[2]] ),
        CONTROLS[5],
        CONTROLS[6].model_copy(update={"options": options, "required": True}),
        *[
            WorkflowField(key="var_" + key, label=f"Şablon alanı {key}", required=True)
            for key in (selected or {}).get("variables", [])
        ],
        *(
            [CONTROLS[7]]
            if source != "file"
            else [CONTROLS[8], CONTROLS[9], CONTROLS[10]]
        ),
    ]


def output(row: Any) -> dict[str, Any]:
    result = dict(row.state.get("output", {}))
    imported = row.state.get("campaign_import")
    if isinstance(imported, dict):
        # The import view contains aggregates and masked examples only. It is
        # safe for the workflow card and never becomes LLM context.
        result["campaign_import"] = imported
        if imported.get("status") and not result.get("summary"):
            result["summary"] = {
                "queued": "Dosya ayrıştırma kuyruğunda.",
                "parsing": "Dosya ayrıştırılıyor.",
                "awaiting_mapping": "Telefon sütununu seçin.",
                "ready": "Dosya incelemeye hazır.",
                "failed": imported.get("failure_reason") or "Dosya içe aktarılamadı.",
            }.get(str(imported.get("status")), "Dosya durumu yenileniyor.")
    selected = template(row)
    if selected:
        body = selected["body"]
        for key in selected["variables"]:
            def replacement(_: re.Match[str], variable: str = key) -> str:
                return row.fields.get("var_" + variable) or "{{" + variable + "}}"

            body = re.sub(
                r"{{\s*" + re.escape(key) + r"\s*}}",
                replacement,
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
    # Existing chat-created outreach workflows are manual by default. Persist
    # that choice so the form does not visually show an empty source while
    # validation silently assumes manual entry.
    if "recipient_source" not in row.fields:
        row.fields = {"recipient_source": "manual", **row.fields}
    import_enabled = campaign_imports.campaign_imports_enabled_for(user.tenant_id, user.id)
    if row.fields.get("recipient_source") == "file" and not import_enabled:
        raise HTTPException(404, "Dosya kaynaklı kampanya bu hesap için henüz etkin değil.")
    try:
        sender = await resolve_channel(db, user.tenant_id)
        templates = await outbound.meta_templates(sender)
    except (HTTPException, ValueError, TimeoutError, httpx.HTTPError) as exc:
        if isinstance(exc, HTTPException) and exc.status_code in {401, 403}:
            raise
        row.state = {
            "campaign_import_enabled": import_enabled,
            "output": {
                "summary": "Meta şablon listesine ulaşılamadı. Devam et ile yeniden deneyebilirsiniz."
            }
        }
        return
    row.state = {
        "templates": templates,
        "sender_id": str(sender.id),
        "campaign_import_enabled": import_enabled,
    }
    if len(templates) == 1 and not row.fields.get("template"):
        row.fields = {**row.fields, "template": templates[0]["id"]}


def errors(row: Any) -> dict[str, str]:
    result = {}
    source = row.fields.get("recipient_source", "manual")
    if source not in {"manual", "file"}:
        result["recipient_source"] = "Alıcı kaynağını seçin."
    elif source == "manual":
        try:
            outbound.phones(re.split(r"[\s,;]+", row.fields.get("recipients", "").strip()))
        except (ValueError, HTTPException):
            result["recipients"] = "En fazla 100 telefon numarasını +ülke koduyla yazın."
    else:
        imported = row.state.get("campaign_import") or {}
        status = imported.get("status")
        if not row.state.get("import_id"):
            result["campaign_file"] = "CSV veya XLSX telefon listesi yükleyin."
        elif status == "awaiting_mapping":
            result["phone_column"] = "Dosyadaki telefon sütununu seçin."
        elif status in {"queued", "parsing"}:
            result["campaign_file"] = "Dosya halen ayrıştırılıyor."
        elif status == "failed":
            result["campaign_file"] = str(
                imported.get("failure_reason") or "Dosya içe aktarılamadı."
            )
        elif status != "ready":
            result["campaign_file"] = "Dosya durumu henüz doğrulanmadı."
        elif int(imported.get("counts", {}).get("eligible", 0)) < 1:
            result["campaign_file"] = "Gönderime uygun telefon numarası bulunamadı."
        elif int(imported.get("counts", {}).get("eligible", 0)) > campaign_imports.campaign_import_recipient_limit():
            result["campaign_file"] = (
                "Bu aşamadaki kampanya alıcı limiti "
                f"{campaign_imports.campaign_import_recipient_limit()} kişidir."
            )
    if row.step != "details":
        selected = template(row)
        if not selected:
            result["template"] = "Onaylı bir şablon seçin."
        for key in (selected or {}).get("variables", []):
            if not row.fields.get("var_" + key, "").strip():
                result["var_" + key] = "Bu şablon alanını doldurun."
        if source == "file":
            if row.fields.get("consent_source", "").strip() not in campaign_imports.CONSENT_SOURCES:
                result["consent_source"] = "İzin kaynağını seçin."
            if row.fields.get("consent_confirmed") != "true":
                result["consent_confirmed"] = "Gönderimden önce izin beyanını onaylayın."
        else:
            evidence = row.fields.get("consent_evidence", "")
            if evidence and len(evidence) < 10:
                result["consent_evidence"] = "İzin kaynağını ve tarihini belirtin."
    return result


def validate_patch(row: Any, fields: dict[str, str]) -> None:
    """Reject source-artifact changes before a draft batch is invalidated."""

    if (
        row.fields.get("recipient_source") == "file"
        and row.state.get("import_id")
        and "country_code" in fields
        and fields["country_code"].strip().upper() != row.fields.get("country_code", "").upper()
    ):
        # Phone normalization is part of the immutable uploaded artifact.
        # Silently changing the form value after parsing would make the card
        # claim a country different from the recipients in the outbox.
        raise HTTPException(409, "Ülke kodunu değiştirmek için dosyayı yeniden yükleyin.")


async def invalidate(db: Any, user: Any, row: Any) -> None:
    if row.state.get("batch_id"):
        batch = await outbound.own_batch(db, user, UUID(row.state["batch_id"]), lock=True)
        if batch.status != "draft":
            raise HTTPException(409, "Başlatılmış gönderimin içeriği değiştirilemez.")
        batch.status = "cancelled"
        await db.execute(
            update(OutboundRecipient)
            .where(
                OutboundRecipient.batch_id == batch.id,
                OutboundRecipient.tenant_id == user.tenant_id,
                OutboundRecipient.status.in_(["draft", "queued"]),
            )
            .values(status="cancelled")
        )
    # A mapping/edit must invalidate an unqueued draft batch, but not erase a
    # private uploaded source file. It stays tenant-scoped and expires by its
    # own 30-day retention task; the workflow can safely re-parse it.
    row.state = {
        k: v
        for k, v in row.state.items()
        if k
        in {
            "templates",
            "sender_id",
            "campaign_import_enabled",
            "import_id",
            "import_status",
            "campaign_import",
        }
    }


def _import_state(row: Any, imported: Any) -> None:
    view = campaign_imports.public_import_view(imported)
    row.state = {
        **row.state,
        "import_id": str(imported.id),
        "import_status": imported.status,
        "campaign_import": view,
        "output": {
            **row.state.get("output", {}),
            "campaign_import": view,
            "summary": (imported.summary or {}).get("message")
            or "Dosya durumu yenileniyor.",
        },
    }


async def sync_import(db: Any, user: Any, row: Any) -> None:
    """Project a stable import snapshot into a session-serialized workflow card.

    All HTTP callers already hold the owning chat-session lock. Locking the
    source row after that parent lock gives retention cleanup the same order
    (session -> workflow -> import), so a two-second poll cannot reintroduce
    a just-expired filename/header/example into JSONB snapshots.
    """

    import_id = row.state.get("import_id")
    if not import_id:
        return
    imported = await campaign_imports.get_campaign_import(
        db,
        tenant_id=user.tenant_id,
        import_id=UUID(import_id),
        workflow_id=row.id,
        lock=True,
    )
    if imported is None:
        # Retention cleanup has already removed the source/rows. Do not keep
        # a stale card replica (filename, headers, masked examples) in the
        # workflow JSON simply because this card is polled after expiry.
        row.fields = campaign_imports.redact_expired_import_fields(row.fields)
        row.state = campaign_imports.redact_expired_import_artifacts(
            row.state, UUID(import_id)
        )
        row.state = {
            **row.state,
            "import_status": "expired",
            "campaign_import": campaign_imports.expired_import_view(),
        }
        if not row.state.get("batch_id"):
            row.step, row.status = "details", "failed"
        return
    _import_state(row, imported)
    if row.state.get("batch_id"):
        return
    if imported.status in {
        campaign_imports.CampaignImportStatus.QUEUED.value,
        campaign_imports.CampaignImportStatus.PARSING.value,
    }:
        row.step, row.status = "details", "running"
    elif imported.status == campaign_imports.CampaignImportStatus.AWAITING_MAPPING.value:
        row.step, row.status = "details", "awaiting_input"
    elif imported.status == campaign_imports.CampaignImportStatus.FAILED.value:
        row.step, row.status = "details", "failed"
    elif imported.status == campaign_imports.CampaignImportStatus.READY.value and row.step == "details":
        row.status = "awaiting_input"


async def select_import_phone_column(db: Any, user: Any, row: Any) -> None:
    """Queue a new parse only after a manager explicitly maps the header."""

    import_id = row.state.get("import_id")
    if not import_id:
        raise HTTPException(409, "Önce bir telefon listesi yükleyin.")
    if not campaign_imports.campaign_imports_enabled_for(user.tenant_id, user.id):
        raise HTTPException(404, "Dosya kaynaklı kampanya bu hesap için henüz etkin değil.")
    selected = row.fields.get("phone_column", "")
    imported = await campaign_imports.select_phone_column(
        db,
        tenant_id=user.tenant_id,
        import_id=UUID(import_id),
        workflow_id=row.id,
        phone_column=selected,
    )
    # The HTTP route enqueues after committing this mapping and its workflow
    # action receipt. Starting a Celery task here could let it observe the
    # previous selected column under a fast local broker.
    _import_state(row, imported)
    row.step, row.status = "details", "running"


async def prepare(db: Any, user: Any, session: Any, row: Any) -> None:
    outbound.manager(user)
    if row.step == "details":
        if row.fields.get("recipient_source", "manual") == "file":
            if not campaign_imports.campaign_imports_enabled_for(user.tenant_id, user.id):
                raise HTTPException(404, "Dosya kaynaklı kampanya bu hesap için henüz etkin değil.")
            await sync_import(db, user, row)
            import_view = row.state.get("campaign_import", {})
            if import_view.get("status") != campaign_imports.CampaignImportStatus.READY.value:
                raise HTTPException(409, "Dosya incelemeye henüz hazır değil.")
            eligible_count = int(import_view.get("counts", {}).get("eligible", 0))
            if eligible_count < 1:
                raise HTTPException(422, "Gönderime uygun telefon numarası bulunamadı.")
            if eligible_count > campaign_imports.campaign_import_recipient_limit():
                raise HTTPException(
                    422,
                    "Bu aşamadaki kampanya alıcı limitini aşan dosya gönderime hazırlanamaz.",
                )
        sender = await resolve_channel(db, user.tenant_id)
        templates = await outbound.meta_templates(sender)
        if not templates:
            raise HTTPException(
                409,
                "Meta onaylı uygun şablon bulunamadı. Yeni şablon ekleyip Meta onayına gönderebilirsiniz.",
            )
        row.state = {
            **{
                key: value
                for key, value in row.state.items()
                if key
                in {"campaign_import_enabled", "import_id", "import_status", "campaign_import"}
            },
            "templates": templates,
            "sender_id": str(sender.id),
            "output": {
                **row.state.get("output", {}),
                "summary": "Onaylı şablonu seçin, alanları ve alıcıların izin durumunu inceleyin.",
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
    source = row.fields.get("recipient_source", "manual")
    imported: campaign_imports.CampaignImport | None = None
    consent_evidence: str | None
    if source == "file":
        if not campaign_imports.campaign_imports_enabled_for(user.tenant_id, user.id):
            raise HTTPException(404, "Dosya kaynaklı kampanya bu hesap için henüz etkin değil.")
        import_id = row.state.get("import_id")
        if not import_id:
            raise HTTPException(409, "İçe aktarma bulunamadı.")
        imported = await campaign_imports.record_consent(
            db,
            tenant_id=user.tenant_id,
            import_id=UUID(import_id),
            workflow_id=row.id,
            consent_source=row.fields["consent_source"],
            consent_note=row.fields.get("consent_note"),
        )
        eligible_count = int(imported.eligible_count)
        if not 1 <= eligible_count <= campaign_imports.campaign_import_recipient_limit():
            raise HTTPException(
                422,
                "Gönderime uygun alıcı sayısı canlı kampanya limiti içinde olmalı.",
            )
        if not imported.consent_source:
            raise HTTPException(422, "İzin kaynağını belirtin.")
        consent_evidence = "Yönetici beyanı: " + imported.consent_source
        if imported.consent_note:
            consent_evidence += " · " + imported.consent_note
    else:
        numbers = outbound.phones(re.split(r"[\s,;]+", row.fields["recipients"].strip()))
        consent_evidence = row.fields.get("consent_evidence") or None
    batch = OutboundBatch(
        tenant_id=user.tenant_id,
        user_id=user.id,
        session_id=session.id,
        sender_id=UUID(row.state["sender_id"]),
        template=selected,
        variables={key: row.fields["var_" + key] for key in selected["variables"]},
        consent_evidence=consent_evidence,
        campaign_import_id=imported.id if imported else None,
        consent_source=imported.consent_source if imported else None,
        consent_note=imported.consent_note if imported else None,
        source_hash=imported.sha256 if imported else None,
        # File recipients are materialized by the durable worker in bounded
        # chunks. The workflow HTTP transaction never loops through 10,000
        # phones or runs their eligibility queries.
        status="preparing" if imported else "draft",
    )
    db.add(batch)
    await db.flush()
    if not imported:
        for phone in numbers:
            db.add(
                OutboundRecipient(
                    tenant_id=user.tenant_id, batch_id=batch.id, phone=phone, status="draft"
                )
            )
    await db.flush()
    row.state = {
        **row.state,
        "batch_id": str(batch.id),
        **({"background_running": True} if imported else {}),
    }
    if imported:
        db.add(
            AuditLog(
                tenant_id=user.tenant_id,
                actor_id=user.id,
                action="campaign_import_consent_attested",
                entity="campaign_import",
                entity_id=str(imported.id),
                meta={
                    "source": imported.consent_source,
                    "note_recorded": bool(imported.consent_note),
                    "consent_confirmed": True,
                    "source_hash": imported.sha256,
                    "eligible_count": int(imported.eligible_count),
                },
            )
        )
    if imported:
        row.step, row.status = "review", "running"
        row.result = {
            "outcome": "preparing",
            "batch_id": str(batch.id),
            "message": "Alıcı listesi arka planda hazırlanıyor; inceleme kartı hazır olduğunda onaylayabilirsiniz.",
        }
    else:
        row.step, row.status = "review", "ready"
    await sync(db, user, row)


async def complete(db: Any, user: Any, row: Any) -> None:
    batch = await outbound.own_batch(db, user, UUID(row.state["batch_id"]), lock=True)
    await outbound.queue_batch(db, user, batch)
    row.status, row.step = "running", "result"
    row.result = (
        {
            "outcome": "queueing",
            "batch_id": str(batch.id),
            "message": "Dosya kampanyası kuyruk ve kapasite doğrulamasına alındı. Meta'ya henüz gönderilmedi.",
        }
        if outbound.is_campaign_import_batch(batch)
        else {
            "outcome": "queued",
            "batch_id": str(batch.id),
            "message": "Uygun alıcılar gönderim kuyruğuna alındı. Henüz gönderildi veya teslim edildi sayılmaz.",
        }
    )
    await sync(db, user, row)


async def cancel(db: Any, user: Any, row: Any) -> None:
    if row.state.get("batch_id"):
        batch = await outbound.own_batch(db, user, UUID(row.state["batch_id"]), lock=True)
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
    imported_batch = outbound.is_campaign_import_batch(batch)
    if imported_batch and batch.status in {"preparing", "queueing"}:
        preparing = batch.status == "preparing"
        row.step, row.status = ("review", "running") if preparing else ("result", "running")
        row.result = {
            "outcome": batch.status,
            "batch_id": str(batch.id),
            "message": (
                "Alıcı listesi arka planda hazırlanıyor."
                if preparing
                else "Gönderim kuyruğu ve kapasitesi arka planda doğrulanıyor."
            ),
        }
        # The existing two-second workflow poll recognizes ``Kuyrukta``. Keep
        # one synthetic progress record until real, bounded recipient samples
        # are available; it contains no phone or file-derived value.
        row.state = {
            **row.state,
            "background_running": True,
            "output": {
                "summary": row.result["message"],
                "delivery_note": "Kuyruk, Meta kabulü, gönderilme ve teslim edilme ayrı durumlardır.",
                **(
                    {"campaign_import": row.state["campaign_import"]}
                    if row.state.get("campaign_import")
                    else {}
                ),
            },
            "records": [
                {
                    "id": "campaign-progress",
                    "title": "Dosya kampanyası hazırlanıyor",
                    "subtitle": "Kuyrukta",
                    "details": {"Durum": row.result["message"]},
                    "actions": [],
                }
            ],
            "delivery_counts": {batch.status: 1},
        }
        return
    card = await outbound.batch_card(db, batch)
    row.state = {
        **row.state,
        "output": {
            "summary": card["summary"],
            "delivery_note": "Kuyruk, Meta kabulü, gönderilme ve teslim edilme ayrı durumlardır.",
            **(
                {"campaign_import": row.state["campaign_import"]}
                if row.state.get("campaign_import")
                else {}
            ),
            **(
                {"recipient_sample_truncated": card["recipient_sample_truncated"]}
                if imported_batch
                else {}
            ),
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
    statuses = set(card["counts"])
    if imported_batch and batch.status == "draft" and row.result.get("outcome") == "preparing":
        row.step, row.status, row.result = "review", "ready", {}
        row.state = {**row.state, "background_running": False}
    elif imported_batch and batch.status == "failed":
        row.step, row.status = "result", "failed"
        row.result = {
            "outcome": "failed",
            "batch_id": str(batch.id),
            "message": "Dosya kampanyası doğrulanamadı; alıcı kartındaki engel bilgisini inceleyin.",
        }
        row.state = {**row.state, "background_running": False}
    if batch.status == "queued" and statuses and not statuses & {"queued", "sending"}:
        row.status = "completed"
        row.result = {
            "outcome": "processed_with_errors"
            if statuses & {"failed", "ambiguous", "blocked"}
            else "processed",
            "batch_id": str(batch.id),
            "message": "Gönderim kuyruğu işlendi. Alıcı bazındaki güncel durumlar aşağıda; Meta kabulü teslimat değildir.",
        }
        row.state = {**row.state, "background_running": False}
    elif imported_batch and batch.status == "queued":
        row.state = {**row.state, "background_running": True}
    if row.status == "paused" and batch.status == "queued":
        row.state = {**row.state, "background_running": True}
