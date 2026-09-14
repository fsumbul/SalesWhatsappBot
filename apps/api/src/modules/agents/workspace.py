# ruff: noqa: RUF001
"""One configuration pipeline for form, conversation, import and preview."""

import csv
import io
import json
import time
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from src.core.config import get_settings
from src.core.deps import DBSessionDep
from src.core.errors import BadGatewayError, ConflictError, NotFoundError, ServiceUnavailableError
from src.core.rbac import RequireManager
from src.integrations.llm import (
    LLMCompletionError,
    LLMMessage,
    LLMNotConfiguredError,
    get_llm_client,
)
from src.modules.compliance.models import AuditLog

from .company_config import CompanyAgentConfig
from .company_runtime import CompanyAgentRuntime, CustomerReplyAction
from .models import BuilderSession
from .schemas import AgentVersionPatchIn
from .service import AgentService
from .workspace_models import AgentTestSession, ConfigProposal

router = APIRouter(prefix="/agents", tags=["workspace"])
CSV_FIELDS = ["id", "subject_id", "category", "customer_text", "source", "search_terms"]


class ProposalIn(BaseModel):
    company_config: CompanyAgentConfig
    expected_revision: int


class ImportIn(BaseModel):
    format: Literal["json", "csv"]
    content: str = Field(max_length=500_000)
    expected_revision: int
    columns: dict[str, str] = Field(default_factory=dict)


class TestIn(BaseModel):
    version_id: UUID


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def ids(claims: dict[str, Any]) -> tuple[UUID, UUID]:
    return UUID(claims["tid"]), UUID(claims["sub"])


def proposal_out(p: ConfigProposal) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "version_id": str(p.version_id),
        "base_revision": p.base_revision,
        "status": p.status,
        "source": p.source,
        "company_config": p.company_config,
    }


async def stage(
    db: Any, tid: UUID, aid: UUID, config: dict[str, Any], revision: int, source: str
) -> ConfigProposal:
    service = AgentService(db)
    await service.lock_agent(tid, aid)
    draft = await service.get_draft(tid, aid)
    if draft is None or draft.revision != revision:
        raise ConflictError("Draft changed; reload before creating a proposal")
    config = {**config, "lifecycle": "draft"}
    try:
        validated = CompanyAgentConfig.model_validate(config)
    except ValidationError as exc:
        raise ConflictError("Invalid company configuration: " + str(exc)) from exc
    proposal = ConfigProposal(
        tenant_id=tid,
        agent_id=aid,
        version_id=draft.id,
        base_revision=revision,
        source=source,
        company_config=validated.model_dump(mode="json"),
    )
    db.add(proposal)
    await db.flush()
    return proposal


@router.get("/imports/template.csv")
async def csv_template(_: RequireManager) -> Response:
    return Response(
        ",".join(CSV_FIELDS) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="company-facts.csv"'},
    )


@router.get("/{agent_id}/proposals")
async def proposals(
    agent_id: UUID, db: DBSessionDep, claims: RequireManager
) -> list[dict[str, Any]]:
    tid, _ = ids(claims)
    await AgentService(db).get_agent(tid, agent_id)
    rows = (
        await db.execute(
            select(ConfigProposal)
            .where(
                ConfigProposal.tenant_id == tid,
                ConfigProposal.agent_id == agent_id,
                ConfigProposal.status == "pending",
            )
            .order_by(ConfigProposal.created_at.desc())
        )
    ).scalars()
    return [proposal_out(p) for p in rows]


@router.post("/{agent_id}/proposals")
async def propose(
    agent_id: UUID, payload: ProposalIn, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, _ = ids(claims)
    p = await stage(
        db,
        tid,
        agent_id,
        payload.company_config.model_dump(mode="json"),
        payload.expected_revision,
        "form",
    )
    await db.commit()
    return proposal_out(p)


@router.post("/{agent_id}/proposals/{proposal_id}/{decision}")
async def decide(
    agent_id: UUID,
    proposal_id: UUID,
    decision: Literal["accept", "reject"],
    db: DBSessionDep,
    claims: RequireManager,
) -> dict[str, Any]:
    tid, uid = ids(claims)
    service = AgentService(db)
    await service.lock_agent(tid, agent_id)
    p = (
        await db.execute(
            select(ConfigProposal)
            .where(
                ConfigProposal.id == proposal_id,
                ConfigProposal.tenant_id == tid,
                ConfigProposal.agent_id == agent_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if p is None:
        raise NotFoundError("Proposal")
    if p.status != "pending":
        raise ConflictError("Proposal already resolved")
    if decision == "accept":
        draft = await service.get_draft(tid, agent_id)
        if draft is None or draft.id != p.version_id or draft.revision != p.base_revision:
            raise ConflictError("Draft changed; create a fresh proposal")
    p.status = "accepted" if decision == "accept" else "rejected"
    db.add(
        AuditLog(
            tenant_id=tid,
            actor_id=uid,
            action="config_proposal_" + decision,
            entity="agent_config_proposal",
            entity_id=str(p.id),
        )
    )
    if decision == "accept":
        await service.update_draft(
            tid,
            agent_id,
            AgentVersionPatchIn(
                company_config=CompanyAgentConfig.model_validate(p.company_config),
                expected_revision=p.base_revision,
            ),
            actor_id=uid,
        )
    else:
        await db.commit()
    return proposal_out(p)


def import_config(
    payload: ImportIn, current: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if payload.format == "json":
        try:
            config = json.loads(payload.content)
            config["lifecycle"] = "draft"
            return CompanyAgentConfig.model_validate(config).model_dump(mode="json"), []
        except (ValueError, TypeError) as exc:
            return {}, [{"row": 0, "error": str(exc)}]
    config = deepcopy(current)
    reader = csv.DictReader(io.StringIO(payload.content.lstrip("\ufeff")))
    if reader.fieldnames and len(reader.fieldnames) != len(set(reader.fieldnames)):
        return {}, [{"row": 1, "error": "CSV sütun adları benzersiz olmalı."}]
    mapping = {key: payload.columns.get(key, key) for key in CSV_FIELDS}
    if not reader.fieldnames or any(mapping[k] not in reader.fieldnames for k in CSV_FIELDS[:5]):
        return {}, [
            {"row": 1, "error": "Bilgi kimliği, şirket/ürün kimliği, bilgi türü, müşteri metni ve kaynak sütunlarını seçin."}
        ]
    locale = (config.get("agent") or {}).get("default_locale", "tr")
    facts = {f["id"]: f for f in config.get("facts", [])}
    entities = {item["id"] for key in ("offerings", "parties") for item in config.get(key, [])}
    if config.get("organization"):
        entities.add(config["organization"]["id"])
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    from .company_config import Fact, FactCategory

    for row_number, row in enumerate(reader, 2):
        if row_number > 1001:
            errors.append({"row": row_number, "error": "Maximum 1000 rows"})
            break
        try:

            def get(key: str, current_row: dict[str, Any] = row) -> str:
                return str(current_row.get(mapping[key]) or "").strip()

            if not get("customer_text") or not get("source"):
                raise ValueError("customer_text and source must not be empty")
            if get("subject_id") not in entities:
                raise ValueError("Unknown subject_id; create the company or offering first")
            fact_id = get("id")
            if fact_id in seen:
                raise ValueError("Duplicate fact id in file")
            seen.add(fact_id)
            f = Fact(
                id=fact_id,
                subject_id=get("subject_id"),
                category=FactCategory(get("category")),
                value=get("customer_text"),
                customer_text={locale: get("customer_text")},
                customer_visible=True,
                source=get("source"),
                search_terms=[v.strip() for v in get("search_terms").split("|") if v.strip()],
            )
            facts[f.id] = f.model_dump(mode="json")
        except (ValueError, TypeError) as exc:
            errors.append({"row": row_number, "error": str(exc)})
    if errors:
        return {}, errors
    config["facts"] = list(facts.values())
    config["lifecycle"] = "draft"
    try:
        return CompanyAgentConfig.model_validate(config).model_dump(mode="json"), []
    except ValidationError as exc:
        return {}, [{"row": 0, "error": str(exc)}]


@router.post("/{agent_id}/imports/preview")
async def preview_import(
    agent_id: UUID, payload: ImportIn, db: DBSessionDep, claims: RequireManager
) -> dict[str, Any]:
    tid, _ = ids(claims)
    draft = await AgentService(db).get_draft(tid, agent_id)
    if draft is None:
        raise NotFoundError("Draft")
    config, errors = import_config(payload, draft.company_config)
    if errors:
        return {"errors": errors, "proposal": None}
    p = await stage(db, tid, agent_id, config, payload.expected_revision, payload.format)
    await db.commit()
    return {"errors": [], "proposal": proposal_out(p)}


@router.get("/{agent_id}/builder/sessions")
async def builder_sessions(agent_id: UUID, db: DBSessionDep, claims: RequireManager) -> Any:
    tid, _ = ids(claims)
    await AgentService(db).get_agent(tid, agent_id)
    rows = (
        (
            await db.execute(
                select(BuilderSession)
                .where(BuilderSession.tenant_id == tid, BuilderSession.agent_id == agent_id)
                .order_by(BuilderSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    from .schemas import BuilderSessionOut

    return [BuilderSessionOut.model_validate(row) for row in rows]


def builder_context(config: dict[str, Any], text: str) -> dict[str, Any]:
    from .company_runtime import _search_tokens

    tokens = _search_tokens(text)

    def relevant(item: dict[str, Any]) -> int:
        return len(tokens & _search_tokens(json.dumps(item, ensure_ascii=False)))

    projection = {key: config.get(key) for key in ("organization", "agent")}
    # No private fact values are needed to explain editable approved claims.
    projection["offerings"] = [
        {k: item[k] for k in ("id", "kind", "display_names") if k in item}
        for item in config.get("offerings", [])[:40]
    ]
    projection["facts"] = [
        {
            k: item[k]
            for k in ("id", "subject_id", "category", "customer_text", "source")
            if k in item
        }
        for item in sorted(config.get("facts", []), key=relevant, reverse=True)[:10]
    ]
    return projection


async def builder_turn(db: Any, tid: UUID, aid: UUID, sid: UUID, text: str) -> dict[str, Any]:
    session = (
        await db.execute(
            select(BuilderSession)
            .where(
                BuilderSession.id == sid,
                BuilderSession.tenant_id == tid,
                BuilderSession.agent_id == aid,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise NotFoundError("Builder session")
    draft = await AgentService(db).get_draft(tid, aid)
    if draft is None or draft.id != session.draft_version_id:
        raise ConflictError("Start a new builder session for the current draft")
    revision = draft.revision
    config = deepcopy(draft.company_config)
    system = """You help a company owner configure a generic company agent. Reply in Turkish.
Return JSON only: {"reply":"short explanation or clarification question", "patch":{}}.
Never invent company claims. Propose only explicitly provided information; ask when missing.
patch may contain organization, agent, offerings, facts, relationships.
A business capability or service statement must become an entry in facts, not only an offering or reply.
Capture EVERY explicit business claim in the current message. When both a company name and a service
are provided, include both organization and facts in the patch. Do not silently drop the service.
Copy the owner's factual wording into value and customer_text; do not paraphrase or invent details.
The reply is not stored as company knowledge. Ask for missing details only after capturing facts already provided.
organization: {"id":"company","display_names":{"tr":"company name"}}.
agent: {"purposes":["information"],"supported_locales":["tr"],"default_locale":"tr","require_fact_ids_for_claims":true,"unknown_fact_action":"handoff"}.
offerings entries: {"id":"lowercase_id","kind":"physical_product" or "professional_service","display_names":{"tr":"name"},"provider_id":"company"}.
facts entries: {"id":"lowercase_id","subject_id":"company or offering id","category":"capability" or "specification" or "other","value":"provided fact","customer_visible":true,"customer_text":{"tr":"provided fact"},"source":"owner","search_terms":[]}.
Arrays are upserted by id. Do not claim saved or published: changes require separate acceptance.
Current configuration excerpt (not complete; omitted records still exist):\n""" + json.dumps(
        builder_context(config, text), ensure_ascii=False
    )
    history = [LLMMessage(role=m["role"], content=m["content"]) for m in session.messages[-6:]]
    try:
        raw = await get_llm_client().complete(
            [*history, LLMMessage(role="user", content=text)], system=system, max_tokens=1500
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("reply"), str)
            or not result["reply"].strip()
        ):
            raise ValueError("Missing reply")
        patch = result.get("patch", {})
        if not isinstance(patch, dict) or set(patch) - {
            "organization",
            "agent",
            "offerings",
            "facts",
            "relationships",
        }:
            raise ValueError("Invalid patch fields")
        for key, value in patch.items():
            if key in ("facts", "offerings"):
                merged = {item["id"]: item for item in config.get(key, [])}
                for item in value:
                    merged[item["id"]] = {**merged.get(item["id"], {}), **item}
                config[key] = list(merged.values())
            elif key in ("organization", "agent"):
                config[key] = {**(config.get(key) or {}), **value}
            else:
                config[key] = value
        p = await stage(db, tid, aid, config, revision, "chat") if patch else None
    except (LLMNotConfiguredError, LLMCompletionError) as exc:
        raise ServiceUnavailableError("Yerel modele ulaşılamadı; taslak değiştirilmedi") from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise BadGatewayError("Model geçerli bir yapılandırma önerisi üretmedi") from exc
    session.messages = [
        *session.messages,
        {"role": "user", "content": text},
        {"role": "assistant", "content": result["reply"]},
    ]
    await db.commit()
    return {"reply": result["reply"], "proposal": proposal_out(p) if p else None}


@router.post("/{agent_id}/test-sessions")
async def start_test(
    agent_id: UUID, payload: TestIn, db: DBSessionDep, claims: RequireManager
) -> Any:
    tid, _ = ids(claims)
    version = await AgentService(db).get_version(tid, agent_id, payload.version_id)
    data = {**version.company_config, "lifecycle": "approved"}
    try:
        CompanyAgentConfig.model_validate(data)
    except ValidationError as exc:
        raise ConflictError(
            "Complete the company configuration before testing: " + str(exc)
        ) from exc
    session = AgentTestSession(
        tenant_id=tid,
        agent_id=agent_id,
        version_id=version.id,
        version_revision=version.revision,
        messages=[],
    )
    db.add(session)
    await db.commit()
    return {"id": session.id, "version_id": session.version_id, "messages": []}


@router.get("/{agent_id}/test-sessions")
async def list_tests(agent_id: UUID, db: DBSessionDep, claims: RequireManager) -> Any:
    tid, _ = ids(claims)
    await AgentService(db).get_agent(tid, agent_id)
    rows = (
        await db.execute(
            select(AgentTestSession)
            .where(AgentTestSession.tenant_id == tid, AgentTestSession.agent_id == agent_id)
            .order_by(AgentTestSession.created_at.desc())
            .limit(30)
        )
    ).scalars()
    return [{"id": s.id, "version_id": s.version_id, "messages": s.messages} for s in rows]


@router.post("/{agent_id}/test-sessions/{session_id}/messages")
async def test_turn(
    agent_id: UUID, session_id: UUID, payload: TurnIn, db: DBSessionDep, claims: RequireManager
) -> Any:
    tid, _ = ids(claims)
    session = (
        await db.execute(
            select(AgentTestSession)
            .where(
                AgentTestSession.id == session_id,
                AgentTestSession.tenant_id == tid,
                AgentTestSession.agent_id == agent_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise NotFoundError("Test session")
    version = await AgentService(db).get_version(tid, agent_id, session.version_id)
    if version.revision != session.version_revision:
        raise ConflictError("Configuration changed; start a new test session")
    config = CompanyAgentConfig.model_validate({**version.company_config, "lifecycle": "approved"})
    history = [
        LLMMessage(role=m["role"], content=m["content"][:600]) for m in session.messages[-6:]
    ]
    context = tuple(session.messages[-1].get("fact_ids", [])) if session.messages else ()
    started = time.monotonic()
    from src.modules.selection.service import preview_natural

    saved = session.messages[-1].get("selection_state") if session.messages else None
    turn, selection_state, _ = await preview_natural(
        config, saved, payload.text, get_llm_client(), history,
    )
    if turn is None:
        turn = await CompanyAgentRuntime(config, get_llm_client()).reply(
            payload.text, history=history, context_fact_ids=context
        )
        if turn.intake_requested and config.selection_flow is not None:
            turn, selection_state, resume_prompt = await preview_natural(
                config, saved, payload.text, get_llm_client(), history, start_requested=True,
            )
    settings = get_settings()
    result = jsonable_encoder(
        {
            **asdict(turn),
            "version_id": version.id,
            "version": version.version,
            "selection_state": selection_state,
            "handoff_requested": turn.action == CustomerReplyAction.HANDOFF,
            "model": settings.llm_model,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    )
    session.messages = [
        *session.messages,
        {"role": "user", "content": payload.text},
        {"role": "assistant", "content": turn.reply, **result},
    ]
    await db.commit()
    return result
