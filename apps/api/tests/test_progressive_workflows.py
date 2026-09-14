"""Real PostgreSQL/RLS acceptance of shared durable workflow actions."""

from uuid import uuid4

from src.modules.auth.models import UserRole
from tests.test_chat_workspace import chat
from tests.test_company_workspace import account
from tests.test_company_workspace import client as client_fixture

client = client_fixture


async def start(client, headers, sid, kind="contact", fields=None, mid=None):
    return await client.post(
        f"/api/v1/admin-chat/sessions/{sid}/workflows",
        headers=headers,
        json={"kind": kind, "fields": fields or {}, "client_operation_id": str(mid or uuid4())},
    )


async def act(client, headers, row, action, fields=None, mid=None):
    return await client.post(
        f"/api/v1/admin-chat/sessions/{row['session_id']}/workflows/{row['id']}/actions",
        headers=headers,
        json={
            "action": action,
            "fields": fields or {},
            "expected_revision": row["revision"],
            "client_operation_id": str(mid or uuid4()),
        },
    )


async def test_contact_idempotency_duplicate_and_stale_card(client):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    fields = {"name": "Ayşe Yılmaz", "email": "ayse@example.com"}
    mid = uuid4()
    r = await start(client, headers, sid, fields=fields, mid=mid)
    assert r.status_code == 200, r.text
    row = r.json()
    assert (await start(client, headers, sid, fields=fields, mid=mid)).json() == row
    reviewed = (await act(client, headers, row, "continue")).json()
    assert reviewed["status"] == "ready"
    assert (await act(client, headers, row, "update", {"name": "Other"})).status_code == 409
    mid = uuid4()
    saved = await act(client, headers, reviewed, "complete", mid=mid)
    assert saved.status_code == 200, saved.text
    assert saved.json()["result"]["outcome"] == "created"
    assert (await act(client, headers, reviewed, "complete", mid=mid)).json() == saved.json()
    assert (await act(client, headers, saved.json(), "complete")).status_code == 409
    duplicate = (await start(client, headers, sid, fields=fields)).json()
    duplicate = (await act(client, headers, duplicate, "continue")).json()
    duplicate = (await act(client, headers, duplicate, "complete")).json()
    assert duplicate["result"]["outcome"] == "duplicate"
    assert duplicate["result"]["lead_ids"] == saved.json()["result"]["lead_id"]
    assert duplicate["records"][0]["title"] == "Ayşe Yılmaz"
    assert "ayse@example.com" in duplicate["records"][0]["details"].values()
    assert duplicate["records"][0]["actions"] == []


async def test_pause_resume_refresh_and_isolation(client):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    first = (await start(client, headers, sid, fields={"name": "Yarım kişi"})).json()
    second = (await start(client, headers, sid, "invite")).json()
    path = f"/api/v1/admin-chat/sessions/{sid}/workflows"
    rows = (await client.get(path, headers=headers)).json()
    assert rows[0]["status"] == "paused"
    assert rows[0]["fields"]["name"] == "Yarım kişi"
    assert (await act(client, headers, first, "resume")).status_code == 409
    resumed = await act(client, headers, rows[0], "resume")
    assert resumed.status_code == 200, resumed.text
    rows = (await client.get(path, headers=headers)).json()
    assert rows[1]["id"] == second["id"] and rows[1]["status"] == "paused"
    other, _ = await account(client)
    assert (await client.get(path, headers=other)).status_code == 404
    assert (await act(client, other, resumed.json(), "cancel")).status_code == 404


async def test_validation_back_and_invitation_semantics(client):
    headers, _ = await account(client, UserRole.TENANT_OWNER)
    sid = await chat(client, headers)
    row = (await start(client, headers, sid, "person")).json()
    row = (await act(client, headers, row, "continue", {"person_type": "invite"})).json()
    assert row["kind"] == "invite"
    row = (
        await act(client, headers, row, "continue", {"email": "broken", "role": "super_admin"})
    ).json()
    assert row["status"] == "awaiting_input" and set(row["errors"]) == {"email", "role"}
    row = (
        await act(client, headers, row, "continue", {"email": "new@example.com", "role": "viewer"})
    ).json()
    assert row["status"] == "ready"
    row = (await act(client, headers, row, "back")).json()
    assert row["fields"]["email"] == "new@example.com"
    assert (await act(client, headers, row, "complete")).status_code == 409
    row = (await act(client, headers, row, "continue")).json()
    r = await act(client, headers, row, "complete")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["outcome"] == "invitation_ready"
    assert r.json()["result"]["token"]
    viewer, _ = await account(client, UserRole.VIEWER)
    sid = await chat(client, viewer)
    assert (await start(client, viewer, sid)).status_code == 403


async def test_chat_form_mixed_and_model_failure_preserves_fields(client, monkeypatch):
    from src.integrations.llm import LLMCompletionError
    from src.modules.admin_chat import planner
    from tests.test_chat_workspace import model, turn

    headers, _ = await account(client)
    sid = await chat(client, headers)
    model(
        monkeypatch,
        {
            "tool": "workflow",
            "workflow_action": "start",
            "workflow_kind": "contact",
            "workflow_fields": {"name": "Deniz"},
        },
    )
    r = await turn(client, headers, sid, "Deniz adlı müşteri ekle")
    assert r.status_code == 200, r.text
    row = r.json()["workflows"][0]
    assert row["fields"] == {"name": "Deniz"}
    row = (await act(client, headers, row, "update", {"email": "deniz@example.com"})).json()

    class Broken:
        async def complete(self, *args, **kwargs):
            raise LLMCompletionError("offline")

    monkeypatch.setattr(planner, "get_llm_client", lambda: Broken())
    assert (await turn(client, headers, sid, "Devam et")).status_code == 503
    rows = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()
    assert rows[0] == row
    model(monkeypatch, {"tool": "workflow", "workflow_action": "continue"})
    r = await turn(client, headers, sid, "Devam et")
    assert r.status_code == 200, r.text
    assert r.json()["workflows"][0]["status"] == "ready"
    model(monkeypatch, {"tool": "workflow", "workflow_action": "complete"})
    r = await turn(client, headers, sid, "Kişiyi kaydet")
    assert r.status_code == 200, r.text
    assert r.json()["workflows"][0]["result"]["outcome"] == "created"


async def test_concurrent_complete_and_invitation_role_revocation(client):
    import asyncio

    from sqlalchemy import select

    from src.core.db import session_scope
    from src.modules.auth.models import Tenant, User

    headers, slug = await account(client, UserRole.TENANT_OWNER)
    sid = await chat(client, headers)
    row = (
        await start(client, headers, sid, fields={"name": "Concurrent", "phone": "+905551234567"})
    ).json()
    row = (await act(client, headers, row, "continue")).json()
    responses = await asyncio.gather(
        act(client, headers, row, "complete"), act(client, headers, row, "complete")
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    row = (
        await start(client, headers, sid, "invite", {"email": "new@example.com", "role": "viewer"})
    ).json()
    row = (await act(client, headers, row, "continue")).json()
    async with session_scope() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.slug == slug))
        from src.core.db import set_tenant_context

        await set_tenant_context(db, tenant.id)
        user = await db.scalar(select(User).where(User.tenant_id == tenant.id))
        user.role = UserRole.SALES_AGENT
        await db.commit()
    assert (await act(client, headers, row, "complete")).status_code == 403


async def test_progressive_company_agent_and_pause_keeps_fields(client):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    row = (await start(client, headers, sid, "create_company", {"name": "Yeni Şirket"})).json()
    row = (
        await act(
            client,
            headers,
            row,
            "pause",
            {"slug": "progressive-" + uuid4().hex, "email": "owner@new.example"},
        )
    ).json()
    assert row["status"] == "paused" and row["fields"]["email"] == "owner@new.example"
    row = (await act(client, headers, row, "resume")).json()
    row = (await act(client, headers, row, "continue")).json()
    mid = uuid4()
    r = await act(client, headers, row, "complete", mid=mid)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["outcome"] == "company_created"
    assert (await act(client, headers, row, "complete", mid=mid)).json() == r.json()
    row = (
        await start(
            client,
            headers,
            sid,
            "create_agent",
            {"name": "Yeni Asistan", "slug": "assistant-" + uuid4().hex},
        )
    ).json()
    row = (await act(client, headers, row, "continue")).json()
    r = await act(client, headers, row, "complete")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["outcome"] == "agent_created"
    assert (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).status_code == 200
    owner, _ = await account(client, UserRole.TENANT_OWNER)
    sid = await chat(client, owner)
    assert (await start(client, owner, sid, "create_company")).status_code == 403


async def test_configuration_import_repreview_and_publish_revision(client):
    import json

    from tests.test_chat_workspace import agent
    from tests.test_company_workspace import config

    headers, _ = await account(client, UserRole.TENANT_OWNER)
    aid, _ = await agent(client, headers)
    sid = await chat(client, headers)
    row = (
        await start(
            client,
            headers,
            sid,
            "configure",
            {"format": "json", "content": json.dumps(config("Acme"))},
        )
    ).json()
    assert row["fields"]["agent"] == "acme"
    row = (await act(client, headers, row, "continue")).json()
    assert row["status"] == "ready", row
    assert row["changes"] and row["primary_label"] == "Taslağa kaydet"
    row = (await act(client, headers, row, "back")).json()
    row = (
        await act(client, headers, row, "continue", {"content": json.dumps(config("Changed"))})
    ).json()
    assert row["status"] == "ready", row
    result = await act(client, headers, row, "complete")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["outcome"] == "draft_saved"
    version = (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()[0]
    assert version["company_config"]["organization"]["display_names"]["tr"] == "Changed"
    publication = (await start(client, headers, sid, "publish")).json()
    publication = (await act(client, headers, publication, "continue")).json()
    assert publication["status"] == "ready", publication
    proposal = await client.post(
        f"/api/v1/agents/{aid}/proposals",
        headers=headers,
        json={"company_config": config("Newer"), "expected_revision": version["revision"]},
    )
    assert proposal.status_code == 200, proposal.text
    assert (
        await client.post(
            f"/api/v1/agents/{aid}/proposals/{proposal.json()['id']}/accept", headers=headers
        )
    ).status_code == 200
    assert (await act(client, headers, publication, "complete")).status_code == 409
    publication = (await act(client, headers, publication, "back")).json()
    publication = (await act(client, headers, publication, "continue")).json()
    published = await act(client, headers, publication, "complete")
    assert published.status_code == 200, published.text
    assert published.json()["result"]["outcome"] == "published"


async def test_workflow_model_outage_and_invalid_import_keep_input(client, monkeypatch):
    from src.integrations.llm import LLMCompletionError
    from src.modules.agents import workspace
    from tests.test_chat_workspace import agent

    class Offline:
        async def complete(self, *args, **kwargs):
            raise LLMCompletionError("offline")

    monkeypatch.setattr(workspace, "get_llm_client", lambda: Offline())
    headers, _ = await account(client, UserRole.TENANT_OWNER)
    await agent(client, headers)
    sid = await chat(client, headers)
    row = (await start(client, headers, sid, "configure")).json()
    r = await act(
        client, headers, row, "continue", {"content": "Şirketimiz bakım hizmeti veriyor."}
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert (
        row["status"] == "failed"
        and row["fields"]["content"] == "Şirketimiz bakım hizmeti veriyor."
    )
    persisted = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()[0]
    assert persisted == row
    row = (
        await act(client, headers, row, "continue", {"format": "json", "content": "{invalid json"})
    ).json()
    assert row["status"] == "awaiting_input" and row["errors"]["content"]
    assert row["fields"]["content"] == "{invalid json"


async def test_progressive_test_result_and_stale_version(client, monkeypatch):
    import json

    from src.modules.agents import workspace
    from tests.test_chat_workspace import agent
    from tests.test_company_workspace import config

    class Model:
        async def complete(self, *args, **kwargs):
            return json.dumps({"action": "reply", "fact_ids": ["service"]})

    monkeypatch.setattr(workspace, "get_llm_client", lambda: Model())
    headers, _ = await account(client, UserRole.TENANT_OWNER)
    aid, version = await agent(client, headers)
    proposal = (
        await client.post(
            f"/api/v1/agents/{aid}/proposals",
            headers=headers,
            json={"company_config": config(), "expected_revision": version["revision"]},
        )
    ).json()
    assert (
        await client.post(
            f"/api/v1/agents/{aid}/proposals/{proposal['id']}/accept", headers=headers
        )
    ).status_code == 200
    sid = await chat(client, headers)
    row = (
        await start(client, headers, sid, "test", {"content": "Hangi hizmetleri sunuyorsunuz?"})
    ).json()
    assert row["fields"]["version"]
    _, foreign_version = await agent(client, headers, "Another")
    assert (
        await act(client, headers, row, "continue", {"version": foreign_version["id"]})
    ).status_code == 422
    row = (await act(client, headers, row, "continue")).json()
    assert row["status"] == "ready", row
    mid = uuid4()
    r = await act(client, headers, row, "complete", mid=mid)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed", r.json()
    assert r.json()["output"]["response_source"] == "model"
    assert r.json()["output"]["fact_ids"] == ["service"]
    assert (await act(client, headers, row, "complete", mid=mid)).json() == r.json()


async def test_record_search_navigation_and_isolation(client):
    from tests.test_chat_workspace import agent

    headers, _ = await account(client, UserRole.TENANT_OWNER)
    aid, _ = await agent(client, headers, "Metal")
    await agent(client, headers, "Consulting")
    sid = await chat(client, headers)
    row = (await start(client, headers, sid, "records", {"category": "agents"})).json()
    assert len(row["records"]) == 2
    row = (await act(client, headers, row, "continue", {"q": "Metal"})).json()
    assert [r["id"] for r in row["records"]] == [aid]
    mid = uuid4()
    launched = await act(
        client, headers, row, "launch", {"operation": "configure", "record": aid}, mid=mid
    )
    assert launched.status_code == 200, launched.text
    assert (
        await act(
            client, headers, row, "launch", {"operation": "configure", "record": aid}, mid=mid
        )
    ).json() == launched.json()
    rows = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()
    assert len(rows) == 2 and rows[0]["status"] == "paused"
    assert rows[1]["kind"] == "configure" and rows[1]["fields"]["agent"] == "metal"
    foreign, _ = await account(client)
    foreign_aid, _ = await agent(client, foreign, "Foreign")
    row = (await act(client, headers, rows[0], "resume")).json()
    assert (
        await act(client, headers, row, "launch", {"operation": "test", "record": foreign_aid})
    ).status_code == 404
    viewer, _ = await account(client, UserRole.VIEWER)
    sid = await chat(client, viewer)
    assert (await start(client, viewer, sid, "records", {"category": "team"})).status_code == 403
    row = (await start(client, viewer, sid, "records", {"category": "agents"})).json()
    assert not row["record_actions"]
    assert (
        await act(client, viewer, row, "launch", {"operation": "create_agent"})
    ).status_code == 422


async def test_contact_records_show_person_and_communication_separately(client):
    headers, _ = await account(client)
    sid = await chat(client, headers)
    row = (
        await start(
            client,
            headers,
            sid,
            "contact",
            {"name": "Ayşe", "company": "Metal Ltd", "email": "ayse@example.com"},
        )
    ).json()
    row = (await act(client, headers, row, "continue")).json()
    assert (await act(client, headers, row, "complete")).status_code == 200
    row = (
        await start(
            client, headers, sid, "records", {"category": "contacts", "q": "ayse@example.com"}
        )
    ).json()
    assert len(row["records"]) == 1
    record = row["records"][0]
    assert record["title"] == "Ayşe" and record["subtitle"] == "Metal Ltd"
    assert "ayse@example.com" in record["details"].values()


async def test_member_workflow_protects_last_owner_and_rejects_stale_access(client):
    from sqlalchemy import select

    from src.core.db import session_scope, set_tenant_context
    from src.modules.auth.models import Tenant, User

    headers, slug = await account(client, UserRole.TENANT_OWNER)
    sid = await chat(client, headers)
    row = (await start(client, headers, sid, "member", {"member": "owner@example.com"})).json()
    row = (await act(client, headers, row, "continue", {"role": "viewer"})).json()
    assert row["changes"][0]["after"] == "İzleyici"
    assert (await act(client, headers, row, "complete")).status_code == 409
    row = (await act(client, headers, row, "back")).json()
    row = (
        await act(client, headers, row, "continue", {"role": "tenant_owner", "active": "true"})
    ).json()
    assert (await act(client, headers, row, "complete")).status_code == 200
    async with session_scope() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.slug == slug))
        await set_tenant_context(db, tenant.id)
        # A second active owner allows changing the first owner's role.
        original = await db.scalar(select(User).where(User.tenant_id == tenant.id))
        db.add(
            User(
                tenant_id=tenant.id,
                email="second@example.com",
                password_hash=original.password_hash,
                role=UserRole.TENANT_OWNER,
                is_active=True,
            )
        )
        await db.commit()
    row = (await start(client, headers, sid, "member", {"member": "second@example.com"})).json()
    row = (await act(client, headers, row, "continue", {"role": "sales_agent"})).json()
    async with session_scope() as db:
        await set_tenant_context(db, tenant.id)
        second = await db.scalar(
            select(User).where(User.tenant_id == tenant.id, User.email == "second@example.com")
        )
        second.role = UserRole.SALES_MANAGER
        await db.commit()
    assert (await act(client, headers, row, "complete")).status_code == 409
    row = (await act(client, headers, row, "back")).json()
    row = (await act(client, headers, row, "continue")).json()
    r = await act(client, headers, row, "complete")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["outcome"] == "member_updated"


async def test_workflow_turn_anchor_survives_actions_and_child_launch(client):
    from tests.test_chat_workspace import turn

    headers, _ = await account(client)
    sid = await chat(client, headers)
    initial = (await start(client, headers, sid)).json()
    assert initial["anchor_sequence"] == 0
    mid = uuid4()
    first = await turn(client, headers, sid, "action:Asistanlar", mid)
    assert first.status_code == 200, first.text
    assert first.json()["workflows"][-1]["anchor_sequence"] == 1
    assert (await turn(client, headers, sid, "action:Asistanlar", mid)).json() == first.json()
    second = await turn(client, headers, sid, "action:Müşteri ekle")
    assert second.status_code == 200, second.text
    assert second.json()["workflows"][-1]["anchor_sequence"] == 2
    rows = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()
    listing = next(row for row in rows if row["kind"] == "records")
    launched = await act(client, headers, listing, "launch", {"operation": "create_agent"})
    assert launched.status_code == 200, launched.text
    rows = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()
    child = rows[-1]
    assert child["kind"] == "create_agent" and child["anchor_sequence"] == 1
    changed = (await act(client, headers, child, "update", {"name": "Draft"})).json()
    assert changed["anchor_sequence"] == 1
    messages = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/messages", headers=headers)
    ).json()
    assert [(message["role"], message["sequence"]) for message in messages] == [
        ("user", 1),
        ("assistant", 1),
        ("user", 2),
        ("assistant", 2),
    ]


async def test_csv_mapping_uses_real_headers_and_persists_selected_columns(client):
    import json

    from tests.test_chat_workspace import agent
    from tests.test_company_workspace import config

    headers, _ = await account(client, UserRole.TENANT_OWNER)
    aid, _ = await agent(client, headers)
    sid = await chat(client, headers)
    row = (
        await start(
            client, headers, sid, "configure", {"format": "json", "content": json.dumps(config())}
        )
    ).json()
    row = (await act(client, headers, row, "continue")).json()
    assert (await act(client, headers, row, "complete")).status_code == 200
    csv_content = "Kimlik,Konu,Tür,Açıklama,Kaynak\nnewfact,company,capability,Yeni bilgi,owner\n"
    row = (
        await start(client, headers, sid, "configure", {"format": "csv", "content": csv_content})
    ).json()
    controls = {control["key"]: control for control in row["controls"]}
    assert "columns" not in controls
    assert set(controls["column_customer_text"]["options"]) == {
        "Kimlik",
        "Konu",
        "Tür",
        "Açıklama",
        "Kaynak",
    }
    mappings = {
        "column_id": "Kimlik",
        "column_subject_id": "Konu",
        "column_category": "Tür",
        "column_customer_text": "Açıklama",
        "column_source": "Kaynak",
    }
    row = (await act(client, headers, row, "update", mappings)).json()
    refreshed = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()[-1]
    assert all(refreshed["fields"][key] == value for key, value in mappings.items())
    row = (await act(client, headers, row, "continue")).json()
    assert row["status"] == "ready", row
    assert (await act(client, headers, row, "complete")).status_code == 200
    version = (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()[0]
    assert (
        next(f for f in version["company_config"]["facts"] if f["id"] == "newfact")[
            "customer_text"
        ]["tr"]
        == "Yeni bilgi"
    )


def test_csv_duplicate_headers_are_rejected():
    from src.modules.agents.workspace import ImportIn, import_config
    from tests.test_company_workspace import config

    _, errors = import_config(
        ImportIn(
            format="csv",
            content="id,id,subject_id,category,customer_text,source\na,b,company,capability,text,owner",
            expected_revision=0,
        ),
        config(),
    )
    assert errors and errors[0]["row"] == 1


async def test_rollback_clones_history_rejects_live_drift_and_retries_once(client):
    import json

    from tests.test_chat_workspace import agent
    from tests.test_company_workspace import config

    headers, _ = await account(client, UserRole.TENANT_OWNER)
    aid, _ = await agent(client, headers)
    sid = await chat(client, headers)
    row = (
        await start(
            client,
            headers,
            sid,
            "configure",
            {"format": "json", "content": json.dumps(config("Acme"))},
        )
    ).json()
    row = (await act(client, headers, row, "continue")).json()
    assert (await act(client, headers, row, "complete")).status_code == 200
    row = (await start(client, headers, sid, "publish")).json()
    row = (await act(client, headers, row, "continue")).json()
    published = (await act(client, headers, row, "complete")).json()
    target = published["result"]["version_id"]
    listing = (
        await start(client, headers, sid, "records", {"category": "versions", "agent": "acme"})
    ).json()
    launched = await act(
        client, headers, listing, "launch", {"operation": "rollback", "record": target}
    )
    assert launched.status_code == 200, launched.text
    rows = (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/workflows", headers=headers)
    ).json()
    row = rows[-1]
    assert row["kind"] == "rollback"
    row = (await act(client, headers, row, "continue")).json()
    assert row["status"] == "ready", row
    assert "yeni bir LIVE" in row["output"]["summary"]
    # A publication through the canonical endpoint invalidates the old preview.
    external = await client.post(
        f"/api/v1/agents/{aid}/versions/{target}/rollback", headers=headers
    )
    assert external.status_code == 200, external.text
    assert (await act(client, headers, row, "complete")).status_code == 409
    row = (await act(client, headers, row, "back")).json()
    row = (await act(client, headers, row, "continue")).json()
    mid = uuid4()
    restored = await act(client, headers, row, "complete", mid=mid)
    assert restored.status_code == 200, restored.text
    assert restored.json()["result"]["outcome"] == "rolled_back"
    assert (await act(client, headers, row, "complete", mid=mid)).json() == restored.json()
    versions = (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()
    assert len(versions) == 3
    assert sum(v["status"] == "live" for v in versions) == 1
    assert next(v for v in versions if v["id"] == target)["status"] == "archived"
    assert versions[0]["id"] != target and versions[0]["rolled_back_from_version"] == 1


async def test_platform_owner_reinvite_receipt_and_private_context(client):
    from uuid import UUID

    from sqlalchemy import select

    from src.core.db import session_scope
    from src.modules.auth.models import Invitation

    headers, _ = await account(client)
    other, slug = await account(client, UserRole.TENANT_OWNER)
    sid = await chat(client, headers)
    companies = (await client.get("/api/v1/platform/tenants", headers=headers)).json()
    company = next(t for t in companies if t["slug"] == slug)
    listing = (
        await start(client, headers, sid, "records", {"category": "companies", "q": slug})
    ).json()
    launched = await act(
        client, headers, listing, "launch", {"operation": "owner_invite", "record": company["id"]}
    )
    assert launched.status_code == 200, launched.text
    url = f"/api/v1/admin-chat/sessions/{sid}/workflows"
    row = (await client.get(url, headers=headers)).json()[-1]
    assert row["kind"] == "owner_invite" and row["fields"]["tenant"] == company["id"]
    row = (await act(client, headers, row, "continue", {"email": "new-owner@example.com"})).json()
    assert row["status"] == "ready" and "E-posta gönderilmez" in row["output"]["summary"]
    mid = uuid4()
    first = await act(client, headers, row, "complete", mid=mid)
    assert first.status_code == 200, first.text
    assert first.json()["result"]["outcome"] == "invitation_ready"
    assert (await act(client, headers, row, "complete", mid=mid)).json() == first.json()
    assert (await client.get(url, headers=headers)).json()[-1]["id"] == row["id"]
    assert (await client.get(url, headers=other)).status_code == 404
    # A deliberate new invitation gets a new token; retrying the old action did not.
    row = (
        await start(
            client,
            headers,
            sid,
            "owner_invite",
            {"tenant": company["id"], "email": "new-owner@example.com"},
        )
    ).json()
    row = (await act(client, headers, row, "continue")).json()
    second = await act(client, headers, row, "complete")
    assert second.status_code == 200, second.text
    assert second.json()["result"]["token"] != first.json()["result"]["token"]
    async with session_scope(UUID(company["id"])) as db:
        invitations = list(
            await db.scalars(
                select(Invitation).where(
                    Invitation.tenant_id == UUID(company["id"]),
                    Invitation.email == "new-owner@example.com",
                )
            )
        )
        assert len(invitations) == 2
        assert all(i.role == UserRole.TENANT_OWNER and i.accepted_at is None for i in invitations)
    other_sid = await chat(client, other)
    assert (
        await start(client, other, other_sid, "owner_invite", {"tenant": company["id"]})
    ).status_code == 403
