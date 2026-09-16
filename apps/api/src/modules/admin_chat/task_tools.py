"""Adapters to canonical tenant-scoped capabilities. No SQL or routes from the model."""

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

from src.modules.discovery.models import Lead, LeadContact

from . import (
    planner,
    service,
    workflow_inbox,
    workflow_intents,
    workflow_records,
    workflow_requests,
    workflows,
)
from .task_schema import TaskGoal, TaskStep


def draft(kind: str, fields: dict[str, str]) -> Any:
    return SimpleNamespace(
        kind=kind, fields=fields, state={}, status="awaiting_input", step="details"
    )


async def read(
    db: Any,
    claims: Any,
    user: Any,
    session: Any,
    step: TaskStep,
    references: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if step.tool == "search":
        if not step.category:
            raise ValueError("Search requires a category")
        row = draft(
            "records",
            {
                "category": step.category,
                "q": step.query,
                "page": str(step.page),
                "status": step.status or "",
                "today": "true" if step.today else "false",
                **({"agent": step.agent} if step.agent else {}),
            },
        )
        await workflow_records.initialize(db, user, session, row)
        return {**row.state, "category": step.category, "fields": row.fields}
    if step.tool in {"conversation", "request", "delivery"}:
        ref = references.get(step.ref)
        if not ref:
            raise ValueError("Select a reference returned by a tool")
        category, identity = ref["category"], ref["id"]
        if category in {"requests", "quotes"}:
            request = await workflow_requests.own(db, user, identity)
            cid = str(request.conversation_id)
        elif category == "inbox" and step.tool != "request":
            cid = identity
        elif category == "messages" and step.tool in {"conversation", "delivery"}:
            cid = ref["conversation"]
        else:
            raise ValueError("Reference type does not support this tool")
        if step.tool == "request":
            total, records = await workflow_requests.details(db, user, identity, step.page)
            return {
                "category": "request_details",
                "records": records,
                "total": total,
                "has_more": step.page * 20 < total,
                "page": step.page,
                "fields": {
                    "category": "request_details",
                    "request": identity,
                    "page": str(step.page),
                },
            }
        conv = await workflow_inbox.own(db, user, cid)
        if step.tool == "delivery":
            contact = await db.get(LeadContact, conv.contact_id)
            lead = await db.get(Lead, conv.lead_id)
            return {
                "subject": {
                    "name": lead.person_name if lead else "",
                    "contact": contact.normalized_value if contact else "",
                },
                "output": {"summary": await workflow_inbox.delivery_summary(db, user, UUID(cid))},
            }
        row = draft("conversation", {"conversation": cid, "page": str(step.page)})
        await workflow_inbox.refresh(db, user, row)
        return {**row.state, "category": "messages", "fields": row.fields}
    if step.tool in {"analytics", "capacity", "templates"}:
        # Do not let legacy read tools clear the real session's workflow context.
        shadow = SimpleNamespace(id=session.id, context=dict(session.context))
        reply, cards, _, _ = await service.execute(
            db,
            claims,
            user,
            shadow,
            planner.Intent(
                tool=step.tool, target=step.query or None, status=step.status, today=step.today
            ),
        )
        return {"output": {"summary": reply}, "cards": cards}
    raise ValueError("Unsupported read capability")


async def prepare(
    db: Any,
    user: Any,
    session: Any,
    goal: TaskGoal,
    step: TaskStep,
    references: dict[str, dict[str, Any]],
    client_id: UUID,
) -> dict[str, Any]:
    if goal.kind != "workflow":
        raise ValueError("A read goal cannot prepare a change")
    # This planner sees ONLY the original operator instruction, never retrieved messages.
    intent, source = await planner.plan(goal.text, allow_task=False)
    if source == "model_rejected_write":
        raise ValueError("The operator did not request this change")
    intent = workflow_intents.normalize(intent)
    ref = references.get(step.ref) if step.ref else None
    if step.ref and not ref:
        raise ValueError("Unknown preparation reference")
    if intent.tool in workflow_requests.OPERATIONS and ref:
        if ref["category"] not in {"requests", "quotes"}:
            raise ValueError("A request change requires a request reference")
        intent.target = ref["id"]
    intent, message = await workflow_requests.route_intent(db, user, session, intent)
    if message or intent.tool != "workflow" or intent.workflow_action != "start":
        raise ValueError("Prepare a new review; confirm existing work through its card")
    if intent.workflow_kind in {"records", "conversation"}:
        raise ValueError("Preparation must represent the requested change")
    if ref and intent.workflow_kind in {"reply", "resume_bot"}:
        if ref["category"] != "inbox":
            raise ValueError("A reply requires a conversation reference")
        intent.workflow_fields["conversation"] = ref["id"]
    if ref and intent.workflow_kind == "request_update":
        if ref["category"] not in {"requests", "quotes"}:
            raise ValueError("A request update requires a request reference")
        intent.workflow_fields["request"] = ref["id"]
    reply, _, action, audit = await workflows.execute_intent(
        db, user, session, intent, uuid5(client_id, f"task-prepare:{step.goal}")
    )
    return {
        "output": {"summary": reply},
        "action": action,
        "workflow": audit,
        "prepared": {"kind": intent.workflow_kind, "values": intent.workflow_fields},
    }


async def present(
    db: Any, user: Any, session: Any, client_id: UUID, observations: list[dict[str, Any]]
) -> None:
    """Expose the last page of each read as an ordinary navigable, persisted card."""
    chosen: dict[str, dict[str, Any]] = {}
    if any(o["tool"] == "prepare" and not o.get("error") for o in observations):
        return  # Keep the prepared review in front; read sources remain in the task result.
    for obs in observations:
        if obs["tool"] in {"conversation", "request", "search"} and obs.get("fields"):
            chosen[f"{obs['goal']}:{obs['tool']}"] = obs
    for key, obs in chosen.items():
        # A successful detail read supersedes its intermediate list for this goal.
        if obs["tool"] == "search" and any(
            other["goal"] == obs["goal"] and other["tool"] in {"conversation", "request"}
            for other in chosen.values()
        ):
            continue
        kind = "conversation" if obs["tool"] == "conversation" else "records"
        await workflows.execute_intent(
            db,
            user,
            session,
            planner.Intent(
                tool="workflow",
                workflow_kind=kind,
                workflow_action="start",
                workflow_fields=obs["fields"],
            ),
            uuid5(client_id, f"task-view:{key}"),
        )
