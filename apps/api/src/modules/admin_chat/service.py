# ruff: noqa: RUF001
"""Server-owned tool execution; customer content is data and never a planner input."""

import re
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_, select

from src.modules.auth.models import User
from src.modules.discovery.models import LeadContact
from src.modules.outreach.models import Conversation, Message
from src.modules.selection.engine import normalize
from src.modules.selection.models import SelectionFile, SelectionRequest
from src.modules.selection.review import ROLES, ReviewUpdate, apply_review

from . import outbound, workspace_tools
from .data_scope import scope as result_scope
from .planner import Intent

INITIAL_SUGGESTIONS = [
    {"label": "Talepleri analiz et", "text": "Talepleri analiz et"},
    {"label": "Teklif taleplerine bak", "text": "Teklif taleplerini göster"},
    {"label": "WhatsApp limiti", "text": "WhatsApp limiti"},
]


def suggestions(context: Any, intent: Any = None) -> Any:
    """Server-known read-only capabilities, never model-proposed unvalidated actions."""
    if intent and (
        intent.tool == "workspace"
        or (
            intent.tool == "workflow"
            and intent.workflow_kind in {"create_agent", "configure", "test", "publish"}
        )
    ):
        return [
            {"label": "Şirket bilgileri", "text": "Şirket bilgileri"},
            {"label": "Müşteri testi", "text": "Müşteri testi"},
            {"label": "Sürümler", "text": "Sürümler"},
        ]
    if context.get("outbound_batch_id") and intent and intent.tool in outbound.TOOLS:
        return [
            {"label": "Gönderim durumu", "text": "Gönderim durumu"},
            {"label": "WhatsApp limiti", "text": "WhatsApp limiti"},
        ]
    if context.get("pending"):
        return []  # Selecting a candidate resolves an explicit pending operation, not a suggestion.
    if not context.get("selected_request_id"):
        if intent is None:
            return [dict(item) for item in INITIAL_SUGGESTIONS]
        options = [
            ("analytics", "Talepleri analiz et", "Talepleri analiz et"),
            ("search", "Bekleyenleri göster", "Bekleyen talepleri göster"),
            ("search", "Bugün gelenler", "Bugün gelen talepleri göster"),
            ("quotes", "Teklif taleplerine bak", "Teklif taleplerini göster"),
        ]
        return [
            {"label": label, "text": text} for tool, label, text in options if tool != intent.tool
        ][:3]
    options = [
        ("missing", "Eksikleri kontrol et", "Bu talepte ne eksik?"),
        ("files", "Dosyalara bak", "Dosyaları göster"),
        ("conversation", "Konuşmayı oku", "Konuşmayı göster"),
        ("delivery", "Mesaj ulaştı mı?", "Son mesaj ulaştı mı?"),
    ]
    return [
        {"label": label, "text": text}
        for tool, label, text in options
        if intent is None or tool != intent.tool
    ][:3]


OPERATIONS = {"status": "update_status", "assign": "assign", "note": "add_note"}


def answer_value(value: Any) -> Any:
    if isinstance(value, dict):
        return str(
            value.get("label") or value.get("value") or value.get("status") or "Belirtilmedi"
        )
    return str(value) if value is not None else "Belirtilmedi"


def request_card(row: Any) -> Any:
    answers = (row.confirmed_snapshot or {}).get("answers", row.answers)
    title = answer_value(answers.get("contact_name"))
    fields = (row.confirmed_snapshot or {}).get("fields") or row.definition.get("steps", [])
    summary = "\n".join(
        f"{f['label']}: {answer_value(answers[f['id']])}" for f in fields if f["id"] in answers
    )
    return {
        "type": "request",
        "request_id": str(row.id),
        "title": title,
        "created_at": row.created_at.isoformat(),
        "status": row.status,
        "summary": summary[:8000],
    }


def normalized(column: Any) -> Any:
    return func.lower(func.translate(column, "İIıŞşĞğÜüÖöÇç", "iiissgguuoocc"))


def search_text(value: str) -> str:
    """Normalize phone formatting without guessing a country code or changing names."""
    value = value.strip()
    compact = re.sub(r"[\s().-]", "", value)
    if re.fullmatch(r"(?:\+|00)?\d{7,15}", compact):
        return "+" + compact[2:] if compact.startswith("00") else compact
    return value


def request_query(user: Any, intent: Any) -> Any:
    stmt = select(SelectionRequest).where(SelectionRequest.tenant_id == user.tenant_id)
    if intent.target:
        query = normalize(search_text(intent.target)).strip()
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = (
            stmt.join(Conversation, Conversation.id == SelectionRequest.conversation_id)
            .join(LeadContact, LeadContact.id == Conversation.contact_id)
            .where(
                Conversation.tenant_id == user.tenant_id,
                LeadContact.tenant_id == user.tenant_id,
                or_(
                    cast(SelectionRequest.id, String).ilike(pattern, escape="\\"),
                    normalized(cast(SelectionRequest.answers["contact_name"], String)).like(
                        pattern, escape="\\"
                    ),
                    LeadContact.normalized_value.ilike(pattern, escape="\\"),
                ),
            )
        )
    if intent.tool in {"search", "quotes"} and intent.status:
        stmt = stmt.where(SelectionRequest.status == intent.status)
    if intent.tool == "quotes":
        stmt = stmt.where(
            func.jsonb_typeof(SelectionRequest.confirmed_snapshot) == "object",
            func.jsonb_typeof(SelectionRequest.confirmed_snapshot["answers"]) == "object",
        )
    if intent.today:
        tz = ZoneInfo(user.timezone or "Europe/Istanbul")
        now = datetime.now(tz)
        start = datetime.combine(now.date(), time.min, tzinfo=tz)
        stmt = stmt.where(
            SelectionRequest.created_at >= start,
            SelectionRequest.created_at < start + timedelta(days=1),
        )
    return stmt


async def find_requests(db: Any, user: Any, intent: Any) -> Any:
    stmt = request_query(user, intent)
    return list(
        (
            await db.scalars(
                stmt.order_by(SelectionRequest.created_at.desc(), SelectionRequest.id).limit(51)
            )
        ).all()
    )


async def execute(db: Any, claims: Any, user: Any, session: Any, intent: Any) -> Any:
    context = dict(session.context)
    cards: list[dict[str, Any]] = []
    action = None
    audit = {
        "tool": intent.tool,
        "arguments": intent.model_dump(mode="json"),
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
    }

    def result(reply: Any) -> Any:
        session.context = {
            **(
                {"workspace": session.context["workspace"]}
                if "workspace" in session.context
                else {}
            ),
            **context,
        }
        return reply, cards, action, audit

    if intent.tool == "workspace":
        reply, cards, action = await workspace_tools.execute(db, claims, user, session, intent)
        return reply, cards, action, audit
    if user.role.value == "viewer":
        raise HTTPException(403, "İzleyici hesabı yalnız şirket bilgilerini okuyabilir.")
    if intent.tool in outbound.TOOLS:
        reply, outbound_cards = await outbound.execute(db, user, session, intent)
        return reply, outbound_cards, None, audit

    if intent.tool == "clarify":
        return result("")  # The common language layer asks a contextual question.
    if intent.tool == "analytics":
        # Aggregate every matching row in SQL; the 50-card display limit must not truncate totals.
        filters = [SelectionRequest.tenant_id == user.tenant_id]
        if intent.target:
            matches = request_query(user, intent.model_copy(update={"tool": "search"})).with_only_columns(SelectionRequest.id)
            filters.append(SelectionRequest.id.in_(matches))
        if intent.status:
            filters.append(SelectionRequest.status == intent.status)
        if intent.today:
            tz = ZoneInfo(user.timezone or "Europe/Istanbul")
            start = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
            filters.extend(
                [
                    SelectionRequest.created_at >= start,
                    SelectionRequest.created_at < start + timedelta(days=1),
                ]
            )
        counts = dict(
            (
                await db.execute(
                    select(SelectionRequest.status, func.count())
                    .where(*filters)
                    .group_by(SelectionRequest.status)
                )
            ).all()
        )
        unassigned = await db.scalar(
            select(func.count())
            .select_from(SelectionRequest)
            .where(
                *filters,
                SelectionRequest.status.in_(["waiting_review", "in_review"]),
                SelectionRequest.assigned_to.is_(None),
            )
        )
        context = {}
        total = sum(counts.values())
        audit["result_scope"] = result_scope(user, "requests", query=intent.target or "",
                                      today=intent.today, total=total, status=intent.status)
        audit["result_data"] = {"counts": counts, "total": total, "unassigned": unassigned}
        if not total:
            return result(
                "Bu kapsamda henüz kayıtlı talep yok. Müşterilerden gelen talepler burada analiz edilecek."
            )
        labels = {
            "draft": "Taslak",
            "waiting_review": "İnceleme bekliyor",
            "in_review": "İnceleniyor",
            "completed": "Tamamlandı",
            "cancelled": "İptal",
        }
        lines = [f"{'Bugün oluşturulan' if intent.today else 'Kayıtlı'} {total} talebin durumu:"]
        lines.extend(
            f"• {labels.get(status or "", status)}: {count}"
            for status, count in sorted(counts.items())
        )
        lines.append(f"Sorumlu atanmamış açık talep: {unassigned}.")
        if unassigned:
            lines.append("Önce bu talepleri inceleyip sorumlu belirleyebilirsiniz.")
        lines.append("Tamamlandı durumu fiyat veya teknik uygunluk onayı anlamına gelmez.")
        return result("\n".join(lines))
    if intent.tool in {"search", "quotes"}:
        rows = await find_requests(db, user, intent)
        cards.extend(request_card(r) for r in rows[:50])
        context = {"candidates": {str(r.id): r.revision for r in rows[:50]}}
        if len(rows) == 1:
            context.update(selected_request_id=str(rows[0].id), revision=rows[0].revision)
        if intent.tool == "quotes":
            scope = "Fiyatlandırılmış teklif kaydı bulunmuyor; müşteri tarafından onaylanmış teknik teklif taleplerini gösteriyorum."
            return result(
                scope
                + (
                    f" {'İlk 50' if len(rows) > 50 else len(rows)} talep var."
                    if rows
                    else " Henüz onaylanmış talep yok."
                )
            )
        return result(
            "Talep bulunamadı."
            if not rows
            else f"{'İlk 50' if len(rows) > 50 else len(rows)} talep gösteriliyor. "
            + (
                "Talep numarasıyla seçim yapabilirsiniz."
                if len(rows) != 1
                else "Bu talep üzerinden devam edebilirsiniz."
            )
        )

    # Resolve explicit references solely against current tenant data. Never trust an LLM ID.
    row = None
    expected_revision = context.get("revision")
    if intent.target:
        rows = await find_requests(db, user, intent)
        if len(rows) != 1:
            cards.extend(request_card(r) for r in rows[:50])
            context = {"candidates": {str(r.id): r.revision for r in rows[:50]}}
            if intent.tool in OPERATIONS:
                context["pending"] = intent.model_dump(mode="json")
            return result(
                "Bu hedefle talep bulunamadı; talep numarasını belirtin."
                if not rows
                else "Birden fazla talep eşleşti. İşlem yapmadım; talep numarasını belirtin."
            )
        row = rows[0]
        expected_revision = context.get("candidates", {}).get(str(row.id), row.revision)
        if context.get("selected_request_id") == str(row.id):
            expected_revision = context.get("revision", row.revision)
        # A disambiguation reply names a candidate; restore only server-stored pending intent.
        if (
            intent.tool == "summary"
            and context.get("pending")
            and str(row.id) in context.get("candidates", {})
        ):
            intent = Intent.model_validate({**context["pending"], "target": str(row.id)})
            audit["tool"] = intent.tool
            audit["arguments"] = intent.model_dump(mode="json")
    elif context.get("selected_request_id"):
        row = await db.scalar(
            select(SelectionRequest).where(
                SelectionRequest.id == UUID(context["selected_request_id"]),
                SelectionRequest.tenant_id == user.tenant_id,
            )
        )
    if row is None:
        return result("Önce bir talep seçin: müşteri telefonu, adı veya talep numarasını belirtin.")
    audit["request_id"] = str(row.id)
    context = {"selected_request_id": str(row.id), "revision": row.revision}
    if intent.tool in OPERATIONS:
        operation = OPERATIONS[intent.tool]
        action = {
            "status": "rejected",
            "operation": operation,
            "request_id": str(row.id),
            "message": "",
        }
        payload = {"revision": expected_revision if expected_revision is not None else row.revision}
        if intent.tool == "status":
            payload["status"] = intent.status
        elif intent.tool == "note":
            payload["note"] = intent.note
        else:
            users = list(
                (
                    await db.scalars(
                        select(User).where(
                            User.tenant_id == user.tenant_id,
                            User.is_active.is_(True),
                            User.role.in_(ROLES),
                        )
                    )
                ).all()
            )
            query = normalize(intent.assignee)
            matches = [
                u
                for u in users
                if query
                in {
                    normalize(u.full_name or ""),
                    normalize(u.email),
                    normalize((u.full_name or "").split(" ")[0]),
                }
            ]
            if len(matches) != 1:
                action["message"] = (
                    "Sorumlu tekil bulunamadı. Aktif ekip üyesinin tam adını veya e-postasını yazarak atama komutunu tekrarlayın."
                )
                return result(action["message"])
            payload["assigned_to"] = matches[0].id
        audit["expected_revision"] = payload["revision"]
        try:
            # Nested transaction makes failures atomic even if a future rule mutates then rejects.
            async with db.begin_nested():
                row = await apply_review(db, claims, row.id, ReviewUpdate(**payload))
            if row.revision == payload["revision"]:
                action.update(
                    status="no_change",
                    message="Talep zaten istenen durumda; değişiklik yapılmadı.",
                )
            else:
                action.update(status="applied", message="Talep güncellendi.")
            context["revision"] = row.revision
            audit["result_revision"] = row.revision
        except HTTPException as exc:
            action.update(
                status="conflict" if exc.status_code == 409 else "rejected",
                message="Talep değişmiş veya bu geçişe uygun değil. Talebi yeniden görüntüleyip işlemi tekrar isteyin."
                if exc.status_code == 409
                else "İşlem yetki veya argüman doğrulamasından geçemedi.",
            )
            audit["rejection_code"] = exc.status_code
        cards.append(request_card(row))
        return result(action["message"])

    cards.append(request_card(row))
    if intent.tool == "summary":
        return result(
            "Talep özeti ve mevcut durumu kartta. "
            + (
                "İç notlar:\n"
                + "\n".join(
                    str(x.get("text", "")) for x in row.internal_notes[-20:] if x.get("text")
                )
                if row.internal_notes
                else ""
            )
        )
    if intent.tool == "missing":
        from .workflow_requests import missing_fields

        missing = missing_fields(row)
        return result(
            "Eksik veya teknik ekipçe netleştirilecek bilgiler: " + ", ".join(missing)
            if missing
            else "Kayıtlı zorunlu alanlarda eksik görünmüyor; teknik uygunluk ayrıca incelenmelidir."
        )
    if intent.tool == "files":
        files = list(
            (
                await db.scalars(
                    select(SelectionFile).where(
                        SelectionFile.tenant_id == user.tenant_id,
                        SelectionFile.request_id == row.id,
                    )
                )
            ).all()
        )
        cards.extend(
            {
                "type": "file",
                "request_id": str(row.id),
                "file_id": str(f.id),
                "filename": f.filename,
            }
            for f in files
        )
        return result(f"Talepte {len(files)} dosya var.")
    if intent.tool == "conversation":
        messages = list(
            (
                await db.scalars(
                    select(Message)
                    .where(
                        Message.tenant_id == user.tenant_id,
                        Message.conversation_id == row.conversation_id,
                    )
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(30)
                )
            ).all()
        )
        summary = "\n".join(
            f"{m.created_at.isoformat()} · {'Müşteri' if m.direction == 'inbound' else 'Ekip/bot'}: {(m.body or '[Dosya/medya]')[:2000]}"
            for m in reversed(messages)
        )
        cards.append(
            {
                "type": "conversation",
                "conversation_id": str(row.conversation_id),
                "title": "Son 30 mesaj (müşteri metni veri olarak gösterilir)",
                "summary": summary,
            }
        )
        return result("Müşteri konuşmasının son mesajları kartta.")
    if intent.tool == "delivery":
        from .workflow_inbox import delivery_summary

        reply = await delivery_summary(db, user, row.conversation_id)
        cards.append({"type": "notice", "title": "Mesaj teslimatı", "summary": reply})
        return result(reply)
    return result("Bu araç desteklenmiyor.")
