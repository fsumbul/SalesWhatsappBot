"""Private admin history and evidence memory. Never grants write authority."""

from typing import Any

from sqlalchemy import select

from src.integrations.llm import LLMMessage
from src.modules.conversation_language import public_data

from .models import AdminChatTurn


async def recall(db: Any, user: Any, session: Any) -> tuple[list[LLMMessage], dict[str, Any]]:
    rows = list((await db.scalars(select(AdminChatTurn).where(
        AdminChatTurn.session_id == session.id, AdminChatTurn.user_id == user.id,
        AdminChatTurn.tenant_id == user.tenant_id,
    ).order_by(AdminChatTurn.sequence.desc()).limit(4))).all())
    history = []
    for row in reversed(rows):
        history.extend([LLMMessage(role="user", content=row.text),
                        LLMMessage(role="assistant", content=row.response["reply"])])
    memory = rows[0].audit.get("language_memory", {}) if rows else {}
    return history, memory


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
                     "workflows": [{k: v[k] for k in ("kind", "status", "step", "output", "errors")}
                                   for v in views[-3:]]}]
    return public_data({"evidence": evidence or previous.get("evidence", []),
                        "result_scope": scopes if scopes is not None else previous.get("result_scope", [])})
