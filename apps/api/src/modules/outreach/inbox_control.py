from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import ClaimsDep, DBSessionDep
from src.core.errors import ConflictError, NotFoundError
from src.core.rbac import RequireAgent
from src.modules.agents.runtime_models import AgentRuntimeJob
from src.modules.compliance.models import AuditLog, OptOut
from src.modules.discovery.models import LeadContact

from .channel import resolve_channel
from .models import Conversation, Message, MessageDirection

router = APIRouter(tags=["inbox-control"])


@router.get("/senders/connection")
async def connection(db: DBSessionDep, claims: ClaimsDep) -> dict[str, Any]:
    try:
        sender = await resolve_channel(db, UUID(claims["tid"]))
        return {
            "connected": True,
            "agent_id": str(sender.agent_id),
            "display_name": sender.display_name,
        }
    except ConflictError:
        return {"connected": False, "agent_id": None}


async def bot_state(
    db: AsyncSession, tid: UUID, cid: UUID
) -> tuple[Conversation, LeadContact | None, list[AgentRuntimeJob], bool, bool]:
    conv = await db.get(Conversation, cid)
    if conv is None or conv.tenant_id != tid:
        raise NotFoundError("Conversation")
    contact = await db.get(LeadContact, conv.contact_id)
    opted_out = bool(
        contact
        and (
            await db.execute(
                select(OptOut.id).where(
                    OptOut.tenant_id == tid, OptOut.phone_e164 == contact.normalized_value
                )
            )
        ).first()
    )
    jobs = list(
        (
            await db.execute(
                select(AgentRuntimeJob).where(
                    AgentRuntimeJob.tenant_id == tid,
                    AgentRuntimeJob.conversation_id == cid,
                    AgentRuntimeJob.status.in_(["handoff", "failed", "sending"]),
                )
            )
        ).scalars()
    )
    manual = (
        await db.execute(
            select(Message.id).where(
                Message.tenant_id == tid,
                Message.conversation_id == cid,
                Message.direction == MessageDirection.OUTBOUND,
                Message.raw["manual_send_state"].astext.in_(["sending", "ambiguous"]),
            )
        )
    ).first()
    return conv, contact, jobs, opted_out, bool(manual)


@router.get("/conversations/{conv_id}/bot-state")
async def read_state(conv_id: UUID, db: DBSessionDep, claims: ClaimsDep) -> dict[str, Any]:
    _, _, jobs, opted_out, manual = await bot_state(db, UUID(claims["tid"]), conv_id)
    reasons = [j.error or ("Human review" if j.status == "handoff" else j.status) for j in jobs]
    if opted_out:
        reasons.append("Customer opted out")
    if manual:
        reasons.append("Manual message delivery is not confirmed; review before sending again")
    return {
        "paused": bool(jobs or opted_out or manual),
        "reasons": reasons,
        "can_resume": bool(jobs)
        and not opted_out
        and not manual
        and all(j.status != "sending" for j in jobs),
    }


@router.post("/conversations/{conv_id}/resume")
async def resume(conv_id: UUID, db: DBSessionDep, claims: RequireAgent) -> dict[str, Any]:
    tid = UUID(claims["tid"])
    conv = await db.get(Conversation, conv_id)
    if conv is None or conv.tenant_id != tid:
        raise NotFoundError("Conversation")
    contact = await db.get(LeadContact, conv.contact_id)
    if contact is None:
        raise NotFoundError("Contact")
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"{tid}:{contact.normalized_value}"},
    )
    _, _, jobs, opted_out, manual = await bot_state(db, tid, conv_id)
    if opted_out or manual or any(j.status == "sending" for j in jobs):
        raise ConflictError("Cannot resume an opted-out conversation or ambiguous send")
    for job in jobs:
        job.status = "resolved"
        job.audit = {
            **(job.audit or {}),
            "manual_review_required": False,
            "human_review_resolved_at": datetime.now(UTC).isoformat(),
            "resolved_by_user_id": claims["sub"],
            "reason": "explicit_inbox_resume",
        }
    db.add(
        AuditLog(
            tenant_id=tid,
            actor_id=UUID(claims["sub"]),
            action="resume_bot",
            entity="conversation",
            entity_id=str(conv_id),
        )
    )
    await db.commit()
    return {"ok": True}
