"""Tests for the LLM client scaffold. There's no real provider to test
against — these pin the deliberate "raise, don't fabricate" behavior of
NullLLMClient and the factory's default wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.integrations.llm import (
    LLMMessage,
    LLMNotConfiguredError,
    NullLLMClient,
    ChatCompletionsLLMClient,
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
