# ruff: noqa: RUF001
"""Progressive configuration, import, preview test and publish adapters.

Only canonical services mutate agent configuration; each workflow owns its own
proposal and immutable revision snapshot instead of sharing a chat preview slot.
"""

import csv
import io
import json
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from src.modules.agents import workspace
from src.modules.agents.builder_service import AgentBuilderService
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.service import AgentService
from src.modules.agents.workspace_models import ConfigProposal

from .workflow_schema import WorkflowField
from .workspace_tools import TurnTransaction

CSV_LABELS = {
    "id": "Bilgi kimliği sütunu",
    "subject_id": "Şirket / ürün kimliği sütunu",
    "category": "Bilgi türü sütunu",
    "customer_text": "Müşteriye gösterilecek metin sütunu",
    "source": "Bilgi kaynağı sütunu",
    "search_terms": "Arama sözcükleri sütunu",
}


KINDS = {"configure", "publish", "test", "rollback"}
CONTROLS = {
    "rollback": [
        WorkflowField(key="agent", label="Asistan", control="select", required=True),
        WorkflowField(key="version", label="Geri alınacak sürüm", control="select", required=True),
    ],
    "configure": [
        WorkflowField(key="agent", label="Asistan", control="select", required=True),
        WorkflowField(
            key="format",
            label="Bilgi kaynağı",
            control="select",
            required=True,
            options={"text": "Mesajla verilen bilgi", "json": "JSON", "csv": "CSV"},
        ),
        WorkflowField(
            key="content",
            label="Eklenecek veya değiştirilecek bilgi",
            control="textarea",
            required=True,
        ),
        WorkflowField(key="columns", label="CSV sütun eşlemesi"),
        *[
            WorkflowField(key="column_" + key, label=label, control="select")
            for key, label in CSV_LABELS.items()
        ],
    ],
    "publish": [WorkflowField(key="agent", label="Asistan", control="select", required=True)],
    "test": [
        WorkflowField(key="agent", label="Asistan", control="select", required=True),
        WorkflowField(key="version", label="Test sürümü", control="select", required=True),
        WorkflowField(
            key="content", label="Denenecek müşteri mesajı", control="textarea", required=True
        ),
    ],
}


def claims(user: Any) -> dict[str, str]:
    return {"tid": str(user.tenant_id), "sub": str(user.id), "role": user.role.value}


def csv_headers(row: Any) -> list[str]:
    if row.kind != "configure" or row.fields.get("format") != "csv":
        return []
    try:
        headers = next(csv.reader(io.StringIO(row.fields.get("content", "").lstrip("\ufeff"))), [])
        return list(
            dict.fromkeys(header for header in headers[:100] if header and len(header) <= 255)
        )
    except csv.Error:
        return []


def controls(row: Any) -> list[WorkflowField]:
    result = []
    headers = csv_headers(row)
    for control in CONTROLS[row.kind]:
        if control.key == "columns":
            continue  # Older persisted mappings remain accepted by the server.
        if control.key.startswith("column_"):
            if headers:
                result.append(
                    control.model_copy(
                        update={
                            "options": {header: header for header in headers},
                            "required": control.key != "column_search_terms",
                        }
                    )
                )
            continue
        options = (row.state or {}).get("choices", {}).get(control.key)
        result.append(
            control.model_copy(update={"options": options}) if options is not None else control
        )
    return result


async def initialize(db: Any, user: Any, session: Any, row: Any) -> None:
    service = AgentService(db)
    agents = await service.list_agents(user.tenant_id)
    choices = {a.slug: a.name for a in agents}
    selected_id = session.context.get("workspace", {}).get("agent_id")
    selected = next((a for a in agents if str(a.id) == selected_id), None)
    if selected is None and len(agents) == 1:
        selected = agents[0]
    defaults = {"format": "text"} if row.kind == "configure" else {}
    if selected:
        defaults["agent"] = selected.slug
    row.fields = {**defaults, **row.fields}
    row.state = {"choices": {"agent": choices}}
    await refresh_versions(db, user, row)


async def agent(db: Any, user: Any, row: Any) -> Any:
    agents = await AgentService(db).list_agents(user.tenant_id)
    selected = next((a for a in agents if row.fields.get("agent") in {a.slug, a.name}), None)
    if selected is None:
        raise HTTPException(422, "Bu şirkette asistan bulunamadı. Listeden seçin.")
    return selected


async def refresh_versions(db: Any, user: Any, row: Any) -> None:
    headers = csv_headers(row)
    if headers:
        try:
            previous_mapping = json.loads(row.fields.get("columns") or "{}")
        except ValueError:
            previous_mapping = {}
        if not isinstance(previous_mapping, dict):
            previous_mapping = {}
        mapping_fields = {}
        for key in CSV_LABELS:
            current = row.fields.get("column_" + key, previous_mapping.get(key, key))
            mapping_fields["column_" + key] = current if current in headers else ""
        row.fields = {**row.fields, **mapping_fields}
    if not row.fields.get("agent"):
        return
    selected = await agent(db, user, row)
    row.fields = {**row.fields, "agent": selected.slug}
    if row.kind not in {"test", "rollback"}:
        return
    versions = await AgentService(db).list_versions(user.tenant_id, selected.id)
    if row.kind == "rollback":
        versions = [v for v in versions if v.status.value in {"live", "archived"}]
    statuses = {"draft": "Taslak", "live": "Yayında", "archived": "Arşiv"}
    choices = {
        str(
            v.id
        ): f"v{v.version} · {statuses.get(v.status.value, v.status.value)} · revizyon {v.revision}"
        for v in versions
    }
    row.state = {**row.state, "choices": {**row.state.get("choices", {}), "version": choices}}
    if row.fields.get("version") and row.fields["version"] not in choices:
        raise HTTPException(422, "Seçilen sürüm bu asistana ait değil. Listeden seçin.")
    if row.kind == "test" and not row.fields.get("version") and versions:
        selected_version = next((v for v in versions if v.status.value == "draft"), versions[0])
        row.fields = {**row.fields, "version": str(selected_version.id)}


def changes(before: Any, after: Any, path: str = "") -> list[dict[str, str]]:
    labels = {
        "organization": "Şirket",
        "agent": "Asistan",
        "offerings": "Ürün ve hizmetler",
        "facts": "Onaylı bilgiler",
        "relationships": "İlişkiler",
        "display_names": "Ad",
        "customer_text": "Müşteriye gösterilen metin",
        "source": "Kaynak",
    }
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            item
            for key in sorted(set(before) | set(after))
            if key != "lifecycle"
            for item in changes(
                before.get(key), after.get(key), f"{path} / {labels.get(key,key)}".strip(" /")
            )
        ]
    if (
        isinstance(before, list)
        and isinstance(after, list)
        and all(isinstance(v, dict) and "id" in v for v in [*before, *after])
    ):
        return changes({v["id"]: v for v in before}, {v["id"]: v for v in after}, path)

    def display(value: Any) -> str:
        if value is None:
            return "Yok"
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    return [{"label": path, "before": display(before), "after": display(after)}]


async def invalidate(db: Any, user: Any, row: Any) -> None:
    if row.state.get("proposal_id"):
        proposal = await db.scalar(
            select(ConfigProposal)
            .where(
                ConfigProposal.id == UUID(row.state["proposal_id"]),
                ConfigProposal.tenant_id == user.tenant_id,
            )
            .with_for_update()
        )
        if proposal and proposal.status == "pending":
            transaction: Any = TurnTransaction(db)
            await workspace.decide(
                proposal.agent_id, proposal.id, "reject", transaction, claims(user)
            )
    row.state = {"choices": row.state.get("choices", {})}


async def prepare(db: Any, user: Any, row: Any) -> None:
    transaction: Any = TurnTransaction(db)
    service = AgentService(transaction)
    selected = await agent(db, user, row)
    await service.lock_agent(user.tenant_id, selected.id)
    draft = await service.get_draft(user.tenant_id, selected.id)
    state = {**row.state, "agent_id": str(selected.id), "agent_name": selected.name}
    if row.kind == "test":
        version = await service.get_version(
            user.tenant_id, selected.id, UUID(row.fields["version"])
        )
        CompanyAgentConfig.model_validate({**version.company_config, "lifecycle": "approved"})
        row.state = {
            **state,
            "version_id": str(version.id),
            "revision": version.revision,
            "output": {
                "summary": f"{selected.name} · v{version.version} · revizyon {version.revision}. Test WhatsApp mesajı göndermez."
            },
        }
        return
    if row.kind == "rollback":
        version = await service.get_version(
            user.tenant_id, selected.id, UUID(row.fields["version"])
        )
        CompanyAgentConfig.model_validate({**version.company_config, "lifecycle": "approved"})
        live = await service.get_live(user.tenant_id, selected.id)
        row.state = {
            **state,
            "version_id": str(version.id),
            "revision": version.revision,
            "live_id": str(live.id) if live else None,
            "live_revision": live.revision if live else None,
            "changes": changes(live.company_config if live else {}, version.company_config),
            "output": {
                "summary": f"{selected.name}: v{version.version} içeriği yeni bir LIVE sürüm olarak yayınlanacak. Eski sürümler korunacak."
            },
        }
        return
    if draft is None:
        raise HTTPException(409, "Bu asistanın taslağı yok. Önce düzenlenebilir taslak oluşturun.")
    if row.kind == "publish":
        CompanyAgentConfig.model_validate({**draft.company_config, "lifecycle": "approved"})
        live = await service.get_live(user.tenant_id, selected.id)
        row.state = {
            **state,
            "version_id": str(draft.id),
            "revision": draft.revision,
            "live_id": str(live.id) if live else None,
            "changes": changes(live.company_config if live else {}, draft.company_config),
            "output": {
                "summary": f"{selected.name} · v{draft.version} · revizyon {draft.revision}. Yayından sonra yeni müşteri mesajları bu sürümü kullanacak."
            },
        }
        return
    if row.fields["format"] == "text":
        builder = await AgentBuilderService(transaction).start_session(
            user.tenant_id, selected.id, actor_id=user.id
        )
        prepared = await workspace.builder_turn(
            transaction, user.tenant_id, selected.id, builder.id, row.fields["content"]
        )
        proposal = prepared["proposal"]
        if not proposal:
            row.state = {**state, "output": {"summary": prepared["reply"]}}
            row.status, row.step = "awaiting_input", "details"
            return
    else:
        columns = json.loads(row.fields.get("columns") or "{}")
        if not isinstance(columns, dict):
            raise ValueError("CSV column mapping must be an object")
        columns.update(
            {
                key: row.fields["column_" + key]
                for key in CSV_LABELS
                if "column_" + key in row.fields
            }
        )
        prepared = await workspace.preview_import(
            selected.id,
            workspace.ImportIn(
                format=row.fields["format"],
                content=row.fields["content"],
                expected_revision=draft.revision,
                columns=columns,
            ),
            transaction,
            claims(user),
        )
        if prepared["errors"]:
            row.state = {
                **state,
                "errors": {
                    "content": "\n".join(
                        f"Satır {e['row']}: {e['error']}" for e in prepared["errors"]
                    )
                },
            }
            row.status, row.step = "awaiting_input", "details"
            return
        proposal = prepared["proposal"]
    row.state = {
        **state,
        "proposal_id": proposal["id"],
        "version_id": proposal["version_id"],
        "revision": proposal["base_revision"],
        "changes": changes(draft.company_config, proposal["company_config"]),
        "output": {
            "summary": "Değişiklikleri inceleyin. Kabul edildiğinde yalnız taslağa kaydedilecek."
        },
    }


async def complete(db: Any, user: Any, session: Any, row: Any) -> None:
    transaction: Any = TurnTransaction(db)
    service = AgentService(transaction)
    aid = UUID(row.state["agent_id"])
    await service.lock_agent(user.tenant_id, aid)
    version = await service.get_version(user.tenant_id, aid, UUID(row.state["version_id"]))
    if version.revision != row.state["revision"]:
        raise HTTPException(409, "Sürüm değişti. Bilgileri düzenleyip yeni önizleme hazırlayın.")
    if row.kind == "configure":
        await workspace.decide(
            aid, UUID(row.state["proposal_id"]), "accept", transaction, claims(user)
        )
        row.result = {
            "outcome": "draft_saved",
            "agent_id": str(aid),
            "message": "Bilgiler taslağa kaydedildi. Canlıya yayınlanmadı.",
        }
    elif row.kind == "rollback":
        live = await service.get_live(user.tenant_id, aid)
        if (str(live.id) if live else None) != row.state["live_id"] or (
            live.revision if live else None
        ) != row.state["live_revision"]:
            raise HTTPException(409, "Canlı sürüm değişti. Yeni geri alma önizlemesi hazırlayın.")
        restored = await service.rollback_to(user.tenant_id, aid, version.id, actor_id=user.id)
        row.result = {
            "outcome": "rolled_back",
            "version_id": str(restored.id),
            "message": f"{row.state['agent_name']} v{version.version} içeriği yeni v{restored.version} sürümü olarak yayınlandı. Eski sürümler korundu.",
        }
    elif row.kind == "publish":
        live = await service.get_live(user.tenant_id, aid)
        if (str(live.id) if live else None) != row.state["live_id"]:
            raise HTTPException(409, "Canlı sürüm değişti. Yeni yayın önizlemesi hazırlayın.")
        await service.promote_to_live(user.tenant_id, aid, version.id, actor_id=user.id)
        row.result = {
            "outcome": "published",
            "version_id": str(version.id),
            "message": f"{row.state['agent_name']} v{version.version} yayınlandı. Yeni müşteri mesajları bu sürümü kullanacak.",
        }
    else:
        context = dict(session.context.get("workspace", {}))
        version_key = f"{version.id}:{version.revision}"
        if context.get("test_version") == version_key and context.get("test_id"):
            test_id = UUID(context["test_id"])
        else:
            test = await workspace.start_test(
                aid, workspace.TestIn(version_id=version.id), transaction, claims(user)
            )
            test_id = test["id"]
        result = await workspace.test_turn(
            aid, test_id, workspace.TurnIn(text=row.fields["content"]), transaction, claims(user)
        )
        session.context = {
            **session.context,
            "workspace": {
                **context,
                "agent_id": str(aid),
                "test_id": str(test_id),
                "test_version": version_key,
            },
        }
        row.state = {**row.state, "output": result}
        row.result = {
            "outcome": "test_result",
            "test_id": str(test_id),
            "message": "Müşteri testi sonucu hazır. WhatsApp mesajı gönderilmedi.",
        }
        if result.get("used_fallback"):
            row.result["message"] = (
                "Model yanıtı alınamadı veya doğrulanamadı. Girilen mesaj ve test sonucu saklandı."
            )
            row.status, row.step = "failed", "details"
