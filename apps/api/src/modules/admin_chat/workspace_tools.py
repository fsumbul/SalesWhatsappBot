# ruff: noqa: RUF001
"""Bounded chat tools reuse canonical services in the chat turn's transaction.

This adapter is ONLY for database/configuration tools. Never pass it to outbound
or manual messaging: those must commit their sending state before network I/O.
"""

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, text

from src.core.db import set_tenant_context
from src.core.rbac import Role, role_at_least
from src.modules.agents import workspace
from src.modules.agents.builder_service import AgentBuilderService
from src.modules.agents.schemas import AgentIn
from src.modules.agents.service import AgentService
from src.modules.auth import platform
from src.modules.auth.schemas import InviteIn
from src.modules.auth.service import AuthService


class TurnTransaction:
    """Defer service commits to the outer atomic, idempotent admin-chat turn."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def __getattr__(self, name: str) -> Any:
        return getattr(self.db, name)

    async def commit(self) -> None:
        await self.db.flush()


MIN_ROLE = {
    "team": Role.TENANT_OWNER,
    "invite": Role.TENANT_OWNER,
    "platform": Role.SUPER_ADMIN,
    "create_company": Role.SUPER_ADMIN,
    "configure": Role.SALES_MANAGER,
    "test": Role.SALES_MANAGER,
    "publish": Role.SALES_MANAGER,
    "accept_config": Role.SALES_MANAGER,
    "create_agent": Role.SALES_MANAGER,
}
TITLES = {
    "agents": "Asistanlar",
    "knowledge": "Şirket bilgileri",
    "configure": "Şirket bilgisi ekle",
    "test": "Müşteri testi",
    "versions": "Sürümler",
    "publish": "Sürümü yayınla",
    "team": "Ekip ve yetkiler",
    "invite": "Kullanıcı davet et",
    "platform": "Şirketler",
    "create_company": "Şirket oluştur",
    "create_agent": "Asistan oluştur",
    "inbox": "Gelen kutusu",
    "accept_config": "Bilgi değişikliğini kabul et",
}


def authorize(user: Any, operation: str) -> None:
    if not role_at_least(user.role, MIN_ROLE.get(operation, Role.VIEWER)):
        raise HTTPException(403, "Bu işlem için yetkiniz yok.")


def panel(operation: str, **values: Any) -> dict[str, Any]:
    return {"type": "workspace", "operation": operation, "title": TITLES[operation], **values}


def preview(context: dict[str, Any], operation: str, summary: str, **values: Any) -> dict[str, Any]:
    pending = {"id": str(uuid4()), "operation": operation, **values}
    context["pending_operation"] = pending
    return {
        "type": "workspace_preview",
        "operation_id": pending["id"],
        "title": TITLES[operation],
        "summary": summary,
        **({"company_config": values["company_config"]} if "company_config" in values else {}),
    }


async def execute(db: Any, claims: Any, user: Any, session: Any, intent: Any) -> Any:
    context = dict(session.context.get("workspace", {}))
    transaction: Any = TurnTransaction(db)
    service = AgentService(transaction)
    tid, uid = user.tenant_id, user.id
    op = intent.operation

    def done(reply: str, cards: list[dict[str, Any]], status: str = "read") -> Any:
        session.context = {**session.context, "workspace": context}
        return reply, cards, {"status": status, "operation": op}

    if op in {"confirm", "cancel"}:
        pending = context.get("pending_operation")
        if not pending or (intent.operation_id and intent.operation_id != pending["id"]):
            raise HTTPException(409, "Bu önizleme artık güncel değil. İşlemi yeniden hazırlayın.")
        kind = pending["operation"]
        authorize(user, kind)
        cards = []
        if op == "cancel":
            if kind == "accept_config":
                await workspace.decide(
                    UUID(pending["agent_id"]),
                    UUID(pending["proposal_id"]),
                    "reject",
                    transaction,
                    claims,
                )
            reply = "İşlem iptal edildi."
        elif kind == "accept_config":
            await workspace.decide(
                UUID(pending["agent_id"]),
                UUID(pending["proposal_id"]),
                "accept",
                transaction,
                claims,
            )
            reply = (
                "Bilgiler taslağa kaydedildi. Yayına almak için ‘Taslağı yayınla’ yazabilirsiniz."
            )
        elif kind == "publish":
            aid = UUID(pending["agent_id"])
            await service.lock_agent(tid, aid)
            version = await service.get_version(tid, aid, UUID(pending["version_id"]))
            live = await service.get_live(tid, aid)
            if (
                version.revision != pending["revision"]
                or str(version.status) not in {"draft", "testing"}
                or (str(live.id) if live else None) != pending["live_id"]
            ):
                raise HTTPException(409, "Sürüm değişti. Yeni bir yayın önizlemesi hazırlayın.")
            await service.promote_to_live(tid, aid, version.id, actor_id=uid)
            reply = f"v{version.version} yayınlandı. Yeni müşteri mesajları bu sürümü kullanacak."
        elif kind == "invite":
            inv = await AuthService(transaction).create_invitation(
                tid, uid, InviteIn(**pending["values"])
            )
            cards = [
                {
                    "type": "invitation",
                    "title": "Davet hazır",
                    "token": inv.token,
                    "company": pending["company"],
                    "summary": inv.email,
                }
            ]
            reply = "Davet bağlantısı hazır. Bağlantıyı kopyalayıp paylaşabilirsiniz."
        elif kind == "create_company":
            try:
                result = await platform.provision(
                    platform.ProvisionIn(**pending["values"]), transaction, claims
                )
            finally:
                # Provisioning is an explicit cross-company boundary. Restore BOTH RLS
                # contexts before writing the private chat response in the original tenant.
                await set_tenant_context(db, tid)
                await db.execute(
                    text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(uid)}
                )
            cards = [
                {
                    "type": "invitation",
                    "title": result["tenant"].name,
                    "token": result["invitation_token"],
                    "company": result["tenant"].slug,
                    "summary": "Şirket sahibinin daveti · WhatsApp bağlantısı kurulmadı",
                }
            ]
            reply = "Şirket oluşturuldu. Sahibinin davet bağlantısı hazır."
        elif kind == "create_agent":
            agent = await service.create_agent(tid, AgentIn(**pending["values"]), actor_id=uid)
            context["agent_id"] = str(agent.id)
            context.pop("builder_id", None)
            context.pop("test_id", None)
            reply = f"{agent.name} oluşturuldu. Şirket bilgilerini bu sohbete yazabilirsiniz."
            cards = []  # A historical confirmation must not introduce a new full workspace panel.
        else:
            raise HTTPException(409, "Desteklenmeyen önizleme.")
        from .models import AdminChatTurn

        prior_turns = await db.scalars(
            select(AdminChatTurn).where(
                AdminChatTurn.session_id == session.id,
                AdminChatTurn.user_id == uid,
                AdminChatTurn.tenant_id == tid,
            )
        )
        for previous in prior_turns:
            if any(
                c.get("operation_id") == pending["id"] for c in previous.response.get("cards", [])
            ):
                previous.response = {
                    **previous.response,
                    "cards": [
                        {**c, "status": "cancelled" if op == "cancel" else "applied"}
                        if c.get("operation_id") == pending["id"]
                        else c
                        for c in previous.response["cards"]
                    ],
                }
        context.pop("pending_operation", None)
        return done(reply, cards, "cancelled" if op == "cancel" else "applied")

    authorize(user, op)
    if op in {"invite", "create_company", "create_agent"}:
        values = {
            key: getattr(intent, key)
            for key in ("name", "slug", "email", "role")
            if getattr(intent, key)
        }
        required = {
            "invite": {"email", "role"},
            "create_company": {"name", "slug", "email"},
            "create_agent": {"name", "slug"},
        }[op]
        if not required.issubset(values):
            return done("Eksik alanları bu kartta tamamlayabilirsiniz.", [panel(op, values=values)])
        if op == "create_company":
            values["owner_email"] = values.pop("email")
            values = platform.ProvisionIn(**values).model_dump(mode="json")
        elif op == "invite":
            values = InviteIn(**values).model_dump(mode="json")
        else:
            values = AgentIn(**values).model_dump(mode="json", exclude_none=True)
        from src.modules.auth.models import Tenant

        tenant = await db.get(Tenant, tid)
        summary = "\n".join(f"{key}: {value}" for key, value in values.items())
        card = preview(context, op, summary, values=values, company=tenant.slug)
        return done(
            "İşlem hazır. Aşağıdaki bilgileri kontrol edip uygulayabilirsiniz.", [card], "preview"
        )

    if op in {"team", "platform", "inbox"}:
        return done("Buradan devam edebilirsiniz; sohbet açık kalacak.", [panel(op)])

    agents = await service.list_agents(tid)
    if op == "select_agent":
        selected = next((a for a in agents if str(a.id) == intent.operation_id), None)
        if not selected:
            raise HTTPException(404, "Asistan bulunamadı.")
    elif intent.target:
        from src.modules.selection.engine import normalize

        matches = [
            a for a in agents if normalize(intent.target) in {normalize(a.name), normalize(a.slug)}
        ]
        selected = matches[0] if len(matches) == 1 else None
    else:
        selected = next((a for a in agents if str(a.id) == context.get("agent_id")), None)
        if selected is None and len(agents) == 1:
            selected = agents[0]
    if not selected:
        return done(
            "Devam etmek için bir asistan seçin."
            if agents
            else "Önce bir asistan oluşturabilirsiniz.",
            [
                {
                    "type": "agent_choices",
                    "title": "Asistan seçin",
                    "choices": [{"id": str(a.id), "label": a.name} for a in agents],
                },
                panel("create_agent"),
            ],
        )
    aid = selected.id
    if context.get("agent_id") != str(aid):
        context.pop("builder_id", None)
        context.pop("test_id", None)
    context["agent_id"] = str(aid)
    if op == "select_agent":
        return done(f"{selected.name} seçildi. Yapmak istediğiniz işlemi yazabilirsiniz.", [])
    if op in {"agents", "knowledge", "versions"} or (
        op in {"configure", "test"} and not intent.instruction
    ):
        return done(
            f"{selected.name} için {TITLES[op].lower()} hazır.", [panel(op, agent_id=str(aid))]
        )
    draft = await service.get_draft(tid, aid)
    if op == "configure":
        builder = None
        if context.get("builder_id"):
            builder = await AgentBuilderService(transaction).get_builder_session(
                tid, aid, UUID(context["builder_id"])
            )
        if builder is None or draft is None or builder.draft_version_id != draft.id:
            builder = await AgentBuilderService(transaction).start_session(tid, aid, actor_id=uid)
        context["builder_id"] = str(builder.id)
        result = await workspace.builder_turn(transaction, tid, aid, builder.id, intent.instruction)
        p = result["proposal"]
        if p:
            card = preview(
                context,
                "accept_config",
                f"{selected.name} · Taslak revizyonu {p['base_revision']}\nKabul ettiğinizde taslağa kaydedilecek.",
                agent_id=str(aid),
                proposal_id=p["id"],
                company_config=p["company_config"],
            )
            return done(result["reply"], [card], "preview")
        return done(result["reply"], [])
    if op == "publish":
        if not draft:
            return done(
                "Yayınlanacak taslak yok. Sürümleri bu karttan yönetebilirsiniz.",
                [panel("versions", agent_id=str(aid))],
            )
        from src.modules.agents.company_config import CompanyAgentConfig

        CompanyAgentConfig.model_validate({**draft.company_config, "lifecycle": "approved"})
        live = await service.get_live(tid, aid)
        card = preview(
            context,
            "publish",
            f"{selected.name} · v{draft.version} · revizyon {draft.revision}\nYayın sonrasında yeni müşteri mesajlarında kullanılacak.",
            agent_id=str(aid),
            version_id=str(draft.id),
            revision=draft.revision,
            live_id=str(live.id) if live else None,
            company_config=draft.company_config,
        )
        return done(
            "Yayınlanacak sürüm aşağıda. Uygulayarak yayına alabilirsiniz.", [card], "preview"
        )
    if op == "test":
        test_version = draft or await service.get_live(tid, aid)
        if test_version is None:
            raise HTTPException(409, "Test edilecek sürüm yok.")
        key = f"{test_version.id}:{test_version.revision}"
        if not context.get("test_id") or context.get("test_version") != key:
            test = await workspace.start_test(
                aid, workspace.TestIn(version_id=test_version.id), transaction, claims
            )
            context.update(test_id=str(test["id"]), test_version=key)
        result = await workspace.test_turn(
            aid,
            UUID(context["test_id"]),
            workspace.TurnIn(text=intent.instruction),
            transaction,
            claims,
        )
        return done(
            "Müşteri testi tamamlandı. WhatsApp mesajı gönderilmedi.",
            [{"type": "agent_test", "title": selected.name, "result": result}],
        )
    raise HTTPException(422, "Desteklenmeyen işlem.")
