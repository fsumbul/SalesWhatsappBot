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
from datetime import UTC, datetime
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
from src.core.db import dispose_engine, get_sessionmaker, session_scope
from src.integrations.whatsapp import WhatsAppClient
from src.modules.agents.company_config import CompanyAgentConfig
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
            "display_names": {"tr-TR": "Artı Kasnak Test"},  # noqa: RUF001
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
                name="Artı Kasnak",  # noqa: RUF001
                slug="arti-kasnak",
                is_active=True,
            )
            session.add_all([owner, agent])
            await session.flush()
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
    tenant_id, _ = await _seed_runtime_tenant()
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
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda: _HandoffLLM())

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
                    "text": {"body": "Orada mısınız?"},  # noqa: RUF001
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


async def test_typing_indicator_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def typing_failure(_client: WhatsAppClient, _message_id: str) -> dict[str, bool]:
        raise httpx.ReadTimeout("typing indicator timed out")

    monkeypatch.setattr(WhatsAppClient, "send_typing_indicator", typing_failure)

    assert await runtime_worker._send_typing_indicator_best_effort("wamid.inbound-timeout") is False


async def test_ambiguous_meta_post_is_attempted_once_and_requires_manual_review(
    runtime_database: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, owner_id = await _seed_runtime_tenant(with_agent=True)
    job_id, conversation_id = await _create_inbound_job(tenant_id, "wamid.integration-ambiguous")
    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda: _HandoffLLM())
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

    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda: _BlockingLLM())
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
                    "text": {"body": "Bir ürün soracağım."},  # noqa: RUF001
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

    monkeypatch.setattr(runtime_worker, "get_llm_client", lambda: _HandoffLLM())
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
