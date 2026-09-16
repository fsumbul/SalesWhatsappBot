"""Tests for the LLM client scaffold. There's no real provider to test
against — these pin the deliberate "raise, don't fabricate" behavior of
NullLLMClient and the factory's default wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.integrations.llm import (
    ChatCompletionsLLMClient,
    LLMMessage,
    LLMNotConfiguredError,
    NullLLMClient,
    get_llm_client,
)


async def test_null_client_raises_rather_than_fabricating_a_reply() -> None:
    client = NullLLMClient()
    with pytest.raises(LLMNotConfiguredError):
        await client.complete([LLMMessage(role="user", content="hi")])


async def test_null_client_raises_regardless_of_arguments() -> None:
    client = NullLLMClient()
    with pytest.raises(LLMNotConfiguredError):
        await client.complete(
            [LLMMessage(role="user", content="build me an agent")],
            system="You are a helpful assistant.",
            max_tokens=4096,
        )


def test_factory_returns_null_client_when_no_provider_configured() -> None:
    with patch("src.integrations.llm.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = ""
        assert isinstance(get_llm_client(), NullLLMClient)


def test_factory_returns_null_client_for_unrecognized_provider() -> None:
    """No real provider is implemented yet, so any value falls back to
    NullLLMClient today — this pins that fallback rather than a crash."""
    with patch("src.integrations.llm.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "anthropic"
        assert isinstance(get_llm_client(), NullLLMClient)


def test_factory_returns_chat_completions_client() -> None:
    with patch("src.integrations.llm.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "chat_compatible"
        mock_settings.return_value.llm_base_url = "https://llm.example.test/v1"
        mock_settings.return_value.llm_model = "instruct-model"
        mock_settings.return_value.llm_api_key = "test-key"

        client = get_llm_client()

    assert isinstance(client, ChatCompletionsLLMClient)
    assert client.api_url == "https://llm.example.test/v1/chat/completions"


# --- role-based endpoints (NIM plan WP5) ---------------------------------------------

import json  # noqa: E402

import httpx  # noqa: E402
import respx  # noqa: E402

from src.core.config import Settings  # noqa: E402
from src.integrations.llm import OllamaLLMClient, role_llm_client  # noqa: E402

_STRONG_SECRET = "fK9!vT2@qL7#sN4$wR8%mC5^xP1&zD6*"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "app_debug": False,
        "app_secret_key": _STRONG_SECRET,
        "database_url": "postgresql+asyncpg://app:pass@db/app",
        "redis_url": "redis://redis:6379/0",
        "celery_broker_url": "redis://redis:6379/1",
        "celery_result_backend": "redis://redis:6379/2",
        "whatsapp_app_secret": "s",
        "whatsapp_access_token": "t",
        "whatsapp_verify_token": "v",
        "whatsapp_phone_number_id": "p",
        "whatsapp_business_account_id": "w",
        "llm_provider": "ollama",
        "llm_model": "qwen3:8b",
        "llm_base_url": "http://127.0.0.1:11434/v1",
        "llm_api_key": "",
        "llm_num_ctx": 4096,
        "llm_schema_mode": "response_format",
        "llm_role_extraction_provider": "",
        "llm_role_extraction_model": "",
        "llm_role_extraction_base_url": "",
        "llm_role_memory_provider": "",
        "llm_role_memory_model": "",
        "llm_role_memory_base_url": "",
        "llm_role_generation_provider": "",
        "llm_role_generation_model": "",
        "llm_role_generation_base_url": "",
        "llm_role_admin_provider": "",
        "llm_role_admin_model": "",
        "llm_role_admin_base_url": "",
    }
    values.update(overrides)
    return Settings(**values)


def test_role_endpoints_inherit_the_base_profile_field_by_field() -> None:
    settings = _settings(
        llm_role_memory_provider="chat_compatible",
        llm_role_memory_model="nvidia/nemotron-3.5-lightning-30b-a3b",
        llm_role_memory_base_url="http://10.0.0.5:8050/v1",
        llm_role_memory_schema_mode="nvext_guided_json",
        llm_role_generation_model="gemma-4-31b-it",
    )
    customer = settings.llm_endpoint("customer")
    assert (customer.provider, customer.model, customer.override) == ("ollama", "qwen3:8b", False)

    memory = settings.llm_endpoint("memory")
    assert memory.override and memory.nim
    assert (memory.provider, memory.model, memory.base_url) == (
        "chat_compatible", "nvidia/nemotron-3.5-lightning-30b-a3b", "http://10.0.0.5:8050/v1"
    )
    assert memory.api_key == "" and memory.num_ctx == 4096

    generation = settings.llm_endpoint("generation")  # only the model differs: same Ollama host
    assert generation.override and generation.provider == "ollama"
    assert generation.model == "gemma-4-31b-it" and generation.base_url == customer.base_url
    assert settings.llm_endpoint("extraction").override is False
    assert [e.role for e in settings.llm_role_overrides()] == ["generation", "memory"]

    roles = [e.role for e in settings.model_endpoints()]
    assert roles[:3] == ["llm.customer", "llm.generation", "llm.memory"]
    assert [e.role for e in settings.nim_endpoints()] == ["llm.memory"]
    assert settings.production_runtime_errors() == []


def test_role_overrides_are_gated_in_production() -> None:
    bad = _settings(
        llm_role_extraction_provider="anthropic",
        llm_role_extraction_base_url="https://integrate.api.nvidia.com/v1",
    )
    errors = bad.production_runtime_errors()
    assert "LLM_ROLE_EXTRACTION_PROVIDER must be ollama or chat_compatible" in errors
    # An unsupported provider is never registered as an endpoint (nothing to probe).
    assert all("LLM_ROLE_EXTRACTION_BASE_URL" not in e for e in errors)

    public = _settings(
        llm_role_extraction_provider="chat_compatible",
        llm_role_extraction_model="m",
        llm_role_extraction_base_url="https://integrate.api.nvidia.com/v1",
    )
    assert any(e.startswith("LLM_ROLE_EXTRACTION_BASE_URL must not point at") for e in public.production_runtime_errors())
    missing_model = _settings(llm_role_admin_provider="chat_compatible", llm_role_admin_base_url="http://10.0.0.5:8050/v1", llm_model="")
    assert "LLM_ROLE_ADMIN_MODEL is required" in missing_model.production_runtime_errors()


def test_factory_builds_role_clients_and_role_helper_returns_none_without_override() -> None:
    settings = _settings(
        llm_role_extraction_provider="chat_compatible",
        llm_role_extraction_model="nvidia/nemotron-3.5-lightning-30b-a3b",
        llm_role_extraction_base_url="http://10.0.0.5:8050/v1",
        llm_role_extraction_schema_mode="nvext_guided_json",
        llm_role_extraction_api_key="ngc",
    )
    with patch("src.integrations.llm.get_settings", return_value=settings):
        customer = get_llm_client()
        extraction = get_llm_client("extraction")
        assert isinstance(customer, OllamaLLMClient) and customer.model == "qwen3:8b"
        assert isinstance(extraction, ChatCompletionsLLMClient)
        assert extraction.api_url == "http://10.0.0.5:8050/v1/chat/completions"
        assert extraction.schema_mode == "nvext_guided_json" and extraction.api_key == "ngc"
        assert role_llm_client("generation") is None
        assert isinstance(role_llm_client("extraction"), ChatCompletionsLLMClient)


@respx.mock
async def test_nvext_guided_json_replaces_response_format() -> None:
    route = respx.post("http://10.0.0.5:8050/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
    )
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    nim = ChatCompletionsLLMClient(base_url="http://10.0.0.5:8050/v1", model="m", schema_mode="nvext_guided_json")
    assert await nim.complete([LLMMessage(role="user", content="x")], response_schema=schema) == '{"ok": true}'
    body = json.loads(route.calls.last.request.content)
    assert body["nvext"] == {"guided_json": schema} and "response_format" not in body

    openai_style = ChatCompletionsLLMClient(base_url="http://10.0.0.5:8050/v1", model="m")
    await openai_style.complete([LLMMessage(role="user", content="x")], response_schema=schema)
    body = json.loads(route.calls.last.request.content)
    assert body["response_format"]["json_schema"]["schema"] == schema and "nvext" not in body
