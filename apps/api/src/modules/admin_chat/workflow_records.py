# ruff: noqa: RUF001
"""Shared tenant-scoped search and record navigation, with bounded result pages."""

from types import SimpleNamespace
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from src.core.rbac import Role, role_at_least
from src.modules.agents.models import Agent, AgentVersion
from src.modules.agents.service import AgentService
from src.modules.auth.models import Tenant, User
from src.modules.discovery.models import Lead, LeadContact

from . import workflow_inbox
from .data_scope import scope
from .workflow_requests import STATUS as REQUEST_STATUS
from .workflow_schema import WorkflowField

CATEGORIES = {
    "request_details": "Talep ayrıntıları",
    "requests": "Teknik talepler",
    "quotes": "Teklif talepleri",
    "inbox": "Gelen kutusu",
    "agents": "Asistanlar",
    "contacts": "Kişiler",
    "team": "Ekip ve yetkiler",
    "companies": "Şirketler",
    "knowledge": "Şirket bilgileri",
    "versions": "Sürümler",
}
CONTROLS = [
    WorkflowField(
        key="category", label="Kayıt türü", control="select", required=True, options=CATEGORIES
    ),
    WorkflowField(key="request", label="Talep"),
    WorkflowField(key="q", label="Kayıtlarda ara"),
    WorkflowField(
        key="status",
        label="Talep durumu",
        control="select",
        options={
            "": "Tümü",
            "waiting_review": "İnceleme bekliyor",
            "in_review": "İnceleniyor",
            "completed": "Tamamlandı",
            "cancelled": "İptal edildi",
        },
    ),
    WorkflowField(
        key="today",
        label="Tarih kapsamı",
        control="select",
        options={"false": "Tüm tarihler", "true": "Bugün"},
    ),
    WorkflowField(key="agent", label="Asistan", control="select"),
    WorkflowField(key="page", label="Sayfa"),
    WorkflowField(key="record", label="Kayıt"),
]


def authorize(user: Any, category: str) -> None:
    required = {
        "companies": Role.SUPER_ADMIN,
        "team": Role.TENANT_OWNER,
        "contacts": Role.SALES_AGENT,
    }.get(category, Role.VIEWER)
    if category not in CATEGORIES or not role_at_least(user.role, required):
        raise HTTPException(403, "Bu kayıtları görüntülemek için yetkiniz yok.")


def controls(row: Any) -> list[WorkflowField]:
    keys = set() if row.fields.get("category") == "request_details" else {"q"}
    if row.fields.get("category") in {"requests", "quotes"}:
        keys.update({"status", "today"})
    if row.fields.get("category") == "inbox":
        keys.add("today")
    if row.fields.get("category") in {"knowledge", "versions"}:
        keys.add("agent")
    return [
        c.model_copy(update={"options": row.state.get("agent_choices", {})})
        if c.key == "agent"
        else c
        for c in CONTROLS
        if c.key in keys
    ]


def operation(key: str, label: str) -> dict[str, str]:
    return {"operation": key, "label": label}


async def initialize(db: Any, user: Any, session: Any, row: Any) -> None:
    row.fields = {"category": "agents", "page": "1", **row.fields}
    agents = await AgentService(db).list_agents(user.tenant_id)
    row.state = {"agent_choices": {a.slug: a.name for a in agents}}
    selected = next(
        (a for a in agents if str(a.id) == session.context.get("workspace", {}).get("agent_id")),
        None,
    )
    if not selected and len(agents) == 1:
        selected = agents[0]
    if selected and not row.fields.get("agent"):
        row.fields = {**row.fields, "agent": selected.slug}
    await refresh(db, user, row)


async def refresh(db: Any, user: Any, row: Any) -> None:
    category = row.fields.get("category", "agents")
    authorize(user, category)
    from .service import search_text

    query = search_text(row.fields.get("q", ""))
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    try:
        page = max(1, int(row.fields.get("page") or "1"))
    except ValueError as exc:
        raise HTTPException(422, "Geçerli bir sayfa numarası seçin.") from exc
    if page > 10000:
        raise HTTPException(422, "Aramayı daraltın.")
    actions = []
    records: list[dict[str, Any]] = []
    total = 0
    if category == "request_details":
        from . import workflow_requests

        total, records = await workflow_requests.details(
            db, user, row.fields.get("request", ""), page
        )
    elif category in {"requests", "quotes"}:
        from src.modules.selection.models import SelectionRequest

        from .service import request_card, request_query

        row.fields = {"status": "", "today": "false", **row.fields}

        if row.fields.get("today", "false") not in {"true", "false"}:
            raise HTTPException(422, "Geçerli bir tarih kapsamı seçin.")
        if row.fields.get("status", "") not in {
            "",
            "waiting_review",
            "in_review",
            "completed",
            "cancelled",
        }:
            raise HTTPException(422, "Geçerli bir talep durumu seçin.")
        intent = SimpleNamespace(
            tool="quotes" if category == "quotes" else "search",
            target=query or None,
            status=row.fields.get("status") or None,
            today=row.fields.get("today") == "true",
        )
        stmt = request_query(user, intent)
        total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = await db.scalars(
            stmt.order_by(SelectionRequest.created_at.desc(), SelectionRequest.id)
            .offset((page - 1) * 20)
            .limit(20)
        )
        for request in rows:
            card = request_card(request)
            records.append(
                {
                    "id": str(request.id),
                    "title": card["title"] or "Teknik talep",
                    "subtitle": str(request.id),
                    "details": {
                        "Durum": REQUEST_STATUS.get(request.status, request.status),
                        "Oluşturuldu": card["created_at"],
                        "Müşteri bilgileri": card["summary"],
                        "Kapsam": "Müşterinin teknik talebi; fiyat veya uygunluk onayı değildir.",
                    },
                    "actions": [
                        {"operation": "request_details", "label": "Talep ayrıntıları"},
                        {"operation": "conversation", "label": "Konuşmayı aç"},
                        *(
                            [
                                {"operation": key, "label": label}
                                for key, label in {
                                    "status": "Durumu değiştir",
                                    "assign": "Sorumlu ata",
                                    "note": "İç not ekle",
                                }.items()
                            ]
                            if request.status != "draft"
                            and role_at_least(user.role, Role.SALES_AGENT)
                            else []
                        ),
                    ],
                }
            )
    elif category == "inbox":
        total, records = await workflow_inbox.listing(db, user, pattern, page, today=row.fields.get("today") == "true")
    elif category in {"knowledge", "versions"}:
        agents = await AgentService(db).list_agents(user.tenant_id)
        row.state = {**row.state, "agent_choices": {a.slug: a.name for a in agents}}
        agent = next((a for a in agents if row.fields.get("agent") in {a.slug, a.name}), None)
        if not agent:
            row.state = {
                **row.state,
                "records": [],
                "output": {"summary": "Önce bir asistan seçin."},
                "total": 0,
                "page": 1,
                "has_more": False,
            }
            return
        row.fields = {**row.fields, "agent": agent.slug}
        if category == "knowledge":
            service = AgentService(db)
            version = await service.get_draft(user.tenant_id, agent.id) or await service.get_live(
                user.tenant_id, agent.id
            )
            facts = (version.company_config or {}).get("facts", []) if version else []
            all_records: list[dict[str, Any]] = [
                {
                    "id": str(f["id"]),
                    "title": str(f["id"]),
                    "subtitle": "Onaylı bilgi" if f.get("customer_visible") else "İç bilgi",
                    "details": {
                        "Bilgi": " · ".join(f.get("customer_text", {}).values())
                        or str(f.get("value", "")),
                        "Kaynak": str(f.get("source", "Belirtilmedi")),
                    },
                    "actions": [],
                }
                for f in facts
            ]
            config = version.company_config if version else {}
            organization = config.get("organization") or {}
            if organization:
                all_records.insert(
                    0,
                    {
                        "id": "organization",
                        "title": " · ".join(organization.get("display_names", {}).values()),
                        "subtitle": "Şirket bilgileri",
                        "details": {
                            "Ürün ve hizmetler": " · ".join(
                                " / ".join(o.get("display_names", {}).values())
                                for o in config.get("offerings", [])
                            )
                            or "Belirtilmedi",
                            "Sürüm": f"v{version.version}" if version else "Yok",
                        },
                        "actions": [],
                    },
                )
            matching_records = [r for r in all_records if query.casefold() in str(r).casefold()]
            total = len(matching_records)
            records = matching_records[(page - 1) * 20 : page * 20]
            if role_at_least(user.role, Role.SALES_MANAGER):
                actions = [
                    operation("configure", "Bilgi ekle / değiştir"),
                    operation("test", "Müşteri testi"),
                    operation("publish", "Taslağı yayınla"),
                ]
        else:
            versions = await AgentService(db).list_versions(user.tenant_id, agent.id)
            matches = [
                v
                for v in versions
                if query.casefold() in f"v{v.version} {v.status.value}".casefold()
            ]
            total = len(matches)
            labels = {"draft": "Taslak", "live": "Yayında", "archived": "Arşiv"}
            records = [
                {
                    "id": str(v.id),
                    "title": f"v{v.version}",
                    "subtitle": labels.get(v.status.value, v.status.value),
                    "details": {"Revizyon": str(v.revision)},
                    "actions": [
                        operation("test", "Bu sürümü test et"),
                        *(
                            [operation("rollback", "Bu sürüme dön")]
                            if v.status.value in {"live", "archived"}
                            else []
                        ),
                    ]
                    if role_at_least(user.role, Role.SALES_MANAGER)
                    else [],
                }
                for v in matches[(page - 1) * 20 : page * 20]
            ]
    else:
        model: Any = {"agents": Agent, "contacts": Lead, "team": User, "companies": Tenant}[
            category
        ]
        stmt = select(model)
        if category != "companies":
            stmt = stmt.where(model.tenant_id == user.tenant_id)
        if query:
            columns: Any = {
                "agents": [Agent.name, Agent.slug],
                "contacts": [Lead.person_name, Lead.company_name],
                "team": [User.full_name, User.email],
                "companies": [Tenant.name, Tenant.slug],
            }[category]
            filters = [c.ilike(pattern, escape="\\") for c in columns]
            if category == "contacts":
                filters.append(
                    Lead.id.in_(
                        select(LeadContact.lead_id).where(
                            LeadContact.tenant_id == user.tenant_id,
                            LeadContact.normalized_value.ilike(pattern, escape="\\"),
                        )
                    )
                )
            stmt = stmt.where(or_(*filters))
        total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = list(
            (
                await db.scalars(
                    stmt.order_by(model.created_at.desc(), model.id)
                    .offset((page - 1) * 20)
                    .limit(20)
                )
            ).all()
        )
        for item in rows:
            item_actions = []
            if category == "agents":
                title = item.name
                subtitle = item.slug
                details = {"Kod": item.slug}
                item_actions = [
                    operation("knowledge", "Bilgileri aç"),
                    operation("versions", "Sürümleri aç"),
                ]
                if role_at_least(user.role, Role.SALES_MANAGER):
                    item_actions += [
                        operation("configure", "Bilgi ekle"),
                        operation("test", "Müşteri testi"),
                        operation("publish", "Taslağı yayınla"),
                    ]
            elif category == "contacts":
                contacts = list(
                    (
                        await db.scalars(
                            select(LeadContact).where(
                                LeadContact.tenant_id == user.tenant_id,
                                LeadContact.lead_id == item.id,
                            )
                        )
                    ).all()
                )
                title = item.person_name or item.company_name or "İsimsiz kayıt"
                subtitle = item.company_name if item.person_name else "Şirket kaydı"
                details = {
                    "Şirket": item.company_name or "Belirtilmedi",
                    **{f"{c.type.value} {i+1}": c.normalized_value for i, c in enumerate(contacts)},
                }
            elif category == "team":
                if item.role.value != "super_admin":
                    item_actions = [operation("member", "Erişimi değiştir")]
                title = item.full_name or item.email
                subtitle = item.email
                details = {
                    "Yetki": {
                        "tenant_owner": "Şirket sahibi",
                        "sales_manager": "Satış yöneticisi",
                        "sales_agent": "Satış temsilcisi",
                        "viewer": "İzleyici",
                        "super_admin": "Platform yöneticisi",
                    }.get(item.role.value, item.role.value),
                    "Durum": "Etkin" if item.is_active else "Pasif",
                }
            else:
                item_actions = [operation("owner_invite", "Sahip daveti hazırla")]
                title = item.name
                subtitle = item.slug
                details = {"Şirket kodu": item.slug, "Durum": item.status.value}
            records.append(
                {
                    "id": str(item.id),
                    "title": title,
                    "subtitle": subtitle,
                    "details": details,
                    "actions": item_actions,
                }
            )
        creation = {
            "agents": ("create_agent", "Asistan oluştur", Role.SALES_MANAGER),
            "contacts": ("contact", "Kişi ekle", Role.SALES_AGENT),
            "team": ("invite", "Ekip üyesi davet et", Role.TENANT_OWNER),
            "companies": ("create_company", "Şirket oluştur", Role.SUPER_ADMIN),
        }[category]
        if role_at_least(user.role, creation[2]):
            actions = [operation(creation[0], creation[1])]
    row.state = {
        **row.state,
        "records": records,
        "record_actions": actions,
        "total": total,
        "page": page,
        "has_more": page * 20 < total,
        "scope": scope(user, category, query=query, today=row.fields.get("today") == "true",
                       total=total, page=page, has_more=page * 20 < total,
                       status=row.fields.get("status") or None),
        "output": {
            "summary": f"{CATEGORIES[category]} · {total} kayıt"
            if total
            else "Bu kapsamda kayıt yok. Aramayı değiştirebilir veya yeni kayıt oluşturabilirsiniz."
        },
    }
    row.status, row.step = "awaiting_input", "details"


async def launch(
    db: Any, user: Any, row: Any, values: dict[str, str]
) -> tuple[str, dict[str, str]]:
    if set(values) - {"operation", "record"}:
        raise HTTPException(422, "Bilinmeyen kayıt işlemi alanı.")
    category = row.fields["category"]
    authorize(user, category)
    op = values.get("operation", "")
    record_id = values.get("record", "")
    if not record_id:
        allowed = {a["operation"] for a in row.state.get("record_actions", [])}
        if op not in allowed:
            raise HTTPException(422, "Bu işlem kayıt üzerinden kullanılamaz.")
        return op, (
            {"agent": row.fields["agent"]}
            if row.fields.get("agent") and op in {"configure", "test", "publish"}
            else {}
        )
    try:
        UUID(record_id)
    except ValueError as exc:
        raise HTTPException(422, "Geçerli bir kayıt seçin.") from exc
    if category in {"requests", "quotes", "request_details"} and op in {
        "request_details",
        "conversation",
        "status",
        "assign",
        "note",
    }:
        from src.modules.selection.models import SelectionRequest

        request = await db.scalar(
            select(SelectionRequest).where(
                SelectionRequest.tenant_id == user.tenant_id, SelectionRequest.id == UUID(record_id)
            )
        )
        if request is None:
            raise HTTPException(404, "Talep bulunamadı.")
        if op == "request_details":
            return "records", {"category": "request_details", "request": str(request.id)}
        if op != "conversation":
            if not role_at_least(user.role, Role.SALES_AGENT):
                raise HTTPException(403, "Bu işlem için yetkiniz yok.")
            return "request_update", {"request": str(request.id), "operation": op}
        return "conversation", {"conversation": str(request.conversation_id)}
    if category == "inbox" and op == "conversation":
        conv = await workflow_inbox.own(db, user, record_id)
        return "conversation", {"conversation": str(conv.id)}
    if category == "agents":
        agent = await AgentService(db).get_agent(user.tenant_id, UUID(record_id))
        if op in {"knowledge", "versions"}:
            return "records", {"category": op, "agent": agent.slug}
        if op in {"configure", "test", "publish"}:
            return op, {"agent": agent.slug}
    if category == "companies" and op == "owner_invite":
        if await db.get(Tenant, UUID(record_id)) is None:
            raise HTTPException(404, "Şirket bulunamadı.")
        return "owner_invite", {"tenant": record_id}
    if category == "team" and op == "member":
        member = await db.scalar(
            select(User).where(User.id == UUID(record_id), User.tenant_id == user.tenant_id)
        )
        if member is None:
            raise HTTPException(404, "Ekip üyesi bulunamadı.")
        return "member", {"member": member.email}
    if category == "versions" and op in {"test", "rollback"}:
        version = await db.scalar(
            select(AgentVersion).where(
                AgentVersion.id == UUID(record_id), AgentVersion.tenant_id == user.tenant_id
            )
        )
        if version is None:
            raise HTTPException(404, "Sürüm bulunamadı.")
        agent = await AgentService(db).get_agent(user.tenant_id, version.agent_id)
        if agent.slug != row.fields.get("agent"):
            raise HTTPException(404, "Bu asistanın sürümü değil.")
        return op, {"agent": agent.slug, "version": str(version.id)}
    raise HTTPException(422, "Bu kayıt için işlem desteklenmiyor.")
