"""Real database inbox acceptance; all external message transport is replaced."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text

from src.core.db import session_scope
from src.integrations.whatsapp import WhatsAppClient
from src.modules.admin_chat.workflow_models import Workflow, WorkflowAction
from src.modules.agents.runtime_models import AgentRuntimeJob
from src.modules.discovery.models import Lead, LeadContact
from src.modules.outreach.models import Conversation, Message
from src.modules.outreach.webhooks import _handle_statuses
from tests.test_chat_outbound import setup
from tests.test_company_workspace import client as client_fixture
from tests.test_progressive_workflows import act, start

client = client_fixture


async def seeded(client, monkeypatch, expired=False):
    headers, tid, uid, _, sid = await setup(client, monkeypatch)
    async with session_scope(tid) as db:
        lead = Lead(
            tenant_id=tid,
            company_name="Inbox Example",
            person_name="Deniz",
            normalized_name="inbox example",
            source="test",
            discovered_at=datetime.now(UTC),
        )
        db.add(lead)
        await db.flush()
        contact = LeadContact(
            tenant_id=tid,
            lead_id=lead.id,
            type="phone",
            raw_value="+15550102030",
            normalized_value="+15550102030",
        )
        db.add(contact)
        await db.flush()
        conv = Conversation(tenant_id=tid, lead_id=lead.id, contact_id=contact.id)
        db.add(conv)
        await db.flush()
        msg = Message(
            tenant_id=tid,
            conversation_id=conv.id,
            direction="inbound",
            body="Ürün bilgisi istiyorum.",
            created_at=datetime.now(UTC) - timedelta(hours=25 if expired else 1),
        )
        db.add(msg)
        await db.flush()
        job = AgentRuntimeJob(
            tenant_id=tid,
            conversation_id=conv.id,
            inbound_message_id=msg.id,
            status="handoff",
            error="Human review",
        )
        db.add(job)
        await db.commit()
    return headers, tid, uid, sid, conv.id, job.id


async def views(client, headers, sid):
    response = await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def reviewed(client, headers, sid, cid):
    response = await start(
        client, headers, sid, "reply", {"conversation": str(cid), "content": "İnceliyoruz."}
    )
    assert response.status_code == 200, response.text
    response = await act(client, headers, response.json(), "continue")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    return response.json()


@pytest.mark.parametrize("delivery", ["accepted", "ambiguous", "early_delivered"])
async def test_reply_durable_receipt_before_transport_and_no_resend(client, monkeypatch, delivery):
    headers, tid, uid, sid, cid, _ = await seeded(client, monkeypatch)
    row = await reviewed(client, headers, sid, cid)
    back = (await act(client, headers, row, "back")).json()
    assert not back["changes"]
    row = (await act(client, headers, back, "continue")).json()
    operation = uuid4()
    calls = []

    async def send(self, to, body, **kwargs):
        calls.append((to, body))
        mid = UUID(kwargs["callback_data"].split(":")[1])
        async with session_scope(tid) as db:
            await db.execute(
                text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(uid)}
            )
            message = await db.get(Message, mid)
            assert message.raw["manual_send_state"] == "sending"
            workflow = await db.get(Workflow, UUID(row["id"]))
            assert workflow.status == "running" and workflow.state["message_id"] == str(mid)
            receipt = await db.scalar(
                select(WorkflowAction).where(WorkflowAction.client_operation_id == operation)
            )
            assert receipt.response["status"] == "running"
        if delivery == "early_delivered":
            async with session_scope(tid) as db:
                await _handle_statuses(
                    db,
                    tid,
                    [
                        {
                            "id": "wamid.inbox-test",
                            "status": "delivered",
                            "biz_opaque_callback_data": kwargs["callback_data"],
                            "timestamp": "1789382000",
                        }
                    ],
                )
                await db.commit()
        if delivery != "accepted":
            raise httpx.ReadTimeout("connection lost")
        return {"messages": [{"id": "wamid.inbox-test"}]}

    monkeypatch.setattr(WhatsAppClient, "send_text_once", send)
    response = await act(client, headers, row, "complete", mid=operation)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "running"
    current = (await views(client, headers, sid))[-1]
    assert current["status"] == "completed", current
    assert current["result"]["outcome"] == (
        "delivered" if delivery == "early_delivered" else delivery
    )
    assert (await act(client, headers, row, "complete", mid=operation)).json() == response.json()
    assert len(calls) == 1
    assert (await views(client, headers, sid))[-1]["revision"] == current["revision"]
    if delivery == "ambiguous":
        next_row = await reviewed(client, headers, sid, cid)
        assert (await act(client, headers, next_row, "complete")).status_code == 409
        assert len(calls) == 1


async def test_inbox_navigation_resume_and_cancel_stay_durable(client, monkeypatch):
    headers, tid, _, sid, cid, jid = await seeded(client, monkeypatch)
    response = await start(client, headers, sid, "records", {"category": "inbox", "q": "Deniz"})
    assert response.status_code == 200, response.text
    listing = response.json()
    assert len(listing["records"]) == 1
    response = await act(
        client, headers, listing, "launch", {"operation": "conversation", "record": str(cid)}
    )
    assert response.status_code == 200, response.text
    conversation = (await views(client, headers, sid))[-1]
    assert conversation["records"][0]["details"]["Mesaj"] == "Ürün bilgisi istiyorum."
    response = await act(client, headers, conversation, "launch", {"operation": "resume_bot"})
    assert response.status_code == 200, response.text
    resume = (await views(client, headers, sid))[-1]
    resume = (await act(client, headers, resume, "continue")).json()
    operation = uuid4()
    response = await act(client, headers, resume, "complete", mid=operation)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["outcome"] == "resumed"
    assert (await act(client, headers, resume, "complete", mid=operation)).json() == response.json()
    async with session_scope(tid) as db:
        assert (await db.get(AgentRuntimeJob, jid)).status == "resolved"
        assert (
            len(list(await db.scalars(select(Message).where(Message.conversation_id == cid)))) == 1
        )
    conversation = next(r for r in await views(client, headers, sid) if r["kind"] == "conversation")
    cancelled = await act(client, headers, conversation, "cancel")
    assert cancelled.status_code == 200, cancelled.text
    conversation = next(r for r in await views(client, headers, sid) if r["kind"] == "conversation")
    assert conversation["status"] == "cancelled"


async def test_closed_window_has_no_receipt_or_send(client, monkeypatch):
    headers, tid, uid, sid, cid, _ = await seeded(client, monkeypatch, expired=True)
    row = await reviewed(client, headers, sid, cid)
    operation = uuid4()

    async def forbidden(*args, **kwargs):
        pytest.fail("Closed window must not reach transport")

    monkeypatch.setattr(WhatsAppClient, "send_text_once", forbidden)
    response = await act(client, headers, row, "complete", mid=operation)
    assert response.status_code == 409, response.text
    async with session_scope(tid) as db:
        await db.execute(
            text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(uid)}
        )
        assert (
            await db.scalar(
                select(WorkflowAction).where(WorkflowAction.client_operation_id == operation)
            )
            is None
        )
        assert (
            len(list(await db.scalars(select(Message).where(Message.conversation_id == cid)))) == 1
        )
    assert (await views(client, headers, sid))[-1]["status"] == "ready"


async def test_inbox_isolation_and_current_role(client, monkeypatch):
    from src.modules.auth.models import User, UserRole
    from tests.test_chat_workspace import chat
    from tests.test_company_workspace import account

    headers, tid, uid, sid, cid, _ = await seeded(client, monkeypatch)
    other, _ = await account(client)
    other_sid = await chat(client, other)
    assert (
        await start(client, other, other_sid, "conversation", {"conversation": str(cid)})
    ).status_code == 404
    row = await reviewed(client, headers, sid, cid)
    async with session_scope(tid) as db:
        user = await db.get(User, uid)
        user.role = UserRole.VIEWER
        await db.commit()
    assert (await act(client, headers, row, "complete")).status_code == 403
    response = await start(client, headers, sid, "conversation", {"conversation": str(cid)})
    assert response.status_code == 200, response.text
    assert response.json()["record_actions"] == []


async def test_optout_rejects_manual_reply_and_resume(client, monkeypatch):
    from src.modules.compliance.models import OptOut

    headers, tid, _, sid, cid, _ = await seeded(client, monkeypatch)
    row = await reviewed(client, headers, sid, cid)
    async with session_scope(tid) as db:
        db.add(OptOut(tenant_id=tid, phone_e164="+15550102030", source="manual"))
        await db.commit()

    async def forbidden(*args, **kwargs):
        pytest.fail("Opt-out must not reach transport")

    monkeypatch.setattr(WhatsAppClient, "send_text_once", forbidden)
    assert (await act(client, headers, row, "complete")).status_code == 409
    response = await start(client, headers, sid, "resume_bot", {"conversation": str(cid)})
    assert response.status_code == 200, response.text
    ready = (await act(client, headers, response.json(), "continue")).json()
    assert (await act(client, headers, ready, "complete")).status_code == 409
