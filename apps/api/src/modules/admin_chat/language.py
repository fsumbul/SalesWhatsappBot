"""Private admin history and evidence memory. Never grants write authority."""

from typing import Any, cast

from sqlalchemy import select

from src.integrations.llm import LLMMessage
from src.modules.conversation_language import public_data

from .models import AdminChatTurn


def _safe_workflow_evidence(view: dict[str, Any]) -> dict[str, Any]:
    """Remove private import artifacts before they become model evidence."""

    output = view.get("output")
    imported = output.get("campaign_import") if isinstance(output, dict) else None
    if view.get("kind") == "outreach" and isinstance(imported, dict):
        return {
            "kind": "outreach",
            "status": view.get("status"),
            "step": view.get("step"),
            "import_status": imported.get("status"),
        }
    return {key: view[key] for key in ("kind", "status", "step", "output", "errors") if key in view}


def _strip_import_artifacts(value: Any) -> Any:
    """Also scrub older persisted language memory created before this boundary.

    Import headers and filenames are user-controlled strings.  They must not
    become instructions merely because a prior card was stored in turn audit.
    """

    if isinstance(value, list):
        return [_strip_import_artifacts(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("kind") == "outreach":
        return _safe_workflow_evidence(value)
    imported = value.get("campaign_import")
    if isinstance(imported, dict):
        return {"campaign_import": {"status": imported.get("status")}}
    return {key: _strip_import_artifacts(item) for key, item in value.items()}


async def recall(db: Any, user: Any, session: Any) -> tuple[list[LLMMessage], dict[str, Any]]:
    rows = list((await db.scalars(select(AdminChatTurn).where(
        AdminChatTurn.session_id == session.id, AdminChatTurn.user_id == user.id,
        AdminChatTurn.tenant_id == user.tenant_id,
    ).order_by(AdminChatTurn.sequence.desc()).limit(4))).all())
    history = []
    for row in reversed(rows):
        history.extend([LLMMessage(role="user", content=row.text),
                        LLMMessage(role="assistant", content=row.response["reply"])])
    # Existing turn audits may predate the import boundary. Scrub memory on
    # read as well as write so an old persisted filename/header can never be
    # reintroduced into a later planner or response prompt.
    memory = _strip_import_artifacts(rows[0].audit.get("language_memory", {})) if rows else {}
    return history, cast(dict[str, Any], memory)


def observe(audit: dict[str, Any], views: list[dict[str, Any]], reply: str,
            action: Any, previous: dict[str, Any]) -> dict[str, Any]:
    evidence = audit.get("conversation_evidence", [])
    scopes = audit.get("result_scope")
    if isinstance(scopes, dict):
        scopes = [scopes]
    if not evidence and audit.get("tool") not in {"reply", "clarify"}:
        if not scopes:
            scopes = [v["scope"] for v in views if v.get("scope")]
        evidence = [{"id": "result", "summary": reply, "action": action,
                     "data": audit.get("result_data"), "scopes": scopes or [],
                     "workflows": [_safe_workflow_evidence(view) for view in views[-3:]]}]
    return cast(
        dict[str, Any],
        public_data(
            {
                "evidence": _strip_import_artifacts(evidence or previous.get("evidence", [])),
                "result_scope": scopes if scopes is not None else previous.get("result_scope", []),
            }
        ),
    )
