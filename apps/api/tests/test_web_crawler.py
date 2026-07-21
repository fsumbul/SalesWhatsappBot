"""Unit tests for WebCrawlerConnector's pure, browser-free parsing logic
(_extract_candidate_links, _extract_lead_from_page) and its config-gated
search() no-op behavior. A real end-to-end smoke test (actual Chromium,
actual robots.txt enforcement) was run manually against a local test
server during development — see the Phase D scaffolding notes — but isn't
part of the automated suite since it needs Playwright's browser binaries
installed (`playwright install chromium`), which CI doesn't yet do.
"""

from __future__ import annotations

from unittest.mock import patch

from src.integrations.web_crawler import (
    WebCrawlerConnector,
    _extract_candidate_links,
    _extract_lead_from_page,
)


class TestExtractCandidateLinks:
    def test_extracts_relative_and_absolute_links(self) -> None:
        html = """
        <a href="/candidate1.html">A</a>
        <a href="https://example.com/candidate2.html">B</a>
        """
        links = _extract_candidate_links(html, "https://example.com/seed.html")
        assert "https://example.com/candidate1.html" in links
        assert "https://example.com/candidate2.html" in links

    def test_filters_login_and_legal_paths(self) -> None:
        html = """
        <a href="/login">Login</a>
        <a href="/privacy">Privacy</a>
        <a href="/terms">Terms</a>
        <a href="/company/acme">Acme</a>
        """
        links = _extract_candidate_links(html, "https://example.com/")
        assert links == ["https://example.com/company/acme"]

    def test_filters_social_media_hosts(self) -> None:
        html = """
        <a href="https://facebook.com/somepage">FB</a>
        <a href="https://linkedin.com/company/acme">LI</a>
        <a href="https://acme-elevator.example.com/">Real site</a>
        """
        links = _extract_candidate_links(html, "https://example.com/")
        assert links == ["https://acme-elevator.example.com/"]

    def test_filters_non_http_schemes(self) -> None:
        html = '<a href="mailto:info@acme.com">Email</a><a href="/page">Page</a>'
        links = _extract_candidate_links(html, "https://example.com/")
        assert links == ["https://example.com/page"]

    def test_deduplicates_repeated_links(self) -> None:
        html = '<a href="/page">One</a><a href="/page">Two</a>'
        links = _extract_candidate_links(html, "https://example.com/")
        assert links == ["https://example.com/page"]

    def test_empty_html_yields_no_links(self) -> None:
        assert _extract_candidate_links("<html><body>no links</body></html>", "https://x.com/") == []


class TestExtractLeadFromPage:
    def test_prefers_h1_over_title(self) -> None:
        html = "<html><head><title>Site Title</title></head><body><h1>Acme Co</h1></body></html>"
        lead = _extract_lead_from_page(html, "https://acme.example.com", "TR", source="web_crawl")
        assert lead is not None
        assert lead.company_name == "Acme Co"

    def test_falls_back_to_title_when_no_h1(self) -> None:
        html = "<html><head><title>Acme Co - Home</title></head><body>text</body></html>"
        lead = _extract_lead_from_page(html, "https://acme.example.com", "TR", source="web_crawl")
        assert lead is not None
        assert lead.company_name == "Acme Co - Home"

    def test_no_name_found_returns_none(self) -> None:
        html = "<html><body><p>no title or h1 here</p></body></html>"
        assert _extract_lead_from_page(html, "https://x.com", "TR", source="web_crawl") is None

    def test_extracts_phone_and_email_from_page_text(self) -> None:
        html = (
            "<html><body><h1>Acme Co</h1>"
            "<p>Contact: info@acme.com or +90 532 123 45 67</p></body></html>"
        )
        lead = _extract_lead_from_page(html, "https://acme.example.com", "TR", source="web_crawl")
        assert lead is not None
        assert lead.emails == ["info@acme.com"]
        assert lead.phones == ["+90 532 123 45 67"]

    def test_sets_source_and_website_and_country(self) -> None:
        html = "<html><body><h1>Acme Co</h1></body></html>"
        lead = _extract_lead_from_page(html, "https://acme.example.com/page", "DE", source="web_crawl")
        assert lead is not None
        assert lead.source == "web_crawl"
        assert lead.website == "https://acme.example.com/page"
        assert lead.source_url == "https://acme.example.com/page"
        assert lead.country == "DE"

    def test_name_truncated_to_255_chars(self) -> None:
        html = f"<html><body><h1>{'x' * 300}</h1></body></html>"
        lead = _extract_lead_from_page(html, "https://x.com", "TR", source="web_crawl")
        assert lead is not None
        assert len(lead.company_name) == 255


class TestSearchGating:
    async def test_disabled_by_default_yields_nothing(self) -> None:
        with patch("src.integrations.web_crawler.get_settings") as mock_settings:
            mock_settings.return_value.web_crawl_enabled = False
            mock_settings.return_value.web_crawl_seed_urls_list = []
            mock_settings.return_value.web_crawl_proxies_list = []
            conn = WebCrawlerConnector()
            results = [r async for r in conn.search("query", "TR", "tr")]
            assert results == []

    async def test_enabled_but_no_seeds_yields_nothing(self) -> None:
        with patch("src.integrations.web_crawler.get_settings") as mock_settings:
            mock_settings.return_value.web_crawl_enabled = True
            mock_settings.return_value.web_crawl_seed_urls_list = []
            mock_settings.return_value.web_crawl_proxies_list = []
            conn = WebCrawlerConnector()
            results = [r async for r in conn.search("query", "TR", "tr")]
            assert results == []
