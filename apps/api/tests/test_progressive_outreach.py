"""Durable workflow queue acceptance; provider reads are mocked, no messages sent."""

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.core.db import session_scope
from src.modules.admin_chat.outbound_models import OutboundRecipient
from tests.test_chat_outbound import setup
from tests.test_company_workspace import client as client_fixture
from tests.test_progressive_workflows import act, start

client = client_fixture


async def reviewed(client, monkeypatch):
    headers, tid, uid, sender, sid = await setup(client, monkeypatch)
    response = await start(
        client, headers, sid, "outreach", {"recipients": "+15550102030\n+15550102031"}
    )
    assert response.status_code == 200, response.text
    row = (await act(client, headers, response.json(), "continue")).json()
    assert row["step"] == "compose", row
    assert any(c["key"] == "var_1" for c in row["controls"])
    invalid = (await act(client, headers, row, "continue")).json()
    assert "var_1" in invalid["errors"]
    response = await act(
        client,
        headers,
        invalid,
        "continue",
        {"var_1": "Ayşe", "consent_evidence": "Web formu tanıtım izni 2026-09-14"},
    )
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["status"] == "ready" and row["step"] == "review", row
    assert len(row["records"]) == 2
    assert "Ayşe" in row["output"]["summary"]
    return headers, tid, sid, row


async def test_queue_receipt_background_cancel_and_no_duplicate(client, monkeypatch):
    headers, tid, sid, row = await reviewed(client, monkeypatch)
    mid = uuid4()
    sent = await act(client, headers, row, "complete", mid=mid)
    assert sent.status_code == 200, sent.text
    queued = sent.json()
    assert queued["status"] == "running", queued
    assert queued["result"]["outcome"] == "queued"
    assert (await act(client, headers, row, "complete", mid=mid)).json() == queued
    assert (await act(client, headers, queued, "update", {"var_1": "Other"})).status_code == 409
    assert (await start(client, headers, sid)).status_code == 200
    rows = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()
    paused = next(r for r in rows if r["id"] == queued["id"])
    assert paused["status"] == "paused"
    cancelled = await act(client, headers, paused, "cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    async with session_scope(tid) as db:
        recipients = list(
            (
                await db.scalars(
                    select(OutboundRecipient).where(
                        OutboundRecipient.batch_id == UUID(queued["result"]["batch_id"])
                    )
                )
            ).all()
        )
        assert len(recipients) == 2
        assert {r.status for r in recipients} == {"cancelled"}


async def test_delivery_sync_is_stable_and_old_review_is_invalidated(client, monkeypatch):
    headers, tid, sid, row = await reviewed(client, monkeypatch)
    back = (await act(client, headers, row, "back")).json()
    assert back["step"] == "compose" and not back["records"]
    assert (await act(client, headers, row, "complete")).status_code == 409
    row = (await act(client, headers, back, "continue")).json()
    queued = (await act(client, headers, row, "complete")).json()
    async with session_scope(tid) as db:
        recipients = await db.scalars(
            select(OutboundRecipient).where(
                OutboundRecipient.batch_id == UUID(queued["result"]["batch_id"])
            )
        )
        for recipient in recipients:
            recipient.status = "accepted"
        await db.commit()
    url = f"/api/v1/admin-chat/sessions/{sid}/workflows"
    done = (await client.get(url, headers=headers)).json()[0]
    assert done["status"] == "completed"
    assert all(r["subtitle"] == "Meta kabul etti" for r in done["records"])
    assert (await client.get(url, headers=headers)).json()[0]["revision"] == done["revision"]
    assert (await act(client, headers, done, "complete")).status_code == 409


@pytest.mark.parametrize(
    "failure", [HTTPException(503, "Şablon servisi yanıt vermedi."), httpx.ConnectError("offline")]
)
async def test_template_outage_preserves_input_and_manager_access(client, monkeypatch, failure):
    from src.modules.admin_chat import outbound
    from src.modules.auth.models import UserRole
    from tests.test_chat_workspace import chat
    from tests.test_company_workspace import account

    headers, _, _, _, sid = await setup(client, monkeypatch)

    async def unavailable(sender):
        raise failure

    monkeypatch.setattr(outbound, "meta_templates", unavailable)
    row = (await start(client, headers, sid, "outreach", {"recipients": "+15550102030"})).json()
    failed = await act(client, headers, row, "continue", {"purpose": "Yeni ürün tanıtımı"})
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    assert failed.json()["fields"]["purpose"] == "Yeni ürün tanıtımı"
    assert failed.json()["step"] == "details"
    other, _ = await account(client, UserRole.SALES_AGENT)
    other_sid = await chat(client, other)
    assert (await start(client, other, other_sid, "outreach")).status_code == 403
