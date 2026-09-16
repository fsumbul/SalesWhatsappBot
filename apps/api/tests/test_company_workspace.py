"""HTTP acceptance tests using real PostgreSQL and a restricted RLS role."""

import json
from uuid import uuid4

import httpx
import pytest_asyncio
from sqlalchemy import select

from src.core.config import get_settings
from src.core.db import dispose_engine, session_scope
from src.modules.agents.workspace import ImportIn, import_config
from src.modules.auth.models import Tenant, TenantStatus, UserRole
from src.modules.auth.schemas import TenantRegisterIn
from src.modules.auth.service import AuthService
from tests.conftest import TEST_DATABASE_URL


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_DEBUG", "false")
    get_settings.cache_clear()
    await dispose_engine()
    from src.main import create_app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield c
    await dispose_engine()
    get_settings.cache_clear()


async def account(client, role=UserRole.SUPER_ADMIN):
    slug = "workspace-" + uuid4().hex
    async with session_scope() as db:
        _, user = await AuthService(db).register_tenant(
            TenantRegisterIn(
                tenant_name="Platform",
                tenant_slug=slug,
                admin_email="owner@example.com",
                admin_password="Test-only-password-123",
            )
        )
        user.role = role
        await db.commit()
    result = await client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": slug,
            "email": "owner@example.com",
            "password": "Test-only-password-123",
        },
    )
    assert result.status_code == 200, result.text
    return {"Authorization": "Bearer " + result.json()["access_token"]}, slug


def config(name="Acme", fact="Bakım hizmeti veriyoruz."):
    return {
        "lifecycle": "draft",
        "organization": {"id": "company", "display_names": {"tr": name}},
        "agent": {"purposes": ["information"], "supported_locales": ["tr"], "default_locale": "tr"},
        "facts": [
            {
                "id": "service",
                "subject_id": "company",
                "category": "capability",
                "value": fact,
                "customer_visible": True,
                "customer_text": {"tr": fact},
                "source": "owner",
            }
        ],
    }


async def company(client, admin, label):
    slug = label.lower() + "-" + uuid4().hex
    r = await client.post(
        "/api/v1/platform/tenants",
        headers=admin,
        json={"name": label, "slug": slug, "owner_email": "owner@example.com"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["invitation_token"]
    r = await client.post(
        "/api/v1/auth/accept-invite", json={"token": token, "password": "Test-only-password-123"}
    )
    assert r.status_code == 201, r.text
    assert (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": token, "password": "Test-only-password-123"},
        )
    ).status_code == 422
    r = await client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": slug,
            "email": "owner@example.com",
            "password": "Test-only-password-123",
        },
    )
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}, slug, r.json()


async def test_two_company_workflow_isolation_and_revision(client, monkeypatch):
    admin, _ = await account(client)
    a, _, _ = await company(client, admin, "Metal")
    b, _, _ = await company(client, admin, "Consulting")
    agents = []
    for headers, name, fact in [
        (a, "Metal", "Metal parçalar üretiyoruz."),
        (b, "Consulting", "Danışmanlık hizmeti veriyoruz."),
    ]:
        r = await client.post(
            "/api/v1/agents", headers=headers, json={"name": name, "slug": "assistant"}
        )
        assert r.status_code == 201, r.text
        aid = r.json()["id"]
        agents.append(aid)
        v = (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()[0]
        payload = {"company_config": config(name, fact), "expected_revision": v["revision"]}
        p = await client.post(f"/api/v1/agents/{aid}/proposals", headers=headers, json=payload)
        assert p.status_code == 200, p.text
        stale = await client.post(f"/api/v1/agents/{aid}/proposals", headers=headers, json=payload)
        assert (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()[0][
            "company_config"
        ]["organization"] is None
        pid = p.json()["id"]
        assert (
            await client.post(
                f"/api/v1/agents/{aid}/proposals/{pid}/accept", headers=headers, json={}
            )
        ).status_code == 200
        assert (
            await client.post(
                f"/api/v1/agents/{aid}/proposals/{stale.json()['id']}/accept",
                headers=headers,
                json={},
            )
        ).status_code == 409
        v = (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()[0]
        expected_name = name

        class LLM:
            async def complete(self, *args, expected_name=expected_name, **kwargs):
                assert "service" in kwargs["system"]
                assert expected_name in kwargs["system"]
                return json.dumps({"action": "reply", "fact_ids": ["service"]})

        monkeypatch.setattr("src.modules.agents.workspace.get_llm_client", lambda *_args, **_kwargs: LLM())
        test = await client.post(
            f"/api/v1/agents/{aid}/test-sessions", headers=headers, json={"version_id": v["id"]}
        )
        assert test.status_code == 200, test.text
        tid = test.json()["id"]
        r = await client.post(
            f"/api/v1/agents/{aid}/test-sessions/{tid}/messages",
            headers=headers,
            json={"text": "Hangi hizmetleri sunuyorsunuz?"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["response_source"] == "model", r.text
        assert r.json()["reply"] == fact
        assert (
            len(
                (await client.get(f"/api/v1/agents/{aid}/test-sessions", headers=headers)).json()[
                    0
                ]["messages"]
            )
            == 2
        )
        r = await client.post(
            f"/api/v1/agents/{aid}/versions/{v['id']}/promote-to-live", headers=headers, json={}
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "live"
    assert (await client.get(f"/api/v1/agents/{agents[0]}/versions", headers=b)).status_code == 404
    assert (await client.get(f"/api/v1/agents/{agents[0]}/proposals", headers=b)).status_code == 404
    assert (
        await client.get(f"/api/v1/agents/{agents[0]}/test-sessions", headers=b)
    ).status_code == 404
    assert (await client.get("/api/v1/senders/connection", headers=b)).json()["connected"] is False
    assert (await client.get("/api/v1/platform/tenants", headers=b)).status_code == 403


async def test_auth_roles_revocation_and_closed_registration(client):
    admin, _ = await account(client)
    headers, slug, tokens = await company(client, admin, "Owner")
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    uid = me["user"]["id"]
    for patch, code in [
        ({"role": "super_admin"}, 403),
        ({"role": "viewer"}, 409),
        ({"is_active": False}, 409),
    ]:
        assert (
            await client.patch(f"/api/v1/users/{uid}", headers=headers, json=patch)
        ).status_code == code
    assert (
        await client.post(
            "/api/v1/auth/invite",
            headers=headers,
            json={"email": "bad@example.com", "role": "super_admin"},
        )
    ).status_code == 422
    assert (await client.post("/api/v1/auth/register-tenant", json={})).status_code == 401
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200, r.text
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401
    async with session_scope() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one()
        tenant.status = TenantStatus.SUSPENDED
        await db.commit()
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
    ).status_code == 401


async def test_builder_stages_canonical_config_without_saving(client, monkeypatch):
    headers, _ = await account(client, UserRole.TENANT_OWNER)
    aid = (
        await client.post(
            "/api/v1/agents", headers=headers, json={"name": "Builder", "slug": "builder"}
        )
    ).json()["id"]
    session = (
        await client.post(f"/api/v1/agents/{aid}/builder/sessions", headers=headers, json={})
    ).json()

    class LLM:
        async def complete(self, *args, **kwargs):
            return json.dumps(
                {
                    "reply": "Öneriyi inceleyebilirsiniz.",
                    "patch": {
                        "organization": {"id": "company", "display_names": {"tr": "Yeni şirket"}}
                    },
                }
            )

    monkeypatch.setattr("src.modules.agents.workspace.get_llm_client", lambda *_args, **_kwargs: LLM())
    r = await client.post(
        f"/api/v1/agents/{aid}/builder/sessions/{session['id']}/messages",
        headers=headers,
        json={"text": "Şirketimizin adı Yeni şirket"},
    )
    assert r.status_code == 200, r.text
    assert (
        r.json()["proposal"]["company_config"]["organization"]["display_names"]["tr"]
        == "Yeni şirket"
    )
    assert (await client.get(f"/api/v1/agents/{aid}/versions", headers=headers)).json()[0][
        "company_config"
    ]["organization"] is None
    assert (
        len(
            (await client.get(f"/api/v1/agents/{aid}/builder/sessions", headers=headers)).json()[0][
                "messages"
            ]
        )
        == 2
    )


def test_csv_mapping_and_atomic_invalid_import():
    base = config()
    result, errors = import_config(
        ImportIn(
            format="csv",
            expected_revision=0,
            columns={"id": "code"},
            content="code,subject_id,category,customer_text,source,search_terms\nnew,company,capability,Onarım yapıyoruz.,owner,onarım\n",
        ),
        base,
    )
    assert not errors
    assert len(result["facts"]) == 2
    result, errors = import_config(
        ImportIn(
            format="csv",
            expected_revision=0,
            content="id,subject_id,category,customer_text,source\nnew,company,capability,OK,owner\nbad,company,invalid,Wrong,owner\n",
        ),
        base,
    )
    assert result == {} and errors[0]["row"] == 3
    assert len(base["facts"]) == 1


async def test_operational_chat_uses_model_is_private_and_idempotent(client, monkeypatch):
    from src.integrations.llm import LLMCompletionError
    from src.modules.admin_chat.planner import Intent

    a, slug = await account(client, UserRole.TENANT_OWNER)
    b, _ = await account(client, UserRole.TENANT_OWNER)
    sid = (await client.post("/api/v1/admin-chat/sessions", headers=a, json={})).json()["id"]
    assert (
        await client.get(f"/api/v1/admin-chat/sessions/{sid}/messages", headers=b)
    ).status_code == 404
    invite = await client.post("/api/v1/auth/invite", headers=a, json={"email":"colleague@example.com","role":"sales_manager"})
    assert invite.status_code == 201
    assert (await client.post("/api/v1/auth/accept-invite",json={"token":invite.json()["token"],"password":"Test-colleague-password-123"})).status_code == 201
    colleague = await client.post("/api/v1/auth/login",json={"tenant_slug":slug,"email":"colleague@example.com","password":"Test-colleague-password-123"})
    colleague_headers = {"Authorization":"Bearer " + colleague.json()["access_token"]}
    assert (await client.get(f"/api/v1/admin-chat/sessions/{sid}/messages",headers=colleague_headers)).status_code == 404
    assert (await client.get("/api/v1/admin-chat/sessions",headers=colleague_headers)).json() == []
    calls = []

    class LLM:
        async def complete(self, messages, **kwargs):
            calls.append(messages[0].content)
            return Intent(tool="analytics").model_dump_json()

    monkeypatch.setattr("src.modules.admin_chat.planner.get_llm_client", lambda *_args, **_kwargs: LLM())
    payload = {"text": "Genel durum", "client_message_id": str(uuid4())}
    url = f"/api/v1/admin-chat/sessions/{sid}/turns"
    first = await client.post(url, headers=a, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["response_source"] == "model"
    retry = await client.post(url, headers=a, json=payload)
    assert retry.json() == first.json()
    assert calls == ["Genel durum"]
    changed = await client.post(url, headers=a, json={**payload, "text": "Başka işlem"})
    assert changed.status_code == 409
    history = await client.get(f"/api/v1/admin-chat/sessions/{sid}/messages", headers=a)
    assert len(history.json()) == 2

    class Unavailable:
        async def complete(self, *args, **kwargs):
            raise LLMCompletionError("test outage")

    monkeypatch.setattr("src.modules.admin_chat.planner.get_llm_client", lambda *_args, **_kwargs: Unavailable())
    fail = await client.post(
        url, headers=a, json={"text": "Talepleri analiz et", "client_message_id": str(uuid4())}
    )
    assert fail.status_code == 503
    guided = await client.post(
        url,
        headers=a,
        json={"text": "action:Talepleri analiz et", "client_message_id": str(uuid4())},
    )
    assert guided.status_code == 200 and guided.json()["response_source"] == "guided"


async def test_server_bootstrap_invitation_creates_platform_role(client, capsys):
    from urllib.parse import parse_qs, urlsplit

    from scripts.bootstrap_platform_owner import bootstrap
    slug = "platform-" + uuid4().hex
    await bootstrap(slug, "bootstrap@example.com", "https://test", None)
    link = capsys.readouterr().out.strip()
    token = parse_qs(urlsplit(link).query)["invite"][0]
    result = await client.post("/api/v1/auth/accept-invite",json={"token":token,"password":"Bootstrap-test-password-123"})
    assert result.status_code == 201, result.text
    assert result.json()["role"] == "super_admin"
    login = await client.post("/api/v1/auth/login",json={"tenant_slug":slug,"email":"bootstrap@example.com","password":"Bootstrap-test-password-123"})
    headers = {"Authorization":"Bearer " + login.json()["access_token"]}
    assert (await client.get("/api/v1/platform/tenants",headers=headers)).status_code == 200
