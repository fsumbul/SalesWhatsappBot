"""Current HTTP routing: old model vocabulary must never open old workspace panels."""

import pytest

from src.modules.auth.models import UserRole
from tests.test_chat_workspace import chat, model, turn
from tests.test_company_workspace import account
from tests.test_company_workspace import client as client_fixture
from tests.test_progressive_workflows import act

client = client_fixture


@pytest.mark.parametrize(
    "operation,kind,category",
    [
        ("agents", "records", "agents"),
        ("knowledge", "records", "knowledge"),
        ("versions", "records", "versions"),
        ("team", "records", "team"),
        ("inbox", "records", "inbox"),
        ("platform", "records", "companies"),
        ("invite", "invite", None),
        ("create_company", "create_company", None),
        ("create_agent", "create_agent", None),
        ("configure", "configure", None),
        ("test", "test", None),
        ("publish", "publish", None),
    ],
)
async def test_legacy_model_vocabulary_opens_shared_workflow(
    client, monkeypatch, operation, kind, category
):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    model(monkeypatch, {"tool": "workspace", "operation": operation})
    response = await turn(client, headers, sid, "İlgili işlemi aç")
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    row = response.json()["workflows"][-1]
    assert row["kind"] == kind and row["anchor_sequence"] == 1
    if category:
        assert row["fields"]["category"] == category
    else:
        assert row["status"] == "awaiting_input"
    if operation == "invite":
        assert "role" not in row["fields"]


async def test_natural_agent_creation_requires_review_and_receipt(client, monkeypatch):
    from uuid import uuid4

    headers, _ = await account(client, UserRole.TENANT_OWNER)
    sid = await chat(client, headers)
    model(
        monkeypatch,
        {"tool": "workspace", "operation": "create_agent", "name": "Acme", "slug": "acme"},
    )
    mid = uuid4()
    response = await turn(client, headers, sid, "Acme acme asistanı oluştur", mid)
    assert response.status_code == 200, response.text
    assert (
        await turn(client, headers, sid, "Acme acme asistanı oluştur", mid)
    ).json() == response.json()
    assert (await client.get("/api/v1/agents", headers=headers)).json() == []
    row = response.json()["workflows"][0]
    row = (await act(client, headers, row, "continue")).json()
    saved = await act(client, headers, row, "complete")
    assert saved.status_code == 200, saved.text
    assert saved.json()["result"]["outcome"] == "agent_created"
    assert len((await client.get("/api/v1/agents", headers=headers)).json()) == 1


async def test_translation_keeps_role_and_grounding_guards(client, monkeypatch):
    headers, _ = await account(client, UserRole.SALES_AGENT)
    sid = await chat(client, headers)
    model(
        monkeypatch,
        {"tool": "workspace", "operation": "invite", "email": "test@example.com", "role": "viewer"},
    )
    assert (
        await turn(client, headers, sid, "test@example.com izleyici davet et")
    ).status_code == 403
    owner, _ = await account(client, UserRole.TENANT_OWNER)
    sid = await chat(client, owner)
    model(
        monkeypatch,
        {
            "tool": "workspace",
            "operation": "invite",
            "email": "test@example.com",
            "role": "tenant_owner",
        },
    )
    assert (await turn(client, owner, sid, "test@example.com davet et")).status_code == 502
    model(
        monkeypatch,
        {"tool": "workspace", "operation": "create_agent", "name": "Invented", "slug": "invented"},
    )
    assert (await turn(client, owner, sid, "asistan oluştur")).status_code == 502


async def test_text_format_is_semantic_but_content_must_stay_literal(monkeypatch):
    from src.modules.admin_chat.planner import plan

    text = "Şirket bilgisine bakım hizmeti sunduğumuzu ekle"
    intent = {
        "tool": "workflow",
        "workflow_kind": "configure",
        "workflow_action": "start",
        "workflow_fields": {"format": "text", "content": "bakım hizmeti sunduğumuzu"},
    }
    model(monkeypatch, intent)
    selected, _ = await plan(text)
    assert selected.workflow_fields["format"] == "text"
    intent["workflow_fields"]["content"] = "Bir günde teslimat garantisi"
    model(monkeypatch, intent)
    with pytest.raises(ValueError, match="Ungrounded workflow field"):
        await plan(text)
    intent["workflow_fields"] = {"format": "unsupported"}
    model(monkeypatch, intent)
    with pytest.raises(ValueError, match="Unsupported workflow format"):
        await plan(text)


async def test_natural_outreach_uses_shared_review_queue_and_status(client, monkeypatch):
    from uuid import uuid4

    from tests.test_chat_outbound import setup
    from tests.test_progressive_workflows import start

    headers, _, _, _, sid = await setup(client, monkeypatch)
    model(monkeypatch, {"tool": "outreach", "recipients": ["+15550102030"], "purpose": "tanıtım"})
    mid = uuid4()
    response = await turn(client, headers, sid, "+15550102030 tanıtım gönder", mid)
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    row = response.json()["workflows"][-1]
    assert row["kind"] == "outreach" and row["step"] == "details"
    assert row["fields"]["recipients"] == "+15550102030"
    assert (
        await turn(client, headers, sid, "+15550102030 tanıtım gönder", mid)
    ).json() == response.json()
    model(monkeypatch, {"tool": "send_outreach"})
    assert (await turn(client, headers, sid, "Hazırladığın tanıtımı gönder")).status_code == 409
    row = (await act(client, headers, row, "continue")).json()
    row = (
        await act(
            client,
            headers,
            row,
            "continue",
            {"var_1": "Deniz", "consent_evidence": "Web formu tanıtım izni 2026-09-14"},
        )
    ).json()
    assert row["status"] == "ready", row
    model(monkeypatch, {"tool": "send_outreach"})
    response = await turn(client, headers, sid, "Hazırladığın tanıtımı gönder")
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    queued = response.json()["workflows"][-1]
    assert queued["result"]["outcome"] == "queued"
    model(monkeypatch, {"tool": "outreach_status"})
    response = await turn(client, headers, sid, "Gönderim durumu")
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    assert response.json()["workflows"][-1]["revision"] == queued["revision"]
    # An unrelated active form must not receive the cancellation.
    await start(client, headers, sid, "contact", {"name": "Deniz"})
    model(monkeypatch, {"tool": "cancel_outreach"})
    response = await turn(client, headers, sid, "Gönderimi iptal et")
    assert response.status_code == 200, response.text
    rows = response.json()["workflows"]
    assert rows[0]["status"] == "cancelled"
    assert rows[1]["status"] == "awaiting_input"


@pytest.mark.parametrize("kind", ["invite", "contact", "publish"])
async def test_send_word_does_not_approve_unrelated_workflow(monkeypatch, kind):
    from src.modules.admin_chat.planner import plan

    model(monkeypatch, {"tool": "workflow", "workflow_kind": kind, "workflow_action": "complete"})
    intent, source = await plan("Bunu gönder")
    assert intent.tool == "clarify" and source == "model_rejected_write"


@pytest.mark.parametrize(
    "intent",
    [
        {"tool": "send_outreach"},
        {"tool": "workflow", "workflow_kind": "outreach", "workflow_action": "complete"},
    ],
)
async def test_new_phone_cannot_confirm_an_existing_recipient_review(monkeypatch, intent):
    from src.modules.admin_chat.planner import plan

    model(monkeypatch, intent)
    result, source = await plan("+15550109999 numarasına gönder")
    assert result.tool == "clarify" and source == "model_rejected_write"


@pytest.mark.parametrize(
    "selected",
    [
        {"tool": "workspace", "operation": "confirm"},
        {"tool": "workflow", "workflow_action": "complete"},
    ],
)
async def test_generic_approval_cannot_choose_legacy_or_current_card(client, monkeypatch, selected):
    from uuid import uuid4

    from tests.test_chat_workspace import prepare
    from tests.test_progressive_workflows import start

    headers, _ = await account(client, UserRole.TENANT_OWNER)
    sid = await chat(client, headers)
    old = await prepare(
        client,
        headers,
        monkeypatch,
        sid,
        "invite",
        "legacy@example.com izleyici davet et",
        email="legacy@example.com",
        role="viewer",
    )
    old_id = old["cards"][0]["operation_id"]
    response = await start(
        client, headers, sid, "contact", {"name": "Deniz", "email": "deniz@example.com"}
    )
    current = (await act(client, headers, response.json(), "continue")).json()
    model(monkeypatch, selected)
    mid = uuid4()
    response = await turn(client, headers, sid, "Onayla", mid)
    assert response.status_code == 200, response.text
    assert "Birden fazla onay hedefi" in response.json()["reply"]
    assert response.json()["workflows"][-1] == current
    assert (await turn(client, headers, sid, "Onayla", mid)).json() == response.json()
    # Both explicitly selected buttons still work; neither preview was consumed.
    response = await act(client, headers, current, "complete")
    assert response.status_code == 200, response.text
    assert response.json()["result"]["outcome"] == "created"
    response = await turn(client, headers, sid, "action:workspace:confirm:" + old_id)
    assert response.status_code == 200, response.text
    assert response.json()["action_result"]["status"] == "applied"


@pytest.mark.parametrize(
    "message,blocked",
    [
        ("Bunu onayla", True),
        ("Değişikliği kaydet", True),
        ("Kişiyi kaydet", False),
        ("Taslağı yayınla", False),
        ("Onaylama", False),
        ("action:workspace:confirm:server-token", False),
    ],
)
async def test_approval_guard_only_handles_ambiguous_natural_phrases(message, blocked):
    from src.modules.admin_chat.planner import Intent
    from src.modules.admin_chat.workflow_intents import ambiguous_approval

    intent = Intent(tool="workflow", workflow_action="complete")
    views = [{"status": "ready"}]
    assert ambiguous_approval(message, True, views, intent) is blocked
    assert not ambiguous_approval(message, False, views, intent)
    assert not ambiguous_approval(message, True, [{"status": "completed"}], intent)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Geri almayı onayla", "workflow"),
        ("Geri alma", "clarify"),
        ("Geri almayın", "clarify"),
        ("Geri almak istemiyorum", "clarify"),
    ],
)
async def test_rollback_approval_is_distinct_from_negation(monkeypatch, message, expected):
    from src.modules.admin_chat.planner import plan

    model(
        monkeypatch,
        {"tool": "workflow", "workflow_kind": "rollback", "workflow_action": "complete"},
    )
    intent, _ = await plan(message)
    assert intent.tool == expected
