# ruff: noqa: RUF001
"""HTTP surface of the self-service knowledge API (Postgres-backed, no Celery)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.db import session_scope
from src.modules.auth.models import UserRole
from tests.test_company_workspace import account, client  # noqa: F401 - fixtures


async def _agent(client, headers) -> str:  # type: ignore[no-untyped-def]  # noqa: F811
    result = await client.post(
        "/api/v1/agents", json={"name": "Bilgi Asistanı", "slug": "bilgi-" + uuid4().hex[:8]}, headers=headers
    )
    assert result.status_code in {200, 201}, result.text
    return result.json()["id"]


@pytest.mark.asyncio
async def test_document_upload_source_listing_and_summary(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from src.modules.knowledge import router as knowledge_router

    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(knowledge_router, "_enqueue_sync", lambda tid, sid: queued.append((str(tid), str(sid))) or True)
    headers, _slug = await account(client, UserRole.TENANT_OWNER)
    agent_id = await _agent(client, headers)

    upload = await client.post(
        "/api/v1/knowledge/documents",
        data={"agent_id": agent_id, "auto_publish": "true", "sync_now": "true"},
        files={"file": ("katalog.md", "# Ürün\n\nDöküm kasnak GG-25 pik dökümden üretilir.\n".encode(), "text/markdown")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["kind"] == "document" and body["created"] is True and body["queued"] is True
    assert len(queued) == 1

    duplicate = await client.post(
        "/api/v1/knowledge/documents",
        data={"agent_id": agent_id},
        files={"file": ("katalog.md", "# Ürün\n\nDöküm kasnak GG-25 pik dökümden üretilir.\n".encode(), "text/markdown")},
        headers=headers,
    )
    assert duplicate.status_code == 201 and duplicate.json()["created"] is False
    assert len(queued) == 1  # a re-upload of identical bytes is not re-queued

    rejected = await client.post(
        "/api/v1/knowledge/documents",
        data={"agent_id": agent_id},
        files={"file": ("virus.exe", b"MZ\x90\x00binary", "application/octet-stream")},
        headers=headers,
    )
    assert rejected.status_code == 422

    website = await client.post(
        "/api/v1/knowledge/sources",
        json={"agent_id": agent_id, "url": "https://www.example-kasnak.test/?utm_source=x", "sync_now": False},
        headers=headers,
    )
    assert website.status_code == 201, website.text
    assert website.json()["canonical_uri"] == "https://www.example-kasnak.test/"
    private = await client.post(
        "/api/v1/knowledge/sources",
        json={"agent_id": agent_id, "url": "http://localhost:8000/admin", "sync_now": False},
        headers=headers,
    )
    assert private.status_code == 422

    listing = await client.get(f"/api/v1/knowledge/sources?agent_id={agent_id}", headers=headers)
    assert listing.status_code == 200 and {s["kind"] for s in listing.json()} == {"document", "website"}

    detail = await client.get(f"/api/v1/knowledge/sources/{body['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["documents"][0]["filename"] == "katalog.md"
    assert "content" not in detail.json()["documents"][0]

    summary = await client.get(f"/api/v1/knowledge/agents/{agent_id}/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["sources"] == 2 and summary.json()["documents"] == 1

    # Another tenant cannot see or touch these sources (RLS + tenant checks).
    other_headers, _ = await account(client, UserRole.TENANT_OWNER)
    foreign = await client.get(f"/api/v1/knowledge/sources/{body['id']}", headers=other_headers)
    assert foreign.status_code == 404
    foreign_delete = await client.delete(f"/api/v1/knowledge/sources/{body['id']}", headers=other_headers)
    assert foreign_delete.status_code == 404

    # Unknown published image → 404 on the public media route.
    async with session_scope():
        pass
    public = await client.get(f"/media/k/{uuid4().hex}/{'a' * 64}.jpg")
    assert public.status_code == 404

    deleted = await client.delete(f"/api/v1/knowledge/sources/{body['id']}", headers=headers)
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    remaining = await client.get(f"/api/v1/knowledge/sources?agent_id={agent_id}", headers=headers)
    assert [s["kind"] for s in remaining.json()] == ["website"]


@pytest.mark.asyncio
async def test_viewer_cannot_register_sources(client) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    headers, _ = await account(client, UserRole.VIEWER)
    result = await client.post(
        "/api/v1/knowledge/sources",
        json={"agent_id": str(uuid4()), "url": "https://www.example.test"},
        headers=headers,
    )
    assert result.status_code == 403
