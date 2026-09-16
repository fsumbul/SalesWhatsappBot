# ruff: noqa: RUF001
"""Postgres-backed integration coverage for the production WhatsApp runtime.

These tests intentionally exercise the real RLS policies, durable runtime-job
state machine, and pooled application session factory.  The only mocked
boundaries are Celery delivery, the local LLM, and Meta's external HTTP call.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from scripts.bootstrap_arti_kasnak_agent import _reconcile
from src.core.config import get_settings
from src.core.db import dispose_engine, get_sessionmaker, session_scope, set_tenant_context
from src.integrations.whatsapp import WhatsAppClient
from src.modules.agents.company_config import CompanyAgentConfig, MediaAsset
from src.modules.agents.company_runtime import (
    CustomerReplyAction,
    RuntimeInteraction,
    RuntimeInteractionKind,
    RuntimeTurn,
)
from src.modules.agents.models import Agent, AgentVersion, AgentVersionStatus
from src.modules.agents.runtime_models import AgentRuntimeJob, AgentRuntimeJobStatus
from src.modules.auth.models import Tenant, TenantStatus, User, UserRole
from src.modules.compliance.models import OptOut
from src.modules.discovery.models import LeadContact
from src.modules.outreach.models import (
    Conversation,
    Message,
    MessageDirection,
    MessageTemplate,
    MessageType,
    OutreachJob,
    OutreachJobStatus,
    TemplateCategory,
    TemplateStatus,
)
from src.modules.outreach.service import ConversationService
from src.modules.outreach.webhooks import _handle_messages, _handle_statuses, receive_webhook
from src.workers import agent_runtime as runtime_worker
from tests.conftest import TEST_DATABASE_URL

_APP_SECRET = "runtime-integration-app-secret"
_WABA_ID = "runtime-integration-waba"
_PHONE_NUMBER_ID = "runtime-integration-phone"


def test_runtime_context_accepts_only_matching_server_owned_reply_metadata() -> None:
    version_id = uuid4()
    message = SimpleNamespace(
        direction=MessageDirection.OUTBOUND,
        raw={
            "runtime_job_id": str(uuid4()),
            "agent_version_id": str(version_id),
            "action": "reply",
            "fact_ids": ["palanga_pulley_details", "cast_pulley_performance"],
        },
    )

    assert runtime_worker._trusted_runtime_context_fact_ids(
        message,
        agent_version_id=version_id,
    ) == ("palanga_pulley_details", "cast_pulley_performance")


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"runtime_job_id": "job", "action": "reply", "fact_ids": ["fact"]},
        {
            "runtime_job_id": "job",
            "agent_version_id": "different-version",
            "action": "reply",
            "fact_ids": ["fact"],
        },
        {
            "runtime_job_id": "job",
            "agent_version_id": "VERSION",
            "action": "handoff",
            "fact_ids": ["fact"],
        },
        {
            "runtime_job_id": "job",
            "agent_version_id": "VERSION",
            "action": "reply",
            "fact_ids": ["fact", "fact"],
        },
    ],
)
def test_runtime_context_rejects_manual_stale_or_malformed_metadata(
    raw: dict[str, object],
) -> None:
    version_id = uuid4()
    normalized_raw = {
        key: (str(version_id) if value == "VERSION" else value)
        for key, value in raw.items()
    }
    message = SimpleNamespace(
        direction=MessageDirection.OUTBOUND,
        raw=normalized_raw,
    )

    assert runtime_worker._trusted_runtime_context_fact_ids(
        message,
        agent_version_id=version_id,
    ) == ()


async def test_runtime_worker_dispatches_planned_cta_as_one_meta_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, str]] = []

    async def send_cta(
        _client: WhatsAppClient,
        to: str,
        body: str,
        *,
        button_text: str,
        url: str,
    ) -> dict[str, list[dict[str, str]]]:
        calls.append((to, body, button_text, url))
        return {"messages": [{"id": "wamid.interactive"}]}

    monkeypatch.setattr(WhatsAppClient, "send_cta_url_once", send_cta)
    turn = RuntimeTurn(
        action=CustomerReplyAction.REPLY,
        reply="Palanga kasnağı bilgisi",
        fact_ids=("palanga_pulley_details",),
        interaction=RuntimeInteraction(
            kind=RuntimeInteractionKind.CTA_URL,
            button_text="Ürünü incele",
            url="https://www.artikasnak.com/asansor-kasnagi",
        ),
    )

    response, transport_type = await runtime_worker._send_runtime_turn_once(
        "+905321112233",
        turn,
    )

    assert response["messages"][0]["id"] == "wamid.interactive"
    assert transport_type == "interactive:cta_url"
    assert calls == [
        (
            "+905321112233",
            "Palanga kasnağı bilgisi",
            "Ürünü incele",
            "https://www.artikasnak.com/asansor-kasnagi",
        )
    ]


async def test_runtime_worker_dispatches_session_image_header_in_one_meta_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def send_cta(
        _client: WhatsAppClient,
        to: str,
        body: str,
        *,
        button_text: str,
        url: str,
        header_media: dict[str, str],
    ) -> dict[str, list[dict[str, str]]]:
        calls.append(
            {
                "to": to,
                "body": body,
                "button_text": button_text,
                "url": url,
                "header_media": header_media,
            }
        )
        return {"messages": [{"id": "wamid.image-header"}]}

    monkeypatch.setattr(WhatsAppClient, "send_cta_url_once", send_cta)
    turn = RuntimeTurn(
        action=CustomerReplyAction.REPLY,
        reply="Captormal plastik asansör kasnağı bilgisi",
        fact_ids=("plastic_pulley_performance",),
        interaction=RuntimeInteraction(
            kind=RuntimeInteractionKind.CTA_URL,
            button_text="Ürünü incele",
            url="https://www.artikasnak.com/urunler/captormal-asansor-kasnagi",
            header_media=MediaAsset(
                id="captormal-elevator-image",
                kind="image",
                url=(
                    "https://api.ashiraai.com/media/arti-kasnak/"
                    "captormal-elevator.jpg"
                ),
                mime_type="image/jpeg",
                size_bytes=90083,
                provenance="official product page",
            ),
        ),
    )

    response, transport_type = await runtime_worker._send_runtime_turn_once(
        "+905321112233",
        turn,
    )

    assert response["messages"][0]["id"] == "wamid.image-header"
    assert transport_type == "interactive:cta_url"
    assert calls == [
        {
            "to": "+905321112233",
            "body": "Captormal plastik asansör kasnağı bilgisi",
            "button_text": "Ürünü incele",
            "url": "https://www.artikasnak.com/urunler/captormal-asansor-kasnagi",
            "header_media": {
                "kind": "image",
                "link": (
                    "https://api.ashiraai.com/media/arti-kasnak/"
                    "captormal-elevator.jpg"
                ),
                "mime_type": "image/jpeg",
                "size_bytes": "90083",
            },
        }
    ]


async def _db_reachable(session: AsyncSession) -> bool:
    try:
        await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def runtime_database(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    """Point application-level ``session_scope`` at the disposable test DB."""

    if not await _db_reachable(db_session):
        pytest.skip("test database not reachable — see tests/conftest.py")

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_DEBUG", "false")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", _APP_SECRET)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "runtime-integration-access-token")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "runtime-integration-verify-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", _PHONE_NUMBER_ID)
    monkeypatch.setenv("WHATSAPP_BUSINESS_ACCOUNT_ID", _WABA_ID)
    monkeypatch.setenv("WHATSAPP_AGENT_SLUG", "arti-kasnak")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1")

    async def typing_success(_client: WhatsAppClient, _message_id: str) -> dict[str, bool]:
        return {"success": True}

    monkeypatch.setattr(WhatsAppClient, "send_typing_indicator", typing_success)

    await dispose_engine()
    get_settings.cache_clear()
    try:
        yield
    finally:
        # Tests deliberately share one configured WABA. The production
        # partial unique index allows only one active tenant for that sender,
        # so remove this test's fully-cascading tenant before the next case.
        async with session_scope() as session:
            from src.modules.outreach.models import SenderProfile
            tenant_ids = (await session.execute(select(Tenant.id).where(Tenant.slug.like("runtime-integration-%")))).scalars().all()
            for tid in tenant_ids:
                await set_tenant_context(session, tid)
                await session.execute(delete(SenderProfile).where(SenderProfile.tenant_id == tid))
            await set_tenant_context(session, None)
            await session.execute(delete(Tenant).where(Tenant.slug.like("runtime-integration-%")))
            await session.commit()
        await dispose_engine()
        get_settings.cache_clear()


def _runtime_company_config() -> dict[str, object]:
    return {
        "schema_version": "company-agent-config/1.0",
        "lifecycle": "approved",
        "organization": {
            "id": "company",
            "display_names": {"tr-TR": "Artı Kasnak Test"},
        },
        "agent": {
            "purposes": ["sales"],
            "supported_locales": ["tr-TR"],
            "default_locale": "tr-TR",
            "unknown_fact_action": "handoff",
        },
    }


async def _seed_runtime_tenant(*, with_agent: bool = False) -> tuple[UUID, UUID | None]:
    tenant_id = uuid4()
    owner_id: UUID | None = None
    tenant = Tenant(
        id=tenant_id,
        name="Runtime Integration Tenant",
        slug=f"runtime-integration-{tenant_id.hex}",
        status=TenantStatus.ACTIVE,
        wa_business_account_id=_WABA_ID,
    )
    async with session_scope() as session:
        session.add(tenant)
        await session.commit()

    if with_agent:
        owner_id = uuid4()
        async with session_scope(tenant_id) as session:
            owner = User(
                id=owner_id,
                tenant_id=tenant_id,
                email=f"owner-{tenant_id.hex}@example.test",
                password_hash="not-a-real-password-hash",
                full_name="Runtime Owner",
                role=UserRole.TENANT_OWNER,
                is_active=True,
            )
            agent = Agent(
                tenant_id=tenant_id,
                name="Artı Kasnak",
                slug="arti-kasnak",
                is_active=True,
            )
            session.add_all([owner, agent])
            await session.flush()
            from src.modules.outreach.models import SenderProfile
            session.add(SenderProfile(tenant_id=tenant_id, agent_id=agent.id,
                display_name="Runtime sender", phone_number_id=_PHONE_NUMBER_ID,
                business_account_id=_WABA_ID, is_active=True))
            session.add(
                AgentVersion(
                    tenant_id=tenant_id,
                    agent_id=agent.id,
                    version=1,
                    status=AgentVersionStatus.LIVE,
                    company_config=_runtime_company_config(),
                )
            )
            await session.commit()

    return tenant_id, owner_id


def _webhook_payload(*, wa_message_id: str, phone_number_id: str = _PHONE_NUMBER_ID) -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": _WABA_ID,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "messages": [
                                {
                                    "from": "905321112233",
                                    "id": wa_message_id,
                                    "timestamp": "1786807996",
                                    "type": "text",
                                    "text": {"body": "Merhaba"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def _signed_request(raw: bytes) -> tuple[Request, str]:
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/webhooks/whatsapp/test",
            "raw_path": b"/webhooks/whatsapp/test",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        },
        receive,
    )
    digest = hmac.new(_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return request, f"sha256={digest}"


async def _receive_signed(tenant_slug: str, raw: bytes) -> dict[str, Any]:
    request, signature = _signed_request(raw)
    return await receive_webhook(tenant_slug, request, signature)


async def _create_inbound_job(tenant_id: UUID, wa_message_id: str) -> tuple[UUID, UUID]:
    async with session_scope(tenant_id) as session:
        job_ids = await _handle_messages(
            session,
            tenant_id,
            [
                {
                    "from": "905321112233",
                    "id": wa_message_id,
                    "type": "text",
                    "text": {"body": "Bana fiyat verebilir misiniz?"},
                }
            ],
        )
        assert len(job_ids) == 1
        job = await session.get(AgentRuntimeJob, job_ids[0])
        assert job is not None
        conversation_id = job.conversation_id
        await session.commit()
    return job_ids[0], conversation_id


@pytest.mark.parametrize(
    "barrier_type",
    [
        MessageType.TEXT,
        MessageType.TEMPLATE,
        MessageType.IMAGE,
        MessageType.DOCUMENT,
    ],
)
async def test_latest_outbound_of_any_type_blocks_stale_bot_context(
    runtime_database: None,
    barrier_type: MessageType,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    job_id, conversation_id = await _create_inbound_job(
        tenant_id,
        f"wamid.context-barrier-{barrier_type.value}",
    )

    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        version = await session.scalar(
            select(AgentVersion).where(AgentVersion.tenant_id == tenant_id)
        )
        assert job is not None
        assert version is not None
        current = await session.get(Message, job.inbound_message_id)
        assert current is not None

        previous_bot = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction=MessageDirection.OUTBOUND,
            message_type=MessageType.TEXT,
            body="Palanga kasnağı bilgisi",
            wa_message_id=f"wamid.old-bot-{barrier_type.value}",
            raw={
                "runtime_job_id": str(job_id),
                "agent_version_id": str(version.id),
                "action": CustomerReplyAction.REPLY.value,
                "fact_ids": ["palanga_pulley_details"],
            },
            created_at=current.created_at - timedelta(minutes=2),
        )
        latest_outbound = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction=MessageDirection.OUTBOUND,
            message_type=barrier_type,
            body="Temsilci veya kampanya mesajı",
            wa_message_id=f"wamid.latest-barrier-{barrier_type.value}",
            raw={"manual_or_campaign": True},
            created_at=current.created_at - timedelta(minutes=1),
        )
        session.add_all([previous_bot, latest_outbound])
        await session.flush()

        history, context_fact_ids = await runtime_worker._conversation_history_with_context(
            session,
            conversation_id,
            current,
            agent_version_id=version.id,
        )

    assert context_fact_ids == ()
    assert any(message.content == "Palanga kasnağı bilgisi" for message in history)
    barrier_body_in_history = any(
        message.content == "Temsilci veya kampanya mesajı" for message in history
    )
    if barrier_type == MessageType.TEXT:
        assert barrier_body_in_history
    else:
        assert not barrier_body_in_history


async def test_quoted_bot_turn_overrides_a_newer_non_text_context_barrier(
    runtime_database: None,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    job_id, conversation_id = await _create_inbound_job(
        tenant_id,
        "wamid.context-quoted-current",
    )

    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        version = await session.scalar(
            select(AgentVersion).where(AgentVersion.tenant_id == tenant_id)
        )
        assert job is not None
        assert version is not None
        current = await session.get(Message, job.inbound_message_id)
        assert current is not None

        quoted_bot = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction=MessageDirection.OUTBOUND,
            message_type=MessageType.TEXT,
            body="Palanga kasnağı bilgisi",
            wa_message_id="wamid.context-quoted-bot",
            raw={
                "runtime_job_id": str(job_id),
                "agent_version_id": str(version.id),
                "action": CustomerReplyAction.REPLY.value,
                "fact_ids": ["palanga_pulley_details"],
            },
            created_at=current.created_at - timedelta(minutes=2),
        )
        newer_template = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction=MessageDirection.OUTBOUND,
            message_type=MessageType.TEMPLATE,
            body="Yeni kampanya mesajı",
            wa_message_id="wamid.context-newer-template",
            raw={"campaign": True},
            created_at=current.created_at - timedelta(minutes=1),
        )
        session.add_all([quoted_bot, newer_template])
        await session.flush()
        current.raw = {
            **(current.raw or {}),
            "context": {"id": quoted_bot.wa_message_id},
        }
        await session.flush()

        _, context_fact_ids = await runtime_worker._conversation_history_with_context(
            session,
            conversation_id,
            current,
            agent_version_id=version.id,
        )

    assert context_fact_ids == ("palanga_pulley_details",)


async def test_conversation_history_is_bounded_to_recent_messages_and_characters(
    runtime_database: None,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    job_id, conversation_id = await _create_inbound_job(
        tenant_id,
        "wamid.context-history-current",
    )

    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        version = await session.scalar(
            select(AgentVersion).where(AgentVersion.tenant_id == tenant_id)
        )
        assert job is not None
        assert version is not None
        current = await session.get(Message, job.inbound_message_id)
        assert current is not None

        for index in range(10):
            session.add(
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    direction=MessageDirection.INBOUND,
                    message_type=MessageType.TEXT,
                    body=f"message-{index}:".ljust(500, str(index)),
                    raw={},
                    created_at=current.created_at - timedelta(seconds=10 - index),
                )
            )
        await session.flush()

        history, context_fact_ids = await runtime_worker._conversation_history_with_context(
            session,
            conversation_id,
            current,
            agent_version_id=version.id,
        )

    assert context_fact_ids == ()
    assert len(history) == 6
    assert sum(len(message.content) for message in history) == 3000
    assert history[0].content.startswith("message-4:")
    assert history[-1].content.startswith("message-9:")


async def _create_outreach_job(
    tenant_id: UUID,
    conversation_id: UUID,
    wa_message_id: str,
) -> UUID:
    async with session_scope(tenant_id) as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        contact = await session.get(LeadContact, conversation.contact_id)
        assert contact is not None
        template = MessageTemplate(
            tenant_id=tenant_id,
            name=f"delivery-{uuid4().hex}",
            language="tr",
            category=TemplateCategory.UTILITY,
            status=TemplateStatus.APPROVED,
            body="Test",
        )
        session.add(template)
        await session.flush()
        job = OutreachJob(
            tenant_id=tenant_id,
            lead_id=contact.lead_id,
            contact_id=contact.id,
            template_id=template.id,
            status=OutreachJobStatus.PENDING,
            wa_message_id=wa_message_id,
        )
        session.add(job)
        await session.flush()
        job_id = job.id
        await session.commit()
    return job_id


class _HandoffLLM:
    async def complete(self, *_args: object, **_kwargs: object) -> str:
        return '{"action":"handoff","fact_ids":[]}'


async def test_session_scope_clears_tenant_guc_on_the_same_pooled_connection(
    runtime_database: None,
) -> None:
    tenant_id = uuid4()
    async with session_scope(tenant_id) as session:
        pid_before, current_tenant = (
            await session.execute(
                text("SELECT pg_backend_pid(), current_setting('app.current_tenant', true)")
            )
        ).one()
        assert current_tenant == str(tenant_id)

    # Only one connection has been opened, so this checkout must reuse the
    # exact backend returned by session_scope.  It must no longer carry the
    # previous tenant's session-scoped GUC.
    async with get_sessionmaker()() as session:
        pid_after, current_tenant = (
            await session.execute(
                text("SELECT pg_backend_pid(), current_setting('app.current_tenant', true)")
            )
        ).one()
        assert pid_after == pid_before
        assert current_tenant == ""


async def test_active_waba_binding_is_unique_but_suspended_duplicate_is_allowed(
    runtime_database: None,
) -> None:
    await _seed_runtime_tenant()
    suspended_id = uuid4()
    async with session_scope() as session:
        session.add(
            Tenant(
                id=suspended_id,
                name="Suspended WABA Duplicate",
                slug=f"runtime-integration-suspended-{suspended_id.hex}",
                status=TenantStatus.SUSPENDED,
                wa_business_account_id=_WABA_ID,
            )
        )
        await session.commit()

    async with session_scope() as session:
        suspended = await session.get(Tenant, suspended_id)
        assert suspended is not None
        suspended.status = TenantStatus.ACTIVE
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with session_scope() as session:
        suspended = await session.get(Tenant, suspended_id)
        assert suspended is not None
        assert suspended.status == TenantStatus.SUSPENDED


async def test_bootstrap_rejects_waba_owned_by_another_active_tenant(
    runtime_database: None,
) -> None:
    await _seed_runtime_tenant()
    target_id = uuid4()
    target_slug = f"runtime-integration-bootstrap-{target_id.hex}"
    async with session_scope() as session:
        session.add(
            Tenant(
                id=target_id,
                name="Bootstrap Target",
                slug=target_slug,
                status=TenantStatus.ACTIVE,
                wa_business_account_id=None,
            )
        )
        await session.commit()

    config = CompanyAgentConfig.model_validate(_runtime_company_config())
    with pytest.raises(SystemExit, match="already bound to active tenant"):
        await _reconcile(
            config,
            tenant_slug=target_slug,
            agent_slug="arti-kasnak",
            dry_run=False,
        )

    async with session_scope() as session:
        target = await session.get(Tenant, target_id)
        assert target is not None
        assert target.wa_business_account_id is None


async def test_signed_duplicate_webhook_is_idempotent_and_enforces_sender_binding(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    tenant_slug = f"runtime-integration-{tenant_id.hex}"
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runtime_worker.process_runtime_job,
        "delay",
        lambda *args: queued.append((str(args[0]), str(args[1]))),
    )

    raw = _webhook_payload(wa_message_id="wamid.integration-duplicate")
    assert await _receive_signed(tenant_slug, raw) == {"ok": True}
    assert await _receive_signed(tenant_slug, raw) == {"ok": True}

    async with session_scope(tenant_id) as session:
        inbound_count = await session.scalar(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant_id,
                Message.direction == MessageDirection.INBOUND,
                Message.wa_message_id == "wamid.integration-duplicate",
            )
        )
        runtime_job_count = await session.scalar(
            select(func.count(AgentRuntimeJob.id)).where(AgentRuntimeJob.tenant_id == tenant_id)
        )
        assert inbound_count == 1
        assert runtime_job_count == 1
    assert len(queued) == 2
    assert len(set(queued)) == 1

    mismatched = _webhook_payload(
        wa_message_id="wamid.integration-wrong-sender",
        phone_number_id="another-phone-number-id",
    )
    with pytest.raises(HTTPException) as exc_info:
        await _receive_signed(tenant_slug, mismatched)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "sender binding mismatch"

    async with session_scope(tenant_id) as session:
        rejected_count = await session.scalar(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant_id,
                Message.wa_message_id == "wamid.integration-wrong-sender",
            )
        )
        assert rejected_count == 0


async def test_out_of_order_outreach_callbacks_are_monotonic_and_use_meta_time(
    runtime_database: None,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant()
    _, conversation_id = await _create_inbound_job(tenant_id, "wamid.integration-status-source")
    job_id = await _create_outreach_job(
        tenant_id, conversation_id, "wamid.integration-outreach-status"
    )

    sent_timestamp = 1_700_000_100
    delivered_timestamp = 1_700_000_200
    read_timestamp = 1_700_000_300
    failed_timestamp = 1_700_000_400
    async with session_scope(tenant_id) as session:
        # Meta may retry or deliver callbacks in any order. Receiving read
        # first is conclusive; later lower-rank callbacks only backfill their
        # own milestone timestamps and cannot regress the effective status.
        await _handle_statuses(
            session,
            tenant_id,
            [
                {
                    "id": "wamid.integration-outreach-status",
                    "status": "read",
                    "timestamp": str(read_timestamp),
                },
                {
                    "id": "wamid.integration-outreach-status",
                    "status": "sent",
                    "timestamp": str(sent_timestamp),
                },
                {
                    "id": "wamid.integration-outreach-status",
                    "status": "delivered",
                    "timestamp": str(delivered_timestamp),
                },
                {
                    "id": "wamid.integration-outreach-status",
                    "status": "failed",
                    "timestamp": str(failed_timestamp),
                    "errors": [{"code": 131026, "title": "Undeliverable"}],
                },
            ],
        )
        await session.commit()

    async with session_scope(tenant_id) as session:
        job = await session.get(OutreachJob, job_id)
        assert job is not None
        assert job.status == OutreachJobStatus.READ
        assert job.sent_at == datetime.fromtimestamp(sent_timestamp, UTC)
        assert job.delivered_at == datetime.fromtimestamp(delivered_timestamp, UTC)
        assert job.read_at == datetime.fromtimestamp(read_timestamp, UTC)
        assert job.error is not None
        assert datetime.fromtimestamp(failed_timestamp, UTC).isoformat() in job.error
        assert "131026" in job.error


async def test_failed_callback_needs_conclusive_delivery_evidence_to_recover(
    runtime_database: None,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant()
    _, conversation_id = await _create_inbound_job(tenant_id, "wamid.integration-failure-source")
    job_id = await _create_outreach_job(
        tenant_id, conversation_id, "wamid.integration-failure-status"
    )

    async with session_scope(tenant_id) as session:
        await _handle_statuses(
            session,
            tenant_id,
            [
                {
                    "id": "wamid.integration-failure-status",
                    "status": "failed",
                    "timestamp": "1700000300",
                    "errors": [{"code": 131000, "title": "Unknown error"}],
                },
                {
                    "id": "wamid.integration-failure-status",
                    "status": "sent",
                    "timestamp": "1700000100",
                },
            ],
        )
        await session.commit()

    async with session_scope(tenant_id) as session:
        job = await session.get(OutreachJob, job_id)
        assert job is not None
        # A stale sent callback says only that Meta accepted the message; it
        # cannot disprove the later delivery failure.
        assert job.status == OutreachJobStatus.FAILED

        await _handle_statuses(
            session,
            tenant_id,
            [
                {
                    "id": "wamid.integration-failure-status",
                    "status": "delivered",
                    "timestamp": "1700000200",
                }
            ],
        )
        await session.commit()

    async with session_scope(tenant_id) as session:
        job = await session.get(OutreachJob, job_id)
        assert job is not None
        # Delivery is conclusive successful evidence, even when its callback
        # reaches us after the failed callback.
        assert job.status == OutreachJobStatus.DELIVERED


async def test_runtime_delivery_audit_preserves_history_errors_and_effective_status(
    runtime_database: None,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant()
    job_id, _ = await _create_inbound_job(tenant_id, "wamid.integration-audit-source")
    outbound_wa_id = "wamid.integration-runtime-status"

    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        job.outbound_wa_message_id = outbound_wa_id
        job.status = AgentRuntimeJobStatus.SENT.value
        job.audit = {"existing_key": "preserved"}
        await session.commit()

    callbacks = [
        {"id": outbound_wa_id, "status": "read", "timestamp": "1700000300"},
        {"id": outbound_wa_id, "status": "sent", "timestamp": "1700000100"},
        {"id": outbound_wa_id, "status": "delivered", "timestamp": "1700000200"},
        {
            "id": outbound_wa_id,
            "status": "failed",
            "timestamp": "1700000400",
            "errors": [{"code": 131026, "title": "Undeliverable"}],
        },
        {
            "id": outbound_wa_id,
            "status": "failed",
            "timestamp": "1700000500",
            "errors": [{"code": 131047, "title": "Re-engagement window"}],
        },
    ]
    async with session_scope(tenant_id) as session:
        await _handle_statuses(session, tenant_id, [*callbacks, callbacks[3]])
        await session.commit()

    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        audit = job.audit
        assert audit["existing_key"] == "preserved"
        assert audit["delivery_status"] == "read"
        assert audit["delivery_status_at"] == datetime.fromtimestamp(1_700_000_300, UTC).isoformat()
        assert [event["status"] for event in audit["delivery_history"]] == [
            "read",
            "sent",
            "delivered",
            "failed",
            "failed",
        ]
        assert all(event["timestamp_source"] == "meta" for event in audit["delivery_history"])
        # The exact retry of the first failed callback is deduplicated, while
        # distinct failure callbacks and both Meta error payloads survive.
        assert len(audit["delivery_history"]) == 5
        assert [item["error"]["code"] for item in audit["delivery_errors"]] == [
            131026,
            131047,
        ]


async def test_handoff_pauses_new_jobs_until_manual_inbox_reply_resolves_it(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, owner_id = await _seed_runtime_tenant(with_agent=True)
    job_id, conversation_id = await _create_inbound_job(tenant_id, "wamid.integration-handoff-one")
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: _HandoffLLM())

    sent_ids = iter(["wamid.integration-bot-handoff", "wamid.integration-manual"])

    async def send_success(
        _client: WhatsAppClient,
        _to: str,
        _body: str,
        _preview_url: bool = False,
    ) -> dict[str, list[dict[str, str]]]:
        return {"messages": [{"id": next(sent_ids)}]}

    monkeypatch.setattr(WhatsAppClient, "send_text_once", send_success)
    result = await runtime_worker._process_runtime_job(tenant_id, job_id)
    assert result == {
        "status": AgentRuntimeJobStatus.HANDOFF.value,
        "wa_message_id": "wamid.integration-bot-handoff",
    }

    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        conversation = await session.get(Conversation, conversation_id)
        system_messages = list(
            (
                await session.execute(
                    select(Message).where(
                        Message.tenant_id == tenant_id,
                        Message.conversation_id == conversation_id,
                        Message.message_type == MessageType.SYSTEM,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert job is not None
        assert job.status == AgentRuntimeJobStatus.HANDOFF.value
        assert job.audit["typing_indicator_sent"] is True
        timing = job.audit["timing"]
        assert timing["queue_ms"] >= 0
        assert timing["flags"]["result_status"] == "handoff"
        assert any(s["stage"] == "whatsapp.send" and s["status"] == "ok" for s in timing["spans"])
        assert job.audit["manual_review_required"] is True
        assert conversation is not None
        assert conversation.assigned_to == owner_id
        assert len(system_messages) == 1
        assert system_messages[0].raw["internal_only"] is True
    # A new inbound message is durable, but the active handoff prevents a
    # second bot job from being created while a human owns the conversation.
    async with session_scope(tenant_id) as session:
        paused_job_ids = await _handle_messages(
            session,
            tenant_id,
            [
                {
                    "from": "905321112233",
                    "id": "wamid.integration-handoff-two",
                    "type": "text",
                    "text": {"body": "Orada mısınız?"},
                }
            ],
        )
        await session.commit()
    assert paused_job_ids == []

    async with session_scope(tenant_id) as session:
        await ConversationService(session).send_free_form(
            tenant_id, conversation_id, "Talebinizi inceliyoruz."
        )

    async with session_scope(tenant_id) as session:
        still_paused = await session.get(AgentRuntimeJob, job_id)
        assert still_paused.status == AgentRuntimeJobStatus.HANDOFF.value
        from src.modules.outreach.inbox_control import resume
        await resume(conversation_id, session, {"tid": str(tenant_id), "sub": str(owner_id)})

    async with session_scope(tenant_id) as session:
        resolved = await session.get(AgentRuntimeJob, job_id)
        assert resolved is not None
        assert resolved.status == AgentRuntimeJobStatus.RESOLVED.value
        assert resolved.audit["manual_review_required"] is False

        resumed_job_ids = await _handle_messages(
            session,
            tenant_id,
            [
                {
                    "from": "905321112233",
                    "id": "wamid.integration-handoff-three",
                    "type": "text",
                    "text": {"body": "Teşekkürler, bekliyorum."},
                }
            ],
        )
        await session.commit()
    assert len(resumed_job_ids) == 1


async def test_new_inbound_preserves_handoff_without_active_reviewer(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, owner_id = await _seed_runtime_tenant(with_agent=True)
    assert owner_id is not None
    job_id, _ = await _create_inbound_job(
        tenant_id, "wamid.integration-handoff-no-reviewer-one"
    )
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: _HandoffLLM())

    async def send_success(
        _client: WhatsAppClient,
        _to: str,
        _body: str,
        _preview_url: bool = False,
    ) -> dict[str, list[dict[str, str]]]:
        return {"messages": [{"id": "wamid.integration-handoff-no-reviewer-reply"}]}

    monkeypatch.setattr(WhatsAppClient, "send_text_once", send_success)
    result = await runtime_worker._process_runtime_job(tenant_id, job_id)
    assert result["status"] == AgentRuntimeJobStatus.HANDOFF.value

    async with session_scope(tenant_id) as session:
        owner = await session.get(User, owner_id)
        assert owner is not None
        owner.is_active = False
        await session.commit()

    async with session_scope(tenant_id) as session:
        resumed_job_ids = await _handle_messages(
            session,
            tenant_id,
            [
                {
                    "from": "905321112233",
                    "id": "wamid.integration-handoff-no-reviewer-two",
                    "type": "text",
                    "text": {"body": "Merhaba"},
                }
            ],
        )
        await session.commit()
    assert resumed_job_ids == []
    async with session_scope(tenant_id) as session:
        paused = await session.get(AgentRuntimeJob, job_id)
        assert paused is not None
        assert paused.status == AgentRuntimeJobStatus.HANDOFF.value


async def test_typing_indicator_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def typing_failure(_client: WhatsAppClient, _message_id: str) -> dict[str, bool]:
        raise httpx.ReadTimeout("typing indicator timed out")

    monkeypatch.setattr(WhatsAppClient, "send_typing_indicator", typing_failure)

    assert await runtime_worker._send_typing_indicator_best_effort("wamid.inbound-timeout") is False


async def test_typing_indicator_is_refreshed_until_model_reply_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second_indicator_sent = asyncio.Event()
    indicator_calls = 0

    async def typing_success(_client: WhatsAppClient, _message_id: str) -> dict[str, bool]:
        nonlocal indicator_calls
        indicator_calls += 1
        if indicator_calls >= 2:
            second_indicator_sent.set()
        return {"success": True}

    monkeypatch.setattr(runtime_worker, "_TYPING_INDICATOR_REFRESH_SECONDS", 0.01)
    monkeypatch.setattr(WhatsAppClient, "send_typing_indicator", typing_success)

    stats = {"attempts": 1, "successes": 1, "refreshes": 0}
    refresh_task = asyncio.create_task(
        runtime_worker._refresh_typing_indicator("wamid.inbound-refresh", stats)
    )
    await asyncio.wait_for(second_indicator_sent.wait(), timeout=5)
    refresh_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await refresh_task
    calls_at_completion = indicator_calls
    await asyncio.sleep(0.03)

    assert calls_at_completion >= 2
    assert indicator_calls == calls_at_completion
    assert stats == {
        "attempts": calls_at_completion + 1,
        "successes": calls_at_completion + 1,
        "refreshes": calls_at_completion,
    }


async def test_ambiguous_meta_post_is_attempted_once_and_requires_manual_review(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, owner_id = await _seed_runtime_tenant(with_agent=True)
    job_id, conversation_id = await _create_inbound_job(tenant_id, "wamid.integration-ambiguous")
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: _HandoffLLM())
    attempts = 0

    async def ambiguous_send(
        _client: WhatsAppClient,
        _to: str,
        _body: str,
        _preview_url: bool = False,
    ) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("Meta may have accepted the POST")

    monkeypatch.setattr(WhatsAppClient, "send_text_once", ambiguous_send)

    first = await runtime_worker._process_runtime_job(tenant_id, job_id)
    second = await runtime_worker._process_runtime_job(tenant_id, job_id)
    assert first == {"status": "failed", "retryable": False}
    assert second == {"status": AgentRuntimeJobStatus.FAILED.value}
    assert attempts == 1

    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        conversation = await session.get(Conversation, conversation_id)
        customer_visible_outbound = await session.scalar(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant_id,
                Message.conversation_id == conversation_id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.message_type == MessageType.TEXT,
            )
        )
        internal_review_messages = await session.scalar(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant_id,
                Message.conversation_id == conversation_id,
                Message.message_type == MessageType.SYSTEM,
            )
        )
        assert job is not None
        assert job.status == AgentRuntimeJobStatus.FAILED.value
        assert job.attempts == 1
        assert job.audit["external_send_attempts"] == 1
        assert job.audit["manual_review_required"] is True
        assert job.audit["retry_suppressed"] is True
        timing = job.audit["timing"]
        assert timing["flags"]["attempt"] == 1
        assert timing["flags"]["retryable"] is False
        assert any(s["stage"] == "whatsapp.send" and s["status"] == "error" for s in timing["spans"])
        assert job.outbound_message_id is None
        assert customer_visible_outbound == 0
        assert internal_review_messages == 1
        assert conversation is not None
        assert conversation.assigned_to == owner_id


async def test_stop_during_model_call_terminalizes_all_jobs_before_meta_post(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    processing_job_id, conversation_id = await _create_inbound_job(
        tenant_id, "wamid.integration-stop-processing"
    )
    model_started = asyncio.Event()
    release_model = asyncio.Event()

    class _BlockingLLM:
        async def complete(self, *_args: object, **_kwargs: object) -> str:
            model_started.set()
            await release_model.wait()
            return '{"action":"handoff","fact_ids":[]}'

    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: _BlockingLLM())
    sends = 0

    async def send_success(
        _client: WhatsAppClient,
        _to: str,
        _body: str,
        _preview_url: bool = False,
    ) -> dict[str, list[dict[str, str]]]:
        nonlocal sends
        sends += 1
        return {"messages": [{"id": "wamid.must-not-be-sent"}]}

    monkeypatch.setattr(WhatsAppClient, "send_text_once", send_success)
    worker_task = asyncio.create_task(
        runtime_worker._process_runtime_job(tenant_id, processing_job_id)
    )
    await asyncio.wait_for(model_started.wait(), timeout=5)

    try:
        async with session_scope(tenant_id) as session:
            enqueued_job_ids = await _handle_messages(
                session,
                tenant_id,
                [
                    {
                        "from": "905321112233",
                        "id": "wamid.integration-stop-pending",
                        "type": "text",
                        "text": {"body": "Bir sorum daha var."},
                    },
                    {
                        "from": "905321112233",
                        "id": "wamid.integration-stop",
                        "type": "text",
                        "text": {"body": "STOP"},
                    },
                ],
            )
            await session.commit()
        assert enqueued_job_ids == []
    finally:
        release_model.set()

    result = await asyncio.wait_for(worker_task, timeout=5)
    assert result == {"status": AgentRuntimeJobStatus.SKIPPED.value}
    assert sends == 0

    async with session_scope(tenant_id) as session:
        jobs = list(
            (
                await session.execute(
                    select(AgentRuntimeJob)
                    .where(AgentRuntimeJob.conversation_id == conversation_id)
                    .order_by(AgentRuntimeJob.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        opt_outs = await session.scalar(
            select(func.count(OptOut.id)).where(OptOut.phone_e164 == "+905321112233")
        )
        customer_visible_outbound = await session.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.message_type == MessageType.TEXT,
            )
        )
        assert len(jobs) == 2
        assert all(job.status == AgentRuntimeJobStatus.SKIPPED.value for job in jobs)
        assert all(job.audit["cancelled_by_opt_out"] is True for job in jobs)
        assert opt_outs == 1
        assert customer_visible_outbound == 0


async def test_product_specific_disinterest_does_not_create_a_global_opt_out(
    runtime_database: None,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant()

    async with session_scope(tenant_id) as session:
        job_ids = await _handle_messages(
            session,
            tenant_id,
            [
                {
                    "from": "905321112233",
                    "id": "wamid.integration-product-disinterest",
                    "type": "text",
                    "text": {
                        "body": (
                            "Palanga kasnağıyla ilgilenmiyorum, "
                            "diğer ürünleri göster"
                        )
                    },
                }
            ],
        )
        await session.commit()

    assert len(job_ids) == 1
    async with session_scope(tenant_id) as session:
        opt_outs = await session.scalar(
            select(func.count(OptOut.id)).where(
                OptOut.tenant_id == tenant_id,
                OptOut.phone_e164 == "+905321112233",
            )
        )
        job = await session.get(AgentRuntimeJob, job_ids[0])
        assert job is not None
        job_status = job.status

    assert opt_outs == 0
    assert job_status == AgentRuntimeJobStatus.PENDING.value


async def test_rapid_inbound_turns_are_ordered_and_coalesced_to_one_reply(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    async with session_scope(tenant_id) as session:
        job_ids = await _handle_messages(
            session,
            tenant_id,
            [
                {
                    "from": "905321112233",
                    "id": "wamid.integration-burst-one",
                    "type": "text",
                    "text": {"body": "Bir ürün soracağım."},
                },
                {
                    "from": "905321112233",
                    "id": "wamid.integration-burst-two",
                    "type": "text",
                    "text": {"body": "Döküm kasnak için fiyat nedir?"},
                },
            ],
        )
        await session.commit()
    assert len(job_ids) == 2

    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda *_args, **_kwargs: _HandoffLLM())
    sends = 0

    async def send_success(
        _client: WhatsAppClient,
        _to: str,
        _body: str,
        _preview_url: bool = False,
    ) -> dict[str, list[dict[str, str]]]:
        nonlocal sends
        sends += 1
        return {"messages": [{"id": "wamid.integration-burst-reply"}]}

    monkeypatch.setattr(WhatsAppClient, "send_text_once", send_success)

    # Even if Celery delivers the second task first, it waits for the earlier
    # durable turn. The earlier turn then coalesces into the newer message, so
    # the customer receives one response to the complete rapid burst.
    deferred = await runtime_worker._process_runtime_job(tenant_id, job_ids[1])
    assert deferred["status"] == "deferred"
    assert deferred["waiting_for_job_id"] == str(job_ids[0])

    assert await runtime_worker._process_runtime_job(tenant_id, job_ids[0]) == {
        "status": AgentRuntimeJobStatus.SKIPPED.value
    }
    assert await runtime_worker._process_runtime_job(tenant_id, job_ids[1]) == {
        "status": AgentRuntimeJobStatus.HANDOFF.value,
        "wa_message_id": "wamid.integration-burst-reply",
    }
    assert sends == 1

    async with session_scope(tenant_id) as session:
        jobs = list(
            (
                await session.execute(
                    select(AgentRuntimeJob)
                    .where(AgentRuntimeJob.id.in_(job_ids))
                    .order_by(AgentRuntimeJob.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        assert [job.status for job in jobs] == [
            AgentRuntimeJobStatus.SKIPPED.value,
            AgentRuntimeJobStatus.HANDOFF.value,
        ]
        assert jobs[0].audit["coalesced_into_job_id"] == str(jobs[1].id)


@pytest.mark.parametrize("reason", ["empty_body", "opted_out", "human_review"])
async def test_early_exit_keeps_timing_without_any_model_or_send(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    """Use real outbox/RLS rows to cover paths before model preparation."""
    from src.modules.discovery.models import ConsentStatus

    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    job_id, conversation_id = await _create_inbound_job(tenant_id, "wamid.timing-early")
    blocking_id = None
    if reason == "human_review":
        blocking_id, _ = await _create_inbound_job(tenant_id, "wamid.timing-blocker")
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        if reason == "empty_body":
            inbound = await session.get(Message, job.inbound_message_id)
            assert inbound is not None
            inbound.body = ""
        elif reason == "opted_out":
            conversation = await session.get(Conversation, conversation_id)
            assert conversation is not None
            contact = await session.get(LeadContact, conversation.contact_id)
            assert contact is not None
            contact.consent_status = ConsentStatus.OPT_OUT
        else:
            blocker = await session.get(AgentRuntimeJob, blocking_id)
            assert blocker is not None
            blocker.status = AgentRuntimeJobStatus.HANDOFF.value
        await session.commit()

    async def forbidden(*args, **kwargs):
        pytest.fail("early exit must not send, type or invoke the model")

    monkeypatch.setattr(runtime_worker, "_send_runtime_turn_once", forbidden)
    monkeypatch.setattr(runtime_worker, "_send_typing_indicator_best_effort", forbidden)
    monkeypatch.setattr(runtime_worker.CompanyAgentRuntime, "reply", forbidden)
    assert await runtime_worker._process_runtime_job(tenant_id, job_id) == {"status": "skipped"}
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        assert job.outbound_message_id is None
        timing = job.audit["timing"]
        assert timing["flags"]["result_status"] == "skipped"
        assert timing["flags"]["exit_reason"] == job.error
        assert not any(s["stage"] in {"llm.http", "whatsapp.send"} for s in timing["spans"])


@pytest.mark.parametrize("previous_attempts", [0, 4])
async def test_pre_send_failure_timing_distinguishes_retry_from_exhaustion(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
    previous_attempts: int,
) -> None:
    tenant_id, _ = await _seed_runtime_tenant(with_agent=True)
    job_id, _ = await _create_inbound_job(tenant_id, "wamid.timing-retry")
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        job.attempts = previous_attempts
        await session.commit()

    async def preparation_failure(*args):
        raise OSError("synthetic pre-send failure")

    monkeypatch.setattr(runtime_worker, "_execute_runtime_job", preparation_failure)
    result = await runtime_worker._process_runtime_job(tenant_id, job_id)
    assert result == {"status": "failed", "retryable": previous_attempts == 0}
    async with session_scope(tenant_id) as session:
        job = await session.get(AgentRuntimeJob, job_id)
        assert job is not None
        assert job.status == ("pending" if previous_attempts == 0 else "failed")
        assert job.outbound_message_id is None
        assert job.audit["timing"]["flags"]["attempt"] == previous_attempts + 1
        assert job.audit["timing"]["flags"]["retryable"] == (previous_attempts == 0)
        assert "external_send_attempts" not in job.audit
        if previous_attempts == 4:
            assert job.audit["manual_review_required"] is True
