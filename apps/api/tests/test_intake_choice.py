"""One request can move between the customer web form and WhatsApp."""

import base64
from pathlib import Path
from uuid import uuid4

from jose import jwt

from src.core.config import get_settings
from src.core.db import session_scope
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.outreach.models import Conversation, Message
from src.modules.selection.access import form_token
from src.modules.selection.models import SelectionRequest
from src.modules.selection.service import handle
from tests.test_company_workspace import client as client_fixture
from tests.test_progressive_inbox import seeded

client = client_fixture


def company():
    return CompanyAgentConfig.model_validate_json(
        (Path(__file__).resolve().parents[1] / "config/arti_kasnak.production.json").read_text()
    )


async def test_entry_buttons_web_link_and_same_draft(client, monkeypatch):
    _, tid, _, _, cid, _ = await seeded(client, monkeypatch)
    config = company()
    async with session_scope(tid) as db:
        conv = await db.get(Conversation, cid)

        async def say(body):
            msg = Message(
                tenant_id=tid, conversation_id=cid, direction="inbound", body=body, raw={}
            )
            db.add(msg)
            await db.flush()
            return await handle(db, config, conv, msg)

        turn, _, row, _ = await say("Teklif oluşturmak istiyorum")
        assert [o.title for o in turn.interaction.options] == ["Formu doldur", "Sohbetle ilerle"]
        rid = row.id
        turn, _, row, _ = await say("Formu doldur [intake:form]")
        assert row.id == rid and turn.interaction.url.startswith(
            "https://api.ashiraai.com/tr/forms/selection#token="
        )
        token = turn.interaction.url.split("#token=")[1]
        await db.commit()
    r = await client.post(
        "/api/v1/customer-form",
        headers={"X-Form-Token": token},
        json={
            "revision": 0,
            "operation_id": str(uuid4()),
            "fields": {"contact_name": "Deniz Yılmaz"},
        },
    )
    assert r.status_code == 200, r.text
    async with session_scope(tid) as db:
        conv = await db.get(Conversation, cid)
        msg = Message(
            tenant_id=tid,
            conversation_id=cid,
            direction="inbound",
            body="Sohbetle ilerle [intake:chat]",
            raw={},
        )
        db.add(msg)
        await db.flush()
        turn, _, row, _ = await handle(db, config, conv, msg)
        assert row.id == rid and row.answers["contact_name"]["value"] == "Deniz Yılmaz"
        assert "isminizi" not in turn.reply
        msg = Message(
            tenant_id=tid, conversation_id=cid, direction="inbound", body="Yeni kasnak seç", raw={}
        )
        db.add(msg)
        await db.flush()
        _, _, row, _ = await handle(db, config, conv, msg)
        assert row.answers["intent"]["value"] == "new"
        await db.commit()
    updated = await client.get("/api/v1/customer-form", headers={"X-Form-Token": token})
    assert updated.status_code == 200
    assert updated.json()["answers"]["intent"]["value"] == "new"


async def test_form_is_private_idempotent_and_shares_chat_revision(client, monkeypatch):
    headers, tid, _, _, cid, _ = await seeded(client, monkeypatch)
    definition = {
        "id": "test",
        "version": 1,
        "title": "Test talebi",
        "start_phrases": ["teklif"],
        "greeting_phrases": [],
        "steps": [
            {"id": "contact_name", "label": "İsim", "question": "Adınız?", "kind": "text"},
            {
                "id": "quantity",
                "label": "Adet",
                "question": "Kaç adet?",
                "kind": "number",
                "integer": True,
            },
        ],
    }
    async with session_scope(tid) as db:
        row = SelectionRequest(
            tenant_id=tid,
            conversation_id=cid,
            definition=definition,
            answers={},
            revision=0,
            step_index=0,
            status="draft",
            internal_notes=[{"note": "private"}],
        )
        db.add(row)
        await db.flush()
        rid = row.id
        token = form_token(row)
        await db.commit()
    fh = {"X-Form-Token": token}
    assert (await client.get("/api/v1/customer-form")).status_code == 422
    assert (
        await client.get(
            "/api/v1/customer-form", headers={"X-Form-Token": headers["Authorization"].split()[1]}
        )
    ).status_code == 401
    settings = get_settings()
    claims = jwt.decode(token, settings.app_secret_key, algorithms=[settings.jwt_algorithm])
    for changed, status in [
        ({"exp": 1}, 401),
        ({"tid": str(uuid4())}, 404),
        ({"sub": str(uuid4())}, 404),
    ]:
        other = jwt.encode(
            {**claims, **changed}, settings.app_secret_key, algorithm=settings.jwt_algorithm
        )
        assert (
            await client.get("/api/v1/customer-form", headers={"X-Form-Token": other})
        ).status_code == status
    assert (
        await client.get("/api/v1/customer-form", headers={"X-Form-Token": "broken"})
    ).status_code == 401
    result = (await client.get("/api/v1/customer-form", headers=fh)).json()
    assert "internal_notes" not in result and "assigned_to" not in result
    body = {
        "revision": 0,
        "operation_id": str(uuid4()),
        "fields": {"contact_name": "Deniz", "quantity": "3"},
    }
    a = await client.post("/api/v1/customer-form", headers=fh, json=body)
    assert a.status_code == 200, a.text
    assert (await client.post("/api/v1/customer-form", headers=fh, json=body)).json() == a.json()
    stale = await client.post(
        "/api/v1/customer-form", headers=fh, json={**body, "operation_id": str(uuid4())}
    )
    assert stale.status_code == 409
    upload = {
        "revision": a.json()["revision"],
        "operation_id": str(uuid4()),
        "action": "upload",
        "file": {
            "name": "drawing.pdf",
            "mime": "application/pdf",
            "data": base64.b64encode(b"%PDF-1.7\nsynthetic test").decode(),
        },
    }
    u = await client.post("/api/v1/customer-form", headers=fh, json=upload)
    assert u.status_code == 200, u.text
    assert len(u.json()["files"]) == 1
    assert (await client.post("/api/v1/customer-form", headers=fh, json=upload)).json() == u.json()
    b = await client.post(
        "/api/v1/customer-form",
        headers=fh,
        json={"revision": u.json()["revision"], "operation_id": str(uuid4()), "action": "confirm"},
    )
    assert b.status_code == 200 and b.json()["status"] == "waiting_review", b.text
    async with session_scope(tid) as db:
        row = await db.get(SelectionRequest, rid)
        assert row.confirmed_snapshot["answers"]["quantity"]["value"] == "3"
        assert len(row.confirmed_snapshot["files"]) == 1
        assert row.assigned_to is not None
        assert row.internal_notes == [{"note": "private"}]
    assert (
        await client.post(
            "/api/v1/customer-form",
            headers=fh,
            json={
                "revision": b.json()["revision"],
                "operation_id": str(uuid4()),
                "fields": {"contact_name": "Başka"},
            },
        )
    ).status_code == 409
