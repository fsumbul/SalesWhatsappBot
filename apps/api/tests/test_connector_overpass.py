"""Mock-based tests for OverpassConnector — no live OSM API calls."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from httpx import Response

from src.integrations.overpass import _ENDPOINTS, OverpassConnector


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.integrations.rate_limit.TokenBucket.acquire", AsyncMock(return_value=0.0)
    )


@respx.mock
async def test_parses_elements_into_raw_leads() -> None:
    respx.post(_ENDPOINTS[0]).mock(
        return_value=Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "id": 12345,
                        "tags": {
                            "name": "Acme Elevator Sheave",
                            "website": "https://acme-elevator.example.com",
                            "phone": "+905321234567",
                            "addr:city": "Istanbul",
                            "addr:street": "Main St",
                            "addr:housenumber": "1",
                        },
                    }
                ]
            },
        )
    )

    conn = OverpassConnector()
    results = [r async for r in conn.search("query", "TR", "tr")]

    assert len(results) == 1
    lead = results[0]
    assert lead.company_name == "Acme Elevator Sheave"
    assert lead.website == "https://acme-elevator.example.com"
    assert lead.phones == ["+905321234567"]
    assert lead.city == "Istanbul"
    assert lead.address == "Main St, 1, Istanbul"
    assert lead.source_url == "https://www.openstreetmap.org/node/12345"


@respx.mock
async def test_elements_without_name_are_skipped() -> None:
    respx.post(_ENDPOINTS[0]).mock(
        return_value=Response(200, json={"elements": [{"type": "node", "id": 1, "tags": {}}]})
    )
    conn = OverpassConnector()
    results = [r async for r in conn.search("query", "TR", "tr")]
    assert results == []


@respx.mock
async def test_duplicate_names_deduplicated() -> None:
    respx.post(_ENDPOINTS[0]).mock(
        return_value=Response(
            200,
            json={
                "elements": [
                    {"type": "node", "id": 1, "tags": {"name": "Acme Co"}},
                    {"type": "node", "id": 2, "tags": {"name": "acme co"}},
                ]
            },
        )
    )
    conn = OverpassConnector()
    results = [r async for r in conn.search("query", "TR", "tr")]
    assert len(results) == 1


@respx.mock
async def test_first_mirror_failure_falls_back_to_second() -> None:
    respx.post(_ENDPOINTS[0]).mock(return_value=Response(503))
    respx.post(_ENDPOINTS[1]).mock(
        return_value=Response(
            200,
            json={"elements": [{"type": "node", "id": 1, "tags": {"name": "Acme Co"}}]},
        )
    )
    conn = OverpassConnector()
    results = [r async for r in conn.search("query", "TR", "tr")]
    assert len(results) == 1
    assert results[0].company_name == "Acme Co"


@respx.mock
async def test_both_mirrors_failing_yields_nothing_not_raises() -> None:
    respx.post(_ENDPOINTS[0]).mock(side_effect=httpx.ConnectTimeout("timed out"))
    respx.post(_ENDPOINTS[1]).mock(side_effect=httpx.ConnectTimeout("timed out"))
    conn = OverpassConnector()
    results = [r async for r in conn.search("query", "TR", "tr")]
    assert results == []
