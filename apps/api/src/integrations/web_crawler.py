"""Generic keyword-driven web crawler connector (Phase D).

Unlike the API-backed connectors (Google Places, SerpAPI, Bing, Overpass),
this one drives a real headless browser (Playwright) against operator-
configured sites, so it carries anti-ban and compliance obligations the
others don't need:

  - robots.txt is checked before every single fetch (src.core.robots),
    with no override — a site that disallows crawling is never crawled.
    This is a hard requirement, not a best-effort check.
  - Per-connector rate limiting via the same TokenBucket every other
    connector uses, plus a randomized human-like delay between page visits.
  - No default seed sites are shipped. The original roadmap named Kompass
    and Europages as example directories, but their own robots.txt disallow
    generic crawlers (Europages: blanket `Disallow: /` for `User-agent: *`)
    or block the paths that actually matter (Kompass: `Disallow: /c/` —
    the company-profile namespace — and `Disallow: /search*`). Hardcoding
    either would mean either violating "zero robots.txt violations" or
    scraping nothing useful. Operators must configure
    `WEB_CRAWL_SEED_URLS` with sites they've confirmed permit crawling.

Off by default (`WEB_CRAWL_ENABLED`); `search()` yields nothing when
disabled or unconfigured — the same defensive pattern every other
connector already uses when its API key is missing.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import structlog
from playwright.async_api import Browser, async_playwright

from src.core.config import get_settings
from src.core.robots import is_allowed
from src.core.text_extract import EMAIL_RE, PHONE_RE, strip_html

from .base import RawLead
from .rate_limit import TokenBucket

logger = structlog.get_logger(__name__)

_PAGE_TIMEOUT_MS = 15_000
_MAX_CANDIDATES_PER_SEED = 15
_MIN_DELAY_S = 1.0
_MAX_DELAY_S = 3.0
_USER_AGENT = "LeadPulseBot/1.0"

# Paths that are never genuine business listings — filters nav/legal/social
# noise out of links extracted from a directory/search page.
_SKIP_PATH_RE = re.compile(
    r"/(login|logout|register|signin|signup|privacy|terms|cookies?|about|"
    r"contact-us|help|faq|sitemap|legal|imprint)(/|$)",
    re.IGNORECASE,
)
_SKIP_HOST_RE = re.compile(
    r"(^|\.)(facebook|twitter|x|linkedin|instagram|youtube|google|"
    r"apple|microsoft)\.com$",
    re.IGNORECASE,
)


class WebCrawlerConnector:
    name = "web_crawl"

    def __init__(self) -> None:
        s = get_settings()
        self.enabled = s.web_crawl_enabled
        self.seed_templates = s.web_crawl_seed_urls_list
        proxies = s.web_crawl_proxies_list
        self._proxies = itertools.cycle(proxies) if proxies else None
        self._bucket = TokenBucket("web_crawl", capacity=2, refill_per_sec=0.2)

    async def search(
        self, query: str, country: str, language: str
    ) -> AsyncIterator[RawLead]:
        if not self.enabled or not self.seed_templates:
            logger.debug("web_crawl_disabled_or_unconfigured")
            return

        seed_urls = [t.replace("{query}", quote_plus(query)) for t in self.seed_templates]

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                for seed_url in seed_urls:
                    async for lead in self._crawl_seed(browser, seed_url, country):
                        yield lead
            finally:
                await browser.close()

    async def _crawl_seed(
        self, browser: Browser, seed_url: str, country: str
    ) -> AsyncIterator[RawLead]:
        if not await is_allowed(seed_url, _USER_AGENT):
            logger.info("web_crawl_seed_disallowed_by_robots", url=seed_url)
            return

        html = await self._fetch(browser, seed_url)
        if html is None:
            return

        candidates = _extract_candidate_links(html, seed_url)[:_MAX_CANDIDATES_PER_SEED]
        for url in candidates:
            if not await is_allowed(url, _USER_AGENT):
                continue
            wait = await self._bucket.acquire()
            if wait > 0:
                await asyncio.sleep(wait)
            await asyncio.sleep(random.uniform(_MIN_DELAY_S, _MAX_DELAY_S))  # noqa: S311 - pacing jitter, not security

            page_html = await self._fetch(browser, url)
            if page_html is None:
                continue
            lead = _extract_lead_from_page(page_html, url, country, source=self.name)
            if lead is not None:
                yield lead

    async def _fetch(self, browser: Browser, url: str) -> str | None:
        context_kwargs: dict[str, Any] = {"user_agent": _USER_AGENT}
        if self._proxies is not None:
            context_kwargs["proxy"] = {"server": next(self._proxies)}
        context = await browser.new_context(**context_kwargs)
        try:
            page = await context.new_page()
            await page.goto(url, timeout=_PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
            return await page.content()
        except Exception as e:  # one bad page must not kill the whole crawl
            logger.warning("web_crawl_fetch_failed", url=url, error=str(e))
            return None
        finally:
            await context.close()


def _extract_candidate_links(html: str, base_url: str) -> list[str]:
    """Pure, browser-free: pull plausible listing links out of a seed page's
    HTML. Regex-based (not a full DOM parser) — same style as the Bing
    free-scrape connector, deliberately avoiding a new parsing dependency."""
    seen: set[str] = set()
    out: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE):
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if _SKIP_PATH_RE.search(parsed.path):
            continue
        if _SKIP_HOST_RE.search(parsed.netloc):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _extract_lead_from_page(
    html: str, url: str, country: str, *, source: str
) -> RawLead | None:
    """Pure, browser-free: extract a RawLead from a rendered page's HTML."""
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    name = strip_html(h1_match.group(1)).strip() if h1_match else ""
    if not name and title_match:
        name = strip_html(title_match.group(1)).strip()
    if not name:
        return None

    text = strip_html(html)
    emails = EMAIL_RE.findall(text)
    phones = PHONE_RE.findall(text)

    return RawLead(
        company_name=name[:255],
        source=source,
        source_url=url,
        website=url,
        country=country,
        phones=phones[:3],
        emails=emails[:3],
        raw={"url": url},
    )
