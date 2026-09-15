"""HTTP/RLS acceptance for the private outreach file-import slice."""

import json
from uuid import UUID, uuid4

from sqlalchemy import func, select

from src.core.db import session_scope
from src.modules.admin_chat import campaign_imports
from src.modules.auth.models import UserRole
from tests.test_chat_outbound import setup
from tests.test_company_workspace import account
from tests.test_company_workspace import client as client_fixture
from tests.test_progressive_workflows import start

client = client_fixture


class InMemoryPrivateStorage:
    """The API/worker contract is tested without a browser-visible object URL."""

    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}

    async def put_bytes(self, object_key: str, data: bytes, mime_type: str) -> None:
        self.items[object_key] = data

    async def get_bytes(self, object_key: str) -> bytes:
        return self.items[object_key]

    async def delete(self, object_key: str) -> None:
        self.items.pop(object_key, None)


async def test_manager_file_import_is_idempotent_masked_and_tenant_scoped(client, monkeypatch):
    from src.workers import campaign_imports as import_worker

    headers, tenant_id, _user_id, _sender_id, session_id = await setup(client, monkeypatch)
    workflow = (
        await start(
            client,
            headers,
            session_id,
            "outreach",
            {"recipient_source": "file", "country_code": "TR"},
        )
    ).json()
    storage = InMemoryPrivateStorage()
    queued: list[str] = []
    monkeypatch.setattr(campaign_imports, "campaign_imports_enabled_for", lambda *_args: True)
    monkeypatch.setattr(campaign_imports, "get_campaign_import_storage", lambda: storage)
    monkeypatch.setattr(import_worker, "get_campaign_import_storage", lambda: storage)
    monkeypatch.setattr(
        campaign_imports,
        "queue_import",
        lambda imported, *_args, **_kwargs: queued.append(str(imported.id if hasattr(imported, "id") else imported)),
    )

    operation = uuid4()
    path = (
        f"/api/v1/admin-chat/sessions/{session_id}/workflows/{workflow['id']}/imports"
    )
    payload = b"Telefon\n+905551234567\n+905551234567\nnot-a-phone\n"
    request = {
        "files": {"file": ("recipients.csv", payload, "text/csv")},
        "data": {
            "country_code": "TR",
            "client_operation_id": str(operation),
            "expected_revision": str(workflow["revision"]),
        },
    }
    first = await client.post(path, headers=headers, **request)
    assert first.status_code == 202, first.text
    import_id = first.json()["import"]["id"]
    repeated = await client.post(path, headers=headers, **request)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["import"]["id"] == import_id
    # Re-enqueueing a still-queued idempotent operation is intentional: it
    # recovers the narrow commit-to-broker failure window without a second DB
    # import, and the worker's row lock makes the duplicate task a no-op.
    assert queued == [import_id, import_id]

    async with session_scope(tenant_id) as db:
        count = await db.scalar(
            select(func.count()).select_from(campaign_imports.CampaignImport).where(
                campaign_imports.CampaignImport.workflow_id == UUID(workflow["id"])
            )
        )
        assert count == 1

    parsed = await import_worker._parse_campaign_import(tenant_id, UUID(import_id))
    assert parsed["status"] == "ready"
    status = await client.get(f"/api/v1/admin-chat/imports/{import_id}", headers=headers)
    assert status.status_code == 200, status.text
    view = status.json()
    assert view["counts"] == {
        "total": 3,
        "eligible": 1,
        "invalid": 1,
        "duplicate": 1,
        "blocked": 0,
    }
    # This public endpoint and the workflow state must never expose source
    # E.164 values. Invalid text is masked too, not echoed back.
    assert "+905551234567" not in json.dumps(view)
    assert "not-a-phone" not in json.dumps(view)

    refreshed = await client.get(
        f"/api/v1/admin-chat/sessions/{session_id}/workflows", headers=headers
    )
    assert refreshed.status_code == 200, refreshed.text
    card = next(item for item in refreshed.json() if item["id"] == workflow["id"])
    assert card["output"]["campaign_import"]["status"] == "ready"
    assert "+905551234567" not in json.dumps(card)

    other_headers, _ = await account(client, UserRole.TENANT_OWNER)
    assert (
        await client.get(f"/api/v1/admin-chat/imports/{import_id}", headers=other_headers)
    ).status_code == 404
    agent_headers, _ = await account(client, UserRole.SALES_AGENT)
    assert (await client.post(path, headers=agent_headers, **request)).status_code == 403


async def test_manager_selected_phone_column_is_queued_after_workflow_commit(client, monkeypatch):
    from src.workers import campaign_imports as import_worker

    headers, tenant_id, _user_id, _sender_id, session_id = await setup(client, monkeypatch)
    workflow = (
        await start(
            client,
            headers,
            session_id,
            "outreach",
            {"recipient_source": "file", "country_code": "TR"},
        )
    ).json()
    storage = InMemoryPrivateStorage()
    queued: list[str] = []
    monkeypatch.setattr(campaign_imports, "campaign_imports_enabled_for", lambda *_args: True)
    monkeypatch.setattr(campaign_imports, "get_campaign_import_storage", lambda: storage)
    monkeypatch.setattr(import_worker, "get_campaign_import_storage", lambda: storage)
    monkeypatch.setattr(
        campaign_imports,
        "queue_import",
        lambda imported, *_args, **_kwargs: queued.append(str(imported.id if hasattr(imported, "id") else imported)),
    )

    upload = await client.post(
        f"/api/v1/admin-chat/sessions/{session_id}/workflows/{workflow['id']}/imports",
        headers=headers,
        files={
            "file": (
                "ambiguous.csv",
                b"Telefon,WhatsApp\n+905551234567,+905551234567\n",
                "text/csv",
            )
        },
        data={
            "country_code": "TR",
            "client_operation_id": str(uuid4()),
            "expected_revision": str(workflow["revision"]),
        },
    )
    assert upload.status_code == 202, upload.text
    import_id = UUID(upload.json()["import"]["id"])
    assert await import_worker._parse_campaign_import(tenant_id, import_id) == {
        "ok": True,
        "status": "awaiting_mapping",
        "columns": ["Telefon", "WhatsApp"],
    }
    queued.clear()

    cards = await client.get(f"/api/v1/admin-chat/sessions/{session_id}/workflows", headers=headers)
    assert cards.status_code == 200, cards.text
    card = next(item for item in cards.json() if item["id"] == workflow["id"])
    selected = await client.post(
        f"/api/v1/admin-chat/sessions/{session_id}/workflows/{workflow['id']}/actions",
        headers=headers,
        json={
            "action": "continue",
            "fields": {"phone_column": "Telefon"},
            "expected_revision": card["revision"],
            "client_operation_id": str(uuid4()),
        },
    )
    assert selected.status_code == 200, selected.text
    assert queued == [str(import_id)]
