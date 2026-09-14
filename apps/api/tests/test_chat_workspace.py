"""Chat tools against real PostgreSQL/RLS; only the language model is a fixture."""

import json
from uuid import uuid4

import pytest

from src.modules.admin_chat import planner
from src.modules.agents import workspace
from src.modules.auth.models import UserRole
from tests.test_company_workspace import account, config
from tests.test_company_workspace import client as client_fixture

client = client_fixture


async def chat(client, headers):
    r = await client.post("/api/v1/admin-chat/sessions", headers=headers, json={})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def turn(client, headers, sid, text, mid=None):
    return await client.post(
        f"/api/v1/admin-chat/sessions/{sid}/turns",
        headers=headers,
        json={"text": text, "client_message_id": str(mid or uuid4())},
    )


def model(monkeypatch, intent, builder_patch=None):
    class LLM:
        async def complete(self, messages, **kwargs):
            if "response_schema" in kwargs:
                return json.dumps(intent)
            return json.dumps({"reply": "Bilgiler önizlemeye hazır.", "patch": builder_patch or {}})

    monkeypatch.setattr(planner, "get_llm_client", lambda: LLM())
    monkeypatch.setattr(workspace, "get_llm_client", lambda: LLM())


async def agent(client, headers, name="Acme"):
    r = await client.post(
        "/api/v1/agents", headers=headers, json={"name": name, "slug": name.lower()}
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    versions = (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()
    return aid, versions[0]


async def prepare(client, headers, monkeypatch, sid, operation, message, **kwargs):
    model(monkeypatch, {"tool": "workspace", "operation": operation, **kwargs})
    r = await turn(client, headers, sid, message)
    assert r.status_code == 200, r.text
    return r.json()


async def test_company_and_invite_commit_with_private_turn_and_retry(client, monkeypatch):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    slug = "chat-" + uuid4().hex
    r = await prepare(
        client,
        headers,
        monkeypatch,
        sid,
        "create_company",
        f"North isimli {slug} kodlu şirketi owner@north.example sahibiyle oluştur",
        name="North",
        slug=slug,
        email="owner@north.example",
    )
    card = r["cards"][0]
    assert r["action_result"]["status"] == "preview"
    text = "action:workspace:confirm:" + card["operation_id"]
    mid = uuid4()
    applied = await turn(client, headers, sid, text, mid)
    assert applied.status_code == 200, applied.text
    assert applied.json()["cards"][0]["company"] == slug
    retry = await turn(client, headers, sid, text, mid)
    assert retry.json() == applied.json()
    history = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/messages", headers=headers)
    ).json()
    assert history[1]["cards"][0]["status"] == "applied"
    assert history[-1]["cards"][0]["token"]
    companies = (await client.get("/api/v1/platform/tenants", headers=headers)).json()
    assert len([c for c in companies if c["slug"] == slug]) == 1
    assert (await turn(client, headers, sid, text)).status_code == 409


async def test_chat_builder_accept_publish_and_stale_revision(client, monkeypatch):
    headers, _ = await account(client, UserRole.TENANT_OWNER)
    aid, version = await agent(client, headers)
    sid = await chat(client, headers)
    instruction = "Şirketimiz Acme. Bakım hizmeti veriyoruz."
    model(
        monkeypatch,
        {"tool": "workspace", "operation": "configure", "instruction": instruction},
        config(),
    )
    # builder patch only permits canonical editable fields.
    patch = config()
    patch.pop("lifecycle")
    model(
        monkeypatch,
        {"tool": "workspace", "operation": "configure", "instruction": instruction},
        patch,
    )
    r = await turn(client, headers, sid, instruction)
    assert r.status_code == 200, r.text
    preview = r.json()["cards"][0]
    versions = (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()
    assert versions[0]["revision"] == version["revision"]
    applied = await turn(
        client, headers, sid, "action:workspace:confirm:" + preview["operation_id"]
    )
    assert applied.status_code == 200, applied.text
    publication = await turn(client, headers, sid, "action:Taslağı yayınla")
    assert publication.status_code == 200, publication.text
    card = publication.json()["cards"][0]
    # Concurrent accepted edit must invalidate the pending publication.
    versions = (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()
    new = await client.post(
        f"/api/v1/agents/{aid}/proposals",
        headers=headers,
        json={"company_config": config("Changed"), "expected_revision": versions[0]["revision"]},
    )
    assert new.status_code == 200, new.text
    assert (
        await client.post(
            f"/api/v1/agents/{aid}/proposals/{new.json()['id']}/accept", headers=headers
        )
    ).status_code == 200
    assert (
        await turn(client, headers, sid, "action:workspace:confirm:" + card["operation_id"])
    ).status_code == 409
    refreshed = await turn(client, headers, sid, "action:Taslağı yayınla")
    final = await turn(
        client,
        headers,
        sid,
        "action:workspace:confirm:" + refreshed.json()["cards"][0]["operation_id"],
    )
    assert final.status_code == 200, final.text
    assert "yayınlandı" in final.json()["reply"]


async def test_workspace_role_boundary_and_cross_tenant_selection(client, monkeypatch):
    admin, _ = await account(client)
    foreign, _ = await agent(client, admin)
    for role in [UserRole.SALES_AGENT, UserRole.VIEWER]:
        headers, _ = await account(client, role)
        sid = await chat(client, headers)
        assert (await turn(client, headers, sid, "action:Şirketler")).status_code == 403
        assert (await turn(client, headers, sid, "action:Ekip ve yetkiler")).status_code == 403
        assert (
            await turn(client, headers, sid, "action:workspace:select_agent:" + foreign)
        ).status_code == 404
        assert (await turn(client, headers, sid, "action:Şirket bilgileri")).status_code == 200
        assert (await turn(client, headers, sid, "action:Taslağı yayınla")).status_code == 403


async def test_pending_token_is_private_and_not_replaced_by_read_tools(client, monkeypatch):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    other = await chat(client, headers)
    r = await prepare(
        client,
        headers,
        monkeypatch,
        sid,
        "invite",
        "viewer@example.com izleyici olarak davet et",
        email="viewer@example.com",
        role="viewer",
    )
    command = "action:workspace:confirm:" + r["cards"][0]["operation_id"]
    assert (await turn(client, headers, other, command)).status_code == 409
    assert (await turn(client, headers, sid, "action:Talepleri analiz et")).status_code == 200
    applied = await turn(client, headers, sid, command)
    assert applied.status_code == 200, applied.text
    assert applied.json()["cards"][0]["type"] == "invitation"


async def test_model_failure_rolls_back_builder_start_and_keeps_retryable_turn(client, monkeypatch):
    headers, _ = await account(client)
    aid, _ = await agent(client, headers)
    sid = await chat(client, headers)
    instruction = "Şirket bilgisine bakım hizmeti ekle"
    model(monkeypatch, {"tool": "workspace", "operation": "configure", "instruction": instruction})

    class Broken:
        async def complete(self, *args, **kwargs):
            return "invalid json"

    monkeypatch.setattr(workspace, "get_llm_client", lambda: Broken())
    r = await turn(client, headers, sid, instruction)
    assert r.status_code == 502, r.text
    assert (
        await client.get(f"/api/v1/agents/{aid}/builder/sessions", headers=headers)
    ).json() == []
    assert (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/messages", headers=headers)
    ).json() == []


async def test_missing_invite_role_uses_form_instead_of_inventing_permissions(client, monkeypatch):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    r = await prepare(
        client,
        headers,
        monkeypatch,
        sid,
        "invite",
        "test@example.com davet et",
        email="test@example.com",
    )
    assert r["cards"][0]["type"] == "workspace"
    assert r["cards"][0]["values"] == {"email": "test@example.com"}
    model(
        monkeypatch,
        {
            "tool": "workspace",
            "operation": "invite",
            "email": "test@example.com",
            "role": "tenant_owner",
        },
    )
    assert (await turn(client, headers, sid, "test@example.com davet et")).status_code == 502


@pytest.mark.parametrize(
    "message", ["Bunu uygulama", "Bu sürümü yayınlama", "Nasıl onayla diyeceğim?"]
)
async def test_model_cannot_convert_negative_or_howto_into_approval(monkeypatch, message):
    model(monkeypatch, {"tool": "workspace", "operation": "confirm"})
    intent, source = await planner.plan(message)
    assert intent.tool == "clarify"
    assert source == "model_rejected_write"


async def test_chat_customer_runtime_history_fallback_and_no_meta(client, monkeypatch):
    headers, _ = await account(client)
    aid, version = await agent(client, headers)
    p = await client.post(
        f"/api/v1/agents/{aid}/proposals",
        headers=headers,
        json={"company_config": config(), "expected_revision": version["revision"]},
    )
    assert (
        await client.post(
            f"/api/v1/agents/{aid}/proposals/{p.json()['id']}/accept", headers=headers
        )
    ).status_code == 200
    sid = await chat(client, headers)
    question = "Hangi hizmetleri sunuyorsunuz?"
    model(monkeypatch, {"tool": "workspace", "operation": "test", "instruction": question})
    calls = []

    class RuntimeModel:
        async def complete(self, messages, **kwargs):
            calls.append(messages)
            assert "Acme" in kwargs["system"]
            return json.dumps({"action": "reply", "fact_ids": ["service"]})

    monkeypatch.setattr(workspace, "get_llm_client", lambda: RuntimeModel())

    async def no_send(*args, **kwargs):
        pytest.fail("Customer tests must never send to Meta")

    monkeypatch.setattr("src.integrations.whatsapp.WhatsAppClient.send_text_once", no_send)
    mid = uuid4()
    r = await turn(client, headers, sid, question, mid)
    assert r.status_code == 200, r.text
    result = r.json()["cards"][0]["result"]
    assert result["reply"] == "Bakım hizmeti veriyoruz."
    assert result["response_source"] == "model"
    assert result["version_id"] == version["id"]
    assert len(calls) == 1
    assert (await turn(client, headers, sid, question, mid)).json() == r.json()
    assert len(calls) == 1
    r2 = await turn(client, headers, sid, question)
    assert r2.status_code == 200, r2.text
    saved = (await client.get(f"/api/v1/agents/{aid}/test-sessions", headers=headers)).json()
    assert len(saved) == 1 and len(saved[0]["messages"]) == 4

    class Broken:
        async def complete(self, *args, **kwargs):
            return "invalid json"

    monkeypatch.setattr(workspace, "get_llm_client", lambda: Broken())
    r3 = await turn(client, headers, sid, question)
    assert r3.status_code == 200, r3.text
    assert r3.json()["cards"][0]["result"]["response_source"] == "fallback"


async def test_new_preview_supersedes_old_and_agent_creation_is_idempotent(client, monkeypatch):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    first = await prepare(
        client,
        headers,
        monkeypatch,
        sid,
        "create_agent",
        "First first asistanı oluştur",
        name="First",
        slug="first",
    )
    second = await prepare(
        client,
        headers,
        monkeypatch,
        sid,
        "create_agent",
        "Second second asistanı oluştur",
        name="Second",
        slug="second",
    )
    assert (
        await turn(
            client, headers, sid, "action:workspace:confirm:" + first["cards"][0]["operation_id"]
        )
    ).status_code == 409
    text = "action:workspace:confirm:" + second["cards"][0]["operation_id"]
    mid = uuid4()
    r = await turn(client, headers, sid, text, mid)
    assert r.status_code == 200, r.text
    assert (await turn(client, headers, sid, text, mid)).json() == r.json()
    rows = (await client.get("/api/v1/agents", headers=headers)).json()
    assert [a["name"] for a in rows] == ["Second"]
