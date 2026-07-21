"""Mock-based tests for SerpAPIConnector — no live API calls."""

# Turkish query text and real separator punctuation (en dash) below are the
# actual thing under test — not typos.
# ruff: noqa: RUF001

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from httpx import Response

from src.integrations.serpapi import SerpAPIConnector, _clean_title


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.integrations.rate_limit.TokenBucket.acquire", AsyncMock(return_value=0.0)
    )


@pytest.fixture
def connector() -> SerpAPIConnector:
    conn = SerpAPIConnector()
    conn.key = "test-key"
    return conn


async def test_no_key_yields_nothing() -> None:
    conn = SerpAPIConnector()
    conn.key = ""
    results = [r async for r in conn.search("query", "TR", "tr")]
    assert results == []


@respx.mock
async def test_parses_organic_results_into_raw_leads(connector: SerpAPIConnector) -> None:
    respx.get("https://serpapi.com/search").mock(
        return_value=Response(
            200,
            json={
                "organic_results": [
                    {
                        "title": "Acme Elevator Sheave - Home",
                        "link": "https://acme-elevator.example.com",
                        "snippet": "We manufacture elevator sheaves in Turkey.",
                    }
                ]
            },
        )
    )

    results = [r async for r in connector.search("asansör kasnağı", "TR", "tr")]

    assert len(results) == 1
    lead = results[0]
    assert lead.company_name == "Acme Elevator Sheave"
    assert lead.source == "serpapi"
    assert lead.website == "https://acme-elevator.example.com"
    assert lead.address == "We manufacture elevator sheaves in Turkey."


@respx.mock
async def test_missing_title_and_link_default_gracefully(connector: SerpAPIConnector) -> None:
    respx.get("https://serpapi.com/search").mock(
        return_value=Response(200, json={"organic_results": [{}]})
    )
    results = [r async for r in connector.search("query", "TR", "tr")]
    assert len(results) == 1
    assert results[0].company_name == "Unknown"
    assert results[0].website is None


@respx.mock
async def test_non_200_response_yields_nothing(connector: SerpAPIConnector) -> None:
    respx.get("https://serpapi.com/search").mock(return_value=Response(403))
    results = [r async for r in connector.search("query", "TR", "tr")]
    assert results == []


@respx.mock
async def test_network_error_yields_nothing_not_raises(connector: SerpAPIConnector) -> None:
    respx.get("https://serpapi.com/search").mock(side_effect=httpx.ConnectTimeout("timed out"))
    results = [r async for r in connector.search("query", "TR", "tr")]
    assert results == []


class TestCleanTitle:
    def test_strips_pipe_separator(self) -> None:
        assert _clean_title("Acme Co | Products") == "Acme Co"

    def test_strips_en_dash_separator(self) -> None:
        assert _clean_title("Acme Co – Products") == "Acme Co"

    def test_no_separator_returns_stripped_input(self) -> None:
        assert _clean_title("  Acme Co  ") == "Acme Co"

    def test_truncates_to_255_chars(self) -> None:
        assert len(_clean_title("x" * 300)) == 255
