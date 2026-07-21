"""Tests for IYSClient — currently a deterministic stub (no live HTTP calls
exist yet to mock). These pin today's documented behavior ("never default
to ALLOWED without a real check") so implementing the real integration
later forces a conscious test update rather than a silent behavior change.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.integrations.iys import IYSClient, IYSDecision


def _client_with_settings(*, url: str, key: str) -> IYSClient:
    fake_settings = SimpleNamespace(iys_api_url=url, iys_api_key=key)
    with patch("src.integrations.iys.get_settings", return_value=fake_settings):
        return IYSClient()


async def test_unconfigured_returns_unknown() -> None:
    client = _client_with_settings(url="", key="")
    assert await client.check("+905321234567") == IYSDecision.UNKNOWN


async def test_missing_api_key_returns_unknown() -> None:
    client = _client_with_settings(url="https://iys.example.com", key="")
    assert await client.check("+905321234567") == IYSDecision.UNKNOWN


async def test_missing_base_url_returns_unknown() -> None:
    client = _client_with_settings(url="", key="test-key")
    assert await client.check("+905321234567") == IYSDecision.UNKNOWN


async def test_configured_still_returns_unknown_stub_never_allows() -> None:
    """Documents current behavior: even fully configured, the stub never
    returns ALLOWED — real HTTP integration is not implemented yet."""
    client = _client_with_settings(url="https://iys.example.com", key="test-key")
    decision = await client.check("+905321234567")
    assert decision == IYSDecision.UNKNOWN
    assert decision != IYSDecision.ALLOWED
