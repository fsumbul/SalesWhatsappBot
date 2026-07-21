"""Tests for the robots.txt compliance checker — no live network calls.

The "fails closed" behavior here is a hard requirement (ROADMAP.md Phase D):
a crawler must never treat an unreachable/unparseable robots.txt as
permission to proceed.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from src.core import robots


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    robots.clear_cache()


@respx.mock
async def test_allows_when_robots_txt_permits() -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=Response(200, text="User-agent: *\nAllow: /\n")
    )
    assert await robots.is_allowed("https://example.com/page") is True


@respx.mock
async def test_disallows_when_robots_txt_forbids_path() -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=Response(200, text="User-agent: *\nDisallow: /private/\n")
    )
    assert await robots.is_allowed("https://example.com/private/page") is False
    assert await robots.is_allowed("https://example.com/public/page") is True


@respx.mock
async def test_missing_robots_txt_404_allows_everything() -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=Response(404))
    assert await robots.is_allowed("https://example.com/anything") is True


@respx.mock
async def test_unexpected_status_fails_closed() -> None:
    respx.get("https://example.com/robots.txt").mock(return_value=Response(503))
    assert await robots.is_allowed("https://example.com/page") is False


@respx.mock
async def test_network_error_fails_closed_not_raises() -> None:
    respx.get("https://example.com/robots.txt").mock(side_effect=httpx.ConnectTimeout("timed out"))
    assert await robots.is_allowed("https://example.com/page") is False


async def test_malformed_url_fails_closed() -> None:
    assert await robots.is_allowed("not-a-url") is False
    assert await robots.is_allowed("") is False


@respx.mock
async def test_repeated_checks_reuse_cached_parser() -> None:
    route = respx.get("https://example.com/robots.txt").mock(
        return_value=Response(200, text="User-agent: *\nAllow: /\n")
    )
    await robots.is_allowed("https://example.com/a")
    await robots.is_allowed("https://example.com/b")
    assert route.call_count == 1


@respx.mock
async def test_different_hosts_fetch_independently() -> None:
    route_a = respx.get("https://a.example.com/robots.txt").mock(
        return_value=Response(200, text="User-agent: *\nAllow: /\n")
    )
    route_b = respx.get("https://b.example.com/robots.txt").mock(
        return_value=Response(200, text="User-agent: *\nDisallow: /\n")
    )
    assert await robots.is_allowed("https://a.example.com/x") is True
    assert await robots.is_allowed("https://b.example.com/x") is False
    assert route_a.call_count == 1
    assert route_b.call_count == 1


@respx.mock
async def test_specific_user_agent_disallow_respected() -> None:
    respx.get("https://example.com/robots.txt").mock(
        return_value=Response(
            200,
            text="User-agent: LeadPulseBot/1.0\nDisallow: /\nUser-agent: *\nAllow: /\n",
        )
    )
    assert await robots.is_allowed("https://example.com/page", user_agent="LeadPulseBot/1.0") is False
