"""Mock-based tests for GooglePlacesConnector — no live API calls.

Uses respx to intercept httpx traffic with recorded-shape fixtures, and
monkeypatches TokenBucket.acquire so these tests don't need live Redis.
"""

# Turkish query text below is the actual thing under test — not a typo.
# ruff: noqa: RUF001

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import respx
from httpx import Response

from src.integrations.google_places import _ENDPOINT, GooglePlacesConnector, _find_city


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.integrations.rate_limit.TokenBucket.acquire", AsyncMock(return_value=0.0)
    )


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> GooglePlacesConnector:
    conn = GooglePlacesConnector()
    conn.api_key = "test-key"
    return conn


async def test_no_api_key_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = GooglePlacesConnector()
    conn.api_key = ""
    results = [r async for r in conn.search("asansör kasnağı", "TR", "tr")]
    assert results == []


@respx.mock
async def test_parses_places_response_into_raw_leads(connector: GooglePlacesConnector) -> None:
    respx.post(_ENDPOINT).mock(
        return_value=Response(
            200,
            json={
                "places": [
                    {
                        "id": "place123",
                        "displayName": {"text": "Acme Elevator Sheave Ltd"},
                        "formattedAddress": "123 Main St, Istanbul",
                        "internationalPhoneNumber": "+90 532 123 45 67",
                        "websiteUri": "https://acme-elevator.example.com",
                        "addressComponents": [
                            {"types": ["locality"], "longText": "Istanbul"},
                        ],
                    }
                ]
            },
        )
    )

    results = [r async for r in connector.search("asansör kasnağı", "TR", "tr")]

    assert len(results) == 1
    lead = results[0]
    assert lead.company_name == "Acme Elevator Sheave Ltd"
    assert lead.source == "google_places"
    assert lead.website == "https://acme-elevator.example.com"
    assert lead.city == "Istanbul"
    assert lead.phones == ["+90 532 123 45 67"]
    assert lead.source_url == "https://www.google.com/maps/place/?q=place_id:place123"


@respx.mock
async def test_missing_optional_fields_default_gracefully(
    connector: GooglePlacesConnector,
) -> None:
    respx.post(_ENDPOINT).mock(
        return_value=Response(200, json={"places": [{"id": "place456"}]})
    )

    results = [r async for r in connector.search("query", "DE", "de")]

    assert len(results) == 1
    lead = results[0]
    assert lead.company_name == "Unknown"
    assert lead.phones == []
    assert lead.website is None


@respx.mock
async def test_non_200_response_yields_nothing(connector: GooglePlacesConnector) -> None:
    respx.post(_ENDPOINT).mock(return_value=Response(429, text="rate limited"))
    results = [r async for r in connector.search("query", "TR", "tr")]
    assert results == []


@respx.mock
async def test_network_error_yields_nothing_not_raises(connector: GooglePlacesConnector) -> None:
    import httpx

    respx.post(_ENDPOINT).mock(side_effect=httpx.ConnectTimeout("timed out"))
    results = [r async for r in connector.search("query", "TR", "tr")]
    assert results == []


def test_find_city_prefers_locality() -> None:
    components = [
        {"types": ["country"], "longText": "Turkey"},
        {"types": ["locality"], "longText": "Ankara"},
    ]
    assert _find_city(components) == "Ankara"


def test_find_city_falls_back_to_admin_area() -> None:
    components = [{"types": ["administrative_area_level_1"], "shortText": "TR-06"}]
    assert _find_city(components) == "TR-06"


def test_find_city_returns_none_when_absent() -> None:
    assert _find_city([{"types": ["country"], "longText": "Turkey"}]) is None
