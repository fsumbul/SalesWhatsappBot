"""Template drafts are reviewed, submitted once and gated on Meta approval."""

import json
from uuid import uuid4

import httpx
import pytest

from src.modules.admin_chat import outbound, planner, workflow_templates
from src.modules.auth.models import UserRole
from tests.test_chat_outbound import setup
from tests.test_company_workspace import client as client_fixture
from tests.test_progressive_workflows import act, start

client = client_fixture
FIELDS = {
    "name": "kasnak_bilgilendirme",
    "body": "Merhaba {{1}}, ürünlerimizi inceleyebilirsiniz.",
    "header": "Artı Kasnak",
    "footer": "Bilgi için bize yazın",
    "buttons": "Bilgi al\nİlgilenmiyorum",
    "example_1": "Deniz",
}


async def ready(client, monkeypatch):
    headers, tid, _, _, sid = await setup(client, monkeypatch, UserRole.SALES_MANAGER)
    row = (await start(client, headers, sid, "create_template", FIELDS)).json()
    assert (
        row["output"]["template_preview"]["body"]
        == "Merhaba Deniz, ürünlerimizi inceleyebilirsiniz."
    )
    row = (await act(client, headers, row, "continue")).json()
    assert row["status"] == "ready", row
    return headers, tid, sid, row


async def test_review_submit_once_and_approval_status(client, monkeypatch):
    headers, tid, sid, row = await ready(client, monkeypatch)
    calls = []

    async def create(self, waba, payload):
        calls.append(payload)
        # Another HTTP request can read the committed submission before Meta returns.
        saved = (
            await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
        ).json()
        assert saved[-1]["result"]["outcome"] == "submitting"
        return {"id": "987654", "status": "PENDING", "category": "MARKETING"}

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.create_template_once", create)
    body = {
        "client_operation_id": str(uuid4()),
        "expected_revision": row["revision"],
        "action": "complete",
    }
    url = f"/api/v1/admin-chat/sessions/{sid}/workflows/{row['id']}/actions"
    first = await client.post(url, headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert (await client.post(url, headers=headers, json=body)).json() == first.json()
    assert len(calls) == 1
    assert calls[0]["components"][1] == {
        "type": "BODY",
        "text": FIELDS["body"],
        "example": {"body_text": [["Deniz"]]},
    }
    saved = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()[-1]
    assert saved["result"]["meta_status"] == "PENDING"
    assert saved["status"] == "running"
    changed = await act(client, headers, saved, "update", {"body": "different"})
    assert changed.status_code == 409
    # Meta review can continue while a new independent workflow is started.
    assert (
        await start(client, headers, sid, "outreach", {"recipients": "+15550102030"})
    ).status_code == 200

    async def approved(sender):
        return [{"id": "987654", "status": "APPROVED", **calls[0]}]

    monkeypatch.setattr(workflow_templates, "catalog", approved)
    now = workflow_templates.time.time()
    monkeypatch.setattr(workflow_templates.time, "time", lambda: now + 60)
    saved = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()[0]
    assert saved["result"]["meta_status"] == "APPROVED"
    assert saved["status"] == "completed"


async def test_ambiguous_submission_never_posts_twice(client, monkeypatch):
    headers, _, sid, row = await ready(client, monkeypatch)
    calls = []

    async def timeout(self, waba, payload):
        calls.append(payload)
        raise httpx.ReadTimeout("ambiguous")

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.create_template_once", timeout)
    body = {
        "client_operation_id": str(uuid4()),
        "expected_revision": row["revision"],
        "action": "complete",
    }
    url = f"/api/v1/admin-chat/sessions/{sid}/workflows/{row['id']}/actions"
    assert (await client.post(url, headers=headers, json=body)).status_code == 200
    assert (await client.post(url, headers=headers, json=body)).status_code == 200
    saved = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()[-1]
    assert saved["result"]["outcome"] == "ambiguous"
    assert (await act(client, headers, saved, "complete")).status_code == 409
    assert len(calls) == 1


async def test_only_approved_options_and_immutable_preview(client, monkeypatch):
    headers, _, _, _, sid = await setup(client, monkeypatch)
    row = (await start(client, headers, sid, "outreach", {"recipients": "+15550102030"})).json()
    control = next(c for c in row["controls"] if c["key"] == "template")
    assert control["control"] == "select" and control["options"] == {"123": "intro · tr"}
    assert not {"body", "header", "footer", "buttons"} & {c["key"] for c in row["controls"]}
    assert row["output"]["template_preview"]["body"]
    assert (await act(client, headers, row, "update", {"body": "changed"})).status_code == 422
    launched = await act(client, headers, row, "launch", {"operation": "create_template"})
    assert launched.status_code == 200, launched.text
    rows = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()
    assert rows[0]["status"] == "paused" and rows[1]["kind"] == "create_template"


async def test_creation_requires_manager_and_full_review(client, monkeypatch):
    headers, _, _, _, sid = await setup(client, monkeypatch, UserRole.SALES_AGENT)
    assert (await start(client, headers, sid, "create_template", FIELDS)).status_code == 403
    headers, _, _, _, sid = await setup(client, monkeypatch, UserRole.SALES_MANAGER)
    row = (await start(client, headers, sid, "create_template", {"name": "new_one"})).json()
    assert (await act(client, headers, row, "complete")).status_code == 409
    row = (await act(client, headers, row, "continue")).json()
    assert row["status"] == "awaiting_input" and "body" in row["errors"]


@pytest.mark.parametrize("text", ["başka şablon ekle", "yeni mesaj şablonu oluştur"])
async def test_model_template_creation_opens_separate_workflow(monkeypatch, text):
    class Model:
        async def complete(self, *args, **kwargs):
            return json.dumps(
                {"tool": "workflow", "workflow_kind": "create_template", "workflow_action": "start"}
            )

    monkeypatch.setattr(planner, "get_llm_client", lambda *_args, **_kwargs: Model())
    intent, source = await planner.plan(text)
    assert intent.workflow_kind == "create_template" and source == "model"


@pytest.mark.parametrize("status", ["PENDING", "REJECTED", "PAUSED", "DISABLED"])
async def test_unapproved_template_never_enters_send_options(monkeypatch, status):
    from types import SimpleNamespace

    async def read(*args, **kwargs):
        return {
            "data": [
                {
                    "id": "1",
                    "name": "new_one",
                    "language": "tr",
                    "status": status,
                    "category": "MARKETING",
                    "components": [{"type": "BODY", "text": "Merhaba"}],
                }
            ]
        }

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.business_read", read)
    assert (
        await outbound.meta_templates(SimpleNamespace(phone_number_id="1", business_account_id="2"))
        == []
    )


async def test_template_choice_is_grounded_in_meta_options(monkeypatch):
    class Model:
        async def complete(self, *args, **kwargs):
            return json.dumps(
                {
                    "tool": "workflow",
                    "workflow_kind": "outreach",
                    "workflow_action": "update",
                    "workflow_fields": {"template": "123"},
                }
            )

    monkeypatch.setattr(planner, "get_llm_client", lambda *_args, **_kwargs: Model())
    intent, _ = await planner.plan(
        "ilk şablonu seç",
        workflow_context=[{"kind": "outreach", "template_choices": {"123": "intro · tr"}}],
    )
    assert intent.workflow_fields["template"] == "123"
    with pytest.raises(ValueError):
        await planner.plan(
            "ilk şablonu seç",
            workflow_context=[{"kind": "outreach", "template_choices": {"999": "other · tr"}}],
        )


@pytest.mark.parametrize("body", ["Merhaba {{0}}", "Merhaba {{2}}", "Merhaba {{name}}"])
def test_invalid_variable_contract_never_reaches_meta(body):
    assert "body" in workflow_templates.errors({**FIELDS, "body": body})


async def test_meta_create_uses_reviewed_payload_once(monkeypatch):
    from src.integrations.whatsapp import WhatsAppClient

    original = httpx.AsyncClient
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "123", "status": "PENDING"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    data = workflow_templates.payload(
        {**FIELDS, "language": "tr", "template_category": "MARKETING"}
    )
    result = await WhatsAppClient(
        access_token="test-only", graph_api_version="v26.0"
    ).create_template_once("999", data)
    assert result["status"] == "PENDING" and len(calls) == 1
    assert calls[0].url.path == "/v26.0/999/message_templates"
    assert calls[0].method == "POST" and json.loads(calls[0].content) == data


async def test_template_validation_failure_cannot_submit(client, monkeypatch):
    headers, _, _, _, sid = await setup(client, monkeypatch)
    row = (
        await start(
            client, headers, sid, "create_template", {"name": "new_one", "body": "Hello {{2}}"}
        )
    ).json()
    row = (await act(client, headers, row, "continue")).json()
    assert row["status"] == "awaiting_input"
    assert (await act(client, headers, row, "complete")).status_code == 409


async def test_approved_flow_and_utility_templates_are_selectable(monkeypatch):
    from types import SimpleNamespace

    async def read(*args, **kwargs):
        return {
            "data": [
                {
                    "id": "1",
                    "name": "form",
                    "language": "tr",
                    "status": "APPROVED",
                    "category": "MARKETING",
                    "components": [
                        {"type": "BODY", "text": "Formu doldurun"},
                        {
                            "type": "BUTTONS",
                            "buttons": [{"type": "FLOW", "text": "Formu aç", "flow_id": "123"}],
                        },
                    ],
                },
                {
                    "id": "2",
                    "name": "hello",
                    "language": "en_US",
                    "status": "APPROVED",
                    "category": "UTILITY",
                    "components": [{"type": "BODY", "text": "Hello"}],
                },
            ]
        }

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.business_read", read)
    templates = await outbound.meta_templates(
        SimpleNamespace(phone_number_id="1", business_account_id="2")
    )
    assert [t["id"] for t in templates] == ["1", "2"]
    components = outbound.template_components(
        SimpleNamespace(template=templates[0], variables={}), "recipient-id"
    )
    assert components == [
        {
            "type": "button",
            "sub_type": "flow",
            "index": "0",
            "parameters": [
                {"type": "action", "action": {"flow_token": "chat-outbound:recipient-id"}}
            ],
        }
    ]


async def test_existing_send_card_loads_meta_choices_after_upgrade(client, monkeypatch):
    from sqlalchemy import text, update

    from src.core.db import session_scope
    from src.modules.admin_chat.workflow_models import Workflow

    headers, tid, uid, _, sid = await setup(client, monkeypatch)
    row = (await start(client, headers, sid, "outreach", {"recipients": "+15550102030"})).json()
    async with session_scope(tid) as db:
        await db.execute(
            text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(uid)}
        )
        await db.execute(update(Workflow).where(Workflow.id == row["id"]).values(state={}))
        await db.commit()
    row = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()[-1]
    options = next(c["options"] for c in row["controls"] if c["key"] == "template")
    assert options == {"123": "intro · tr"}
