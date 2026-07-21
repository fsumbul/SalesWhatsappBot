"""Mock-based tests for BingSearchConnector — both the paid API path and the
free HTML-scrape path, since the latter's regex parsing is the most fragile
code in this connector and deserves real coverage."""

# Turkish query text and real Bing separator punctuation (en dash, angle
# quote) below are the actual thing under test — not typos.
# ruff: noqa: RUF001

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from httpx import Response

from src.integrations.bing import (
    BingSearchConnector,
    _blacklisted,
    _cite_to_url,
    _clean_title,
    _valid_host,
)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.integrations.rate_limit.TokenBucket.acquire", AsyncMock(return_value=0.0)
    )
    # Free-path pacing lock/global also gate on real time — reset & bypass.
    monkeypatch.setattr("src.integrations.bing._MIN_INTERVAL_S", 0.0)


@respx.mock
async def test_api_path_parses_webpages_into_raw_leads() -> None:
    conn = BingSearchConnector()
    conn.key = "test-key"
    respx.get("https://api.bing.microsoft.com/v7.0/search").mock(
        return_value=Response(
            200,
            json={
                "webPages": {
                    "value": [
                        {
                            "name": "Acme Elevator Sheave – Home",
                            "url": "https://acme-elevator.example.com",
                            "snippet": "Elevator sheave manufacturer.",
                        }
                    ]
                }
            },
        )
    )

    results = [r async for r in conn.search("asansör kasnağı", "TR", "tr")]

    assert len(results) == 1
    lead = results[0]
    assert lead.company_name == "Acme Elevator Sheave"
    assert lead.website == "https://acme-elevator.example.com"
    assert lead.address == "Elevator sheave manufacturer."


@respx.mock
async def test_api_path_non_200_yields_nothing() -> None:
    conn = BingSearchConnector()
    conn.key = "test-key"
    respx.get("https://api.bing.microsoft.com/v7.0/search").mock(return_value=Response(401))
    results = [r async for r in conn.search("query", "TR", "tr")]
    assert results == []


@respx.mock
async def test_api_path_network_error_yields_nothing_not_raises() -> None:
    conn = BingSearchConnector()
    conn.key = "test-key"
    respx.get("https://api.bing.microsoft.com/v7.0/search").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    results = [r async for r in conn.search("query", "TR", "tr")]
    assert results == []


_FREE_HTML_FIXTURE = """
<html><body>
<ol id="b_results">
<li class="b_algo">
  <h2><a href="https://acme-elevator.example.com/">Acme Elevator Sheave Co</a></h2>
  <div class="b_caption">
    <cite>acme-elevator.example.com</cite>
    <p>We manufacture <strong>elevator</strong> sheaves for the Turkish market.</p>
  </div>
</li>
<li class="b_algo">
  <h2><a href="https://www.wikipedia.org/wiki/Elevator">Elevator - Wikipedia</a></h2>
  <div class="b_caption">
    <cite>wikipedia.org</cite>
    <p>An elevator is a type of vertical transport.</p>
  </div>
</li>
</ol>
</body></html>
"""


@respx.mock
async def test_free_path_parses_html_and_filters_blacklisted_hosts() -> None:
    conn = BingSearchConnector()
    conn.key = ""
    respx.get("https://www.bing.com/search").mock(
        return_value=Response(200, text=_FREE_HTML_FIXTURE)
    )

    results = [r async for r in conn.search("elevator sheave", "TR", "en")]

    # wikipedia.org is blacklisted, so only the real business result survives.
    assert len(results) == 1
    lead = results[0]
    assert lead.company_name == "Acme Elevator Sheave Co"
    assert lead.website == "https://acme-elevator.example.com"
    assert "elevator" in (lead.address or "").lower()


@respx.mock
async def test_free_path_non_200_yields_nothing() -> None:
    conn = BingSearchConnector()
    conn.key = ""
    respx.get("https://www.bing.com/search").mock(return_value=Response(429, text="blocked"))
    results = [r async for r in conn.search("query", "TR", "en")]
    assert results == []


class TestCleanTitle:
    def test_strips_separator_and_unescapes_entities(self) -> None:
        assert _clean_title("Acme &amp; Co | Products") == "Acme & Co"


class TestCiteToUrl:
    def test_adds_scheme_when_missing(self) -> None:
        assert _cite_to_url("acme-elevator.example.com") == "https://acme-elevator.example.com"

    def test_takes_host_before_breadcrumb_separator(self) -> None:
        assert _cite_to_url("acme-elevator.example.com › products") == (
            "https://acme-elevator.example.com"
        )

    def test_empty_input_returns_none(self) -> None:
        assert _cite_to_url("") is None


class TestValidHost:
    def test_rejects_empty_or_spaced_host(self) -> None:
        assert _valid_host("") is False
        assert _valid_host("www. company.com") is False

    def test_accepts_normal_host(self) -> None:
        assert _valid_host("acme-elevator.example.com") is True


class TestBlacklisted:
    def test_exact_match(self) -> None:
        assert _blacklisted("wikipedia.org") is True

    def test_subdomain_match(self) -> None:
        assert _blacklisted("tr.wikipedia.org") is True

    def test_unrelated_host_not_blacklisted(self) -> None:
        assert _blacklisted("acme-elevator.example.com") is False
