"""Real PostgreSQL/RLS outbox tests; provider transport is mocked, no real messages sent."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.core.db import session_scope
from src.modules.admin_chat import outbound, planner
from src.modules.admin_chat.outbound_models import OutboundRecipient
from src.modules.agents.models import Agent
from src.modules.auth.models import Tenant, User, UserRole
from src.modules.compliance.models import OptOut, OptOutSource
from src.modules.outreach.models import SenderProfile
from src.workers.chat_outbound import dispatch, send_one
from tests.test_company_workspace import account
from tests.test_company_workspace import client as client_fixture

client = client_fixture

TEMPLATE = {
    "id": "123",
    "name": "intro",
    "language": "tr",
    "body": "Merhaba {{1}}, ürünlerimizi tanıyalım.",
    "variables": ["1"],
    "header": "",
    "footer": "",
    "buttons": ["Bilgi Al", "İlgilenmiyorum"],
}


async def setup(client, monkeypatch, role=UserRole.TENANT_OWNER):
    headers, slug = await account(client, role)
    waba = str(uuid4().int)[:15]
    async with session_scope() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.slug == slug))
        tid = tenant.id
        tenant.wa_business_account_id = waba
        await db.commit()
    async with session_scope(tid) as db:
        user = await db.scalar(select(User).where(User.tenant_id == tid))
        agent = Agent(tenant_id=tid, slug="main", name="Company Agent")
        db.add(agent)
        await db.flush()
        sender = SenderProfile(
            tenant_id=tid,
            agent_id=agent.id,
            display_name="Main",
            phone_number_id=uuid4().int.__str__()[:15],
            business_account_id=waba,
            daily_cap=100,
        )
        db.add(sender)
        await db.commit()
        sender_id = sender.id
    from src.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", sender.phone_number_id)
    monkeypatch.setattr(settings, "whatsapp_business_account_id", waba)

    async def read(self, object_id, fields, **kwargs):
        if kwargs.get("edge"):
            return {
                "data": [
                    {
                        "id": "123",
                        "name": "intro",
                        "language": "tr",
                        "category": "MARKETING",
                        "status": "APPROVED",
                        "components": [
                            {"type": "BODY", "text": TEMPLATE["body"]},
                            {
                                "type": "BUTTONS",
                                "buttons": [
                                    {"type": "QUICK_REPLY", "text": b} for b in TEMPLATE["buttons"]
                                ],
                            },
                        ],
                    }
                ]
            }
        return {"whatsapp_business_manager_messaging_limit": "TIER_2K"}

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.business_read", read)

    class LLM:
        async def complete(self, messages, **kwargs):
            if "template_id" in kwargs["response_schema"].get("properties", {}):
                return '{"template_id":"123"}'
            return '{"tool":"outreach","recipients":["+15550102030","+15550102031"],"purpose":"tanıtım"}'

    monkeypatch.setattr(planner, "get_llm_client", lambda *_args, **_kwargs: LLM())
    monkeypatch.setattr(outbound, "get_llm_client", lambda *_args, **_kwargs: LLM())
    sid = (await client.post("/api/v1/admin-chat/sessions", headers=headers, json={})).json()["id"]
    return headers, tid, user.id, sender_id, sid


async def prepare(client, headers, sid):
    payload = {
        "text": "+15550102030 ve +15550102031 için tanıtım",
        "client_message_id": str(uuid4()),
    }
    url = f"/api/v1/admin-chat/sessions/{sid}/turns"
    from unittest.mock import patch

    with patch(
        "src.modules.admin_chat.workflow_intents.normalize", side_effect=lambda intent: intent
    ):
        response = await client.post(url, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["response_source"] == "model"
    assert (await client.post(url, headers=headers, json=payload)).json() == response.json()
    return response.json()["cards"][0]["batch_id"]


async def queue(client, headers, bid):
    return await client.post(
        f"/api/v1/admin-chat/batches/{bid}/actions",
        headers=headers,
        json={
            "action": "send",
            "variables": {"1": "Müşterimiz"},
            "consent_evidence": "2026-09-14 web formu kaydı TEST-123",
        },
    )


def test_numbers_deduplicate_and_reject_partial_invalid_list():
    assert outbound.phones(["+1 (555) 010-2030", "0015550102030"]) == ["+15550102030"]
    with pytest.raises(HTTPException):
        outbound.phones(["+15550102030", "0532bad"])


async def test_template_read_preserves_real_meta_quick_replies(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)
    card = (await client.get(f"/api/v1/admin-chat/batches/{bid}", headers=headers)).json()
    assert card["buttons"] == ["Bilgi Al", "İlgilenmiyorum"]
    assert all(r["reason"] == "Tanıtım izni kaydı gerekli." for r in card["recipients"])
    cap = (await client.get("/api/v1/admin-chat/capacity", headers=headers)).json()
    assert cap["meta_limit"] == 2000 and cap["meta_remaining"] is None
    assert cap["local_cap"] == 100
    other, _ = await account(client)
    assert (await client.get(f"/api/v1/admin-chat/batches/{bid}", headers=other)).status_code == 404
    assert (await client.get("/api/v1/admin-chat/capacity", headers=other)).json()[
        "connected"
    ] is False


async def test_queue_requires_consent_and_fields_and_is_idempotent(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)
    url = f"/api/v1/admin-chat/batches/{bid}/actions"
    assert (await client.post(url, headers=headers, json={"action": "send"})).status_code == 422
    assert (
        await client.post(url, headers=headers, json={"action": "send", "variables": {"1": "Ali"}})
    ).status_code == 422
    first = await queue(client, headers, bid)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "queued"
    assert (await queue(client, headers, bid)).json() == first.json()
    async with session_scope(tid) as db:
        assert (
            len(
                list(
                    (
                        await db.scalars(
                            select(OutboundRecipient).where(OutboundRecipient.batch_id == UUID(bid))
                        )
                    ).all()
                )
            )
            == 2
        )
    cap = (await client.get("/api/v1/admin-chat/capacity", headers=headers)).json()
    assert cap["local_remaining"] == 98


async def test_worker_concurrent_claim_exactly_once_and_receipts(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)
    assert (await queue(client, headers, bid)).status_code == 200
    async with session_scope(tid) as db:
        rid = await db.scalar(
            select(OutboundRecipient.id).where(OutboundRecipient.batch_id == UUID(bid))
        )
    sent = []

    async def transport(self, *args, **kwargs):
        sent.append((args, kwargs))
        await asyncio.sleep(0.02)
        return {"messages": [{"id": "wamid." + str(rid)}]}

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.send_template_once", transport)
    await asyncio.gather(send_one(tid, rid), send_one(tid, rid))
    assert len(sent) == 1
    assert sent[0][0][1] == "intro"
    assert sent[0][0][3][1]["parameters"][0]["payload"] == "Bilgi Al"
    from src.modules.outreach.webhooks import _handle_statuses

    async with session_scope(tid) as db:
        row = await db.get(OutboundRecipient, rid)
        assert row.status == "accepted"
        for state in ["read", "delivered", "sent"]:
            await _handle_statuses(
                db,
                tid,
                [
                    {
                        "id": row.wa_message_id,
                        "status": state,
                        "timestamp": str(int(datetime.now(UTC).timestamp())),
                    }
                ],
            )
            await db.flush()
        assert row.status == "read"
        await db.commit()
    await send_one(tid, rid)
    assert len(sent) == 1


async def test_ambiguous_send_and_crashed_sending_are_never_replayed(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)
    assert (await queue(client, headers, bid)).status_code == 200
    async with session_scope(tid) as db:
        rows = list(
            (
                await db.scalars(
                    select(OutboundRecipient).where(OutboundRecipient.batch_id == UUID(bid))
                )
            ).all()
        )
        rid = rows[0].id
        rows[1].status = "sending"
        rows[1].attempted_at = datetime.now(UTC) - timedelta(minutes=10)
        await db.commit()
    calls = []

    async def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("accepted then timed out")

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.send_template_once", fail)
    await send_one(tid, rid)
    await dispatch(tid)
    await dispatch(tid)
    assert len(calls) == 1
    async with session_scope(tid) as db:
        assert set(
            (
                await db.scalars(
                    select(OutboundRecipient.status).where(OutboundRecipient.batch_id == UUID(bid))
                )
            ).all()
        ) == {"ambiguous"}


async def test_optout_after_queue_blocks_before_transport(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)
    assert (await queue(client, headers, bid)).status_code == 200
    async with session_scope(tid) as db:
        row = await db.scalar(
            select(OutboundRecipient).where(OutboundRecipient.batch_id == UUID(bid))
        )
        rid = row.id
        db.add(OptOut(tenant_id=tid, phone_e164=row.phone, source=OptOutSource.USER_REPLY))
        await db.commit()

    async def never(*args, **kwargs):
        raise AssertionError("Must not send")

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.send_template_once", never)
    await send_one(tid, rid)
    async with session_scope(tid) as db:
        assert (await db.get(OutboundRecipient, rid)).status == "blocked"


async def test_concurrent_batches_cannot_overbook_local_capacity(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)
    async with session_scope(tid) as db:
        sender = await db.get(SenderProfile, sender_id)
        sender.daily_cap = 1
        await db.commit()
    assert (await queue(client, headers, bid)).status_code == 409
    async with session_scope(tid) as db:
        assert set(
            (
                await db.scalars(
                    select(OutboundRecipient.status).where(OutboundRecipient.batch_id == UUID(bid))
                )
            ).all()
        ) == {"draft"}


async def test_sales_agent_cannot_prepare_outreach(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch, UserRole.SALES_AGENT)
    response = await client.post(
        f"/api/v1/admin-chat/sessions/{sid}/turns",
        headers=headers,
        json={
            "text": "+15550102030 ve +15550102031 için tanıtım",
            "client_message_id": str(uuid4()),
        },
    )
    assert response.status_code == 403


async def test_model_cannot_invent_recipient_or_execute_negated_send(monkeypatch):
    class LLM:
        async def complete(self, *args, **kwargs):
            return '{"tool":"outreach","recipients":["+19998887777"]}'

    monkeypatch.setattr(planner, "get_llm_client", lambda *_args, **_kwargs: LLM())
    with pytest.raises(ValueError):
        await planner.plan("+15550102030 numarasına gönder")

    class Wrong:
        async def complete(self, *args, **kwargs):
            return '{"tool":"send_outreach"}'

    monkeypatch.setattr(planner, "get_llm_client", lambda *_args, **_kwargs: Wrong())
    intent, source = await planner.plan("Bu tanıtımı gönderme")
    assert intent.tool == "clarify"


async def test_meta_failure_and_template_revocation_do_not_queue(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)

    async def failure(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.business_read", failure)
    cap = (await client.get("/api/v1/admin-chat/capacity", headers=headers)).json()
    assert cap["meta_available"] is False and cap["meta_limit"] is None
    assert (await queue(client, headers, bid)).status_code == 503

    async def revoked(*args, **kwargs):
        return {"data": []}

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.business_read", revoked)
    assert (await queue(client, headers, bid)).status_code == 409


async def test_actual_concurrent_reservations_and_rls(client, monkeypatch):
    from sqlalchemy import text

    from src.core.db import set_tenant_context
    from src.modules.admin_chat.outbound_models import OutboundBatch

    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    one = await prepare(client, headers, sid)
    two = await prepare(client, headers, sid)
    async with session_scope(tid) as db:
        rows = list(
            (
                await db.scalars(
                    select(OutboundRecipient).where(OutboundRecipient.batch_id == UUID(two))
                )
            ).all()
        )
        for i, row in enumerate(rows):
            row.phone = f"+1555010204{i}"
        sender = await db.get(SenderProfile, sender_id)
        sender.daily_cap = 2
        await db.commit()
    results = await asyncio.gather(queue(client, headers, one), queue(client, headers, two))
    assert sorted(r.status_code for r in results) == [200, 409]
    async with session_scope(tid) as db:
        await set_tenant_context(db, uuid4())
        assert (await db.scalars(select(OutboundBatch))).all() == []
        assert (await db.scalars(select(OutboundRecipient))).all() == []
        assert await db.scalar(text("SELECT count(*) FROM chat_outbound_batches")) == 0


async def test_disable_user_and_cancel_prevent_dispatch(client, monkeypatch):
    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)
    assert (await queue(client, headers, bid)).status_code == 200
    async with session_scope(tid) as db:
        user = await db.get(User, uid)
        user.is_active = False
        await db.commit()
    calls = []

    async def transport(*args, **kwargs):
        calls.append(1)
        return {"messages": [{"id": "unexpected"}]}

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.send_template_once", transport)
    await dispatch(tid)
    assert not calls
    assert (
        await client.get(f"/api/v1/admin-chat/batches/{bid}", headers=headers)
    ).status_code == 401


async def test_legacy_queue_never_sends_pending_jobs(client, monkeypatch):
    from src.workers.outreach import _dispatch

    calls = []

    async def transport(*args, **kwargs):
        calls.append(1)
        raise AssertionError("No legacy send")

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.send_template", transport)
    monkeypatch.setattr("src.workers.outreach.dispatch", lambda tid: asyncio.sleep(0))
    await _dispatch()
    assert not calls


async def test_explicit_consent_cannot_override_blacklisted_customer(client, monkeypatch):
    from src.modules.discovery.models import ContactType, Lead, LeadContact, LeadStatus

    headers, tid, uid, sender_id, sid = await setup(client, monkeypatch)
    bid = await prepare(client, headers, sid)
    async with session_scope(tid) as db:
        lead = Lead(
            tenant_id=tid,
            company_name="Blocked customer",
            normalized_name="blocked",
            source="test",
            discovered_at=datetime.now(UTC),
            status=LeadStatus.BLACKLISTED,
        )
        db.add(lead)
        await db.flush()
        db.add(
            LeadContact(
                tenant_id=tid,
                lead_id=lead.id,
                type=ContactType.PHONE,
                raw_value="+15550102030",
                normalized_value="+15550102030",
            )
        )
        await db.commit()
    response = await queue(client, headers, bid)
    assert response.status_code == 200
    recipient = next(r for r in response.json()["recipients"] if r["phone"] == "+15550102030")
    assert recipient["status"] == "blocked"
    assert "engelli" in recipient["reason"]
