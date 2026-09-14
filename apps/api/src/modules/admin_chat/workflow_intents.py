"""Translate validated legacy planning vocabulary into the shared workflow UI.

Confirmation/cancellation/selection for already-stored historical workspace
cards retain compatibility through their explicit buttons. New outreach intents use workflows.
This conversion runs after grounding and
permission-language checks; it never supplies a missing role or approval.
"""

import re
from typing import Any

from .planner import Intent

RECORDS = {
    "inbox": "inbox",
    "agents": "agents",
    "knowledge": "knowledge",
    "versions": "versions",
    "team": "team",
    "platform": "companies",
}
CREATES = {"invite", "create_company", "create_agent"}


def normalize(intent: Intent) -> Intent:
    if intent.tool in {"search", "quotes"}:
        return Intent(
            tool="workflow",
            workflow_kind="records",
            workflow_action="start",
            workflow_fields={
                "category": "quotes" if intent.tool == "quotes" else "requests",
                "q": intent.target or "",
                "status": intent.status or "",
                "today": "true" if intent.today else "false",
            },
        )
    if intent.tool == "outreach":
        return Intent(
            tool="workflow",
            workflow_kind="outreach",
            workflow_action="start",
            workflow_fields={
                "recipients": "\n".join(
                    re.sub(r"[\s().-]", "", value) for value in intent.recipients
                ),
                "purpose": intent.purpose or "",
            },
        )
    if intent.tool in {"send_outreach", "cancel_outreach", "outreach_status"}:
        return Intent.model_validate(
            {
                "tool": "workflow",
                "workflow_kind": "outreach",
                "workflow_action": {
                    "send_outreach": "complete",
                    "cancel_outreach": "cancel",
                    "outreach_status": "inspect",
                }[intent.tool],
            }
        )
    if intent.tool != "workspace":
        return intent
    operation = intent.operation
    fields: dict[str, str] = {}
    if operation in RECORDS:
        kind = "records"
        fields["category"] = RECORDS[operation]
        if intent.target:
            fields["agent" if operation in {"knowledge", "versions"} else "q"] = intent.target
    elif operation in CREATES:
        kind = operation
        fields = {
            key: str(getattr(intent, key))
            for key in ("name", "slug", "email", "role")
            if getattr(intent, key)
        }
    elif operation in {"configure", "test", "publish"}:
        kind = operation
        if intent.target:
            fields["agent"] = intent.target
        if intent.instruction:
            fields["content"] = intent.instruction
        if operation == "configure":
            fields["format"] = "text"
    else:
        return intent
    return Intent.model_validate(
        {
            "tool": "workflow",
            "workflow_kind": kind,
            "workflow_action": "start",
            "workflow_fields": fields,
        }
    )


def ambiguous_approval(text: str, pending: bool, views: list[dict[str, Any]], intent: Intent) -> bool:
    """Generic natural approval cannot choose between old preview and current workflow."""
    import re

    from src.modules.selection.engine import normalize

    if not pending or text.startswith("action:"):
        return False
    if not any(v["status"] in {"awaiting_input", "ready", "failed", "running"} for v in views):
        return False
    confirms = (intent.tool == "workflow" and intent.workflow_action == "complete") or (
        intent.tool == "workspace" and intent.operation == "confirm"
    )
    if not confirms:
        return False
    normalized = normalize(text).strip(" .!?")
    return bool(
        re.fullmatch(
            r"(?:(?:bunu|bu islemi|islemi|degisikligi|degisiklikleri|onizlemeyi) )?"
            r"(?:onayla|uygula|kaydet|kabul et)",
            normalized,
        )
    )
