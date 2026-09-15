"""Same-host website snapshotter for a tenant's own site.

Politeness is not optional even for "our own" site: robots.txt is checked per
URL (fail-closed, ``src/core/robots.py``), fetches are paced, and the crawl
is capped by pages and depth. Only HTML on the registered host (www/non-www
variants) is followed; assets, downloads and off-site links are ignored.
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import structlog

from src.core.robots import is_allowed

from .chunking import content_hash
from .extract import PageExtract, html_to_page

logger = structlog.get_logger(__name__)

_MAX_HTML_BYTES = 2 * 1024 * 1024
_TIMEOUT = httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=8.0)
_SKIP_EXTENSIONS = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".zip",
    ".rar",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".mp4",
    ".mp3",
    ".css",
    ".js",
    ".ico",
    ".xml",
    ".json",
    ".woff",
    ".woff2",
)
_SKIP_PATH_RE = re.compile(
    r"/(wp-admin|wp-login|cart|sepet|checkout|login|giris|logout|account|hesap|search|ara\b|tag/|etiket/|feed)",
    re.IGNORECASE,
)
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}
_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


@dataclass(frozen=True)
class CrawlPage:
    url: str
    depth: int
    page: PageExtract | None
    content_hash: str | None
    status_code: int | None
    error: str | None = None
    discovered: tuple[str, ...] = field(default_factory=tuple)


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k not in _TRACKING_PARAMS
        ]
    )
    return urlunparse((scheme, netloc, path, "", query, ""))


def _host_key(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def same_site(root: str, candidate: str) -> bool:
    return _host_key(urlparse(root).netloc) == _host_key(urlparse(candidate).netloc)


def _crawlable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
        return False
    return not _SKIP_PATH_RE.search(parsed.path)


class WebsiteCrawler:
    def __init__(
        self,
        *,
        user_agent: str = "LeadPulseBot/1.0",
        max_pages: int = 60,
        max_depth: int = 3,
        min_delay_s: float = 0.6,
        max_delay_s: float = 1.6,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.min_delay_s = min_delay_s
        self.max_delay_s = max_delay_s
        self._client = client

    async def _fetch(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[int | None, str | None, str | None]:
        try:
            response = await client.get(
                url, headers={"User-Agent": self.user_agent, "Accept": "text/html,*/*;q=0.5"}
            )
        except httpx.HTTPError as exc:
            return None, None, type(exc).__name__
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            return response.status_code, None, f"http {response.status_code}"
        if "html" not in content_type.lower():
            return response.status_code, None, f"skipped content-type {content_type[:40]}"
        body = response.content[:_MAX_HTML_BYTES]
        return (
            response.status_code,
            body.decode(response.encoding or "utf-8", errors="replace"),
            None,
        )

    async def _sitemap_urls(self, client: httpx.AsyncClient, root: str) -> list[str]:
        sitemap = urljoin(root, "/sitemap.xml")
        if not await is_allowed(sitemap, self.user_agent):
            return []
        try:
            response = await client.get(sitemap, headers={"User-Agent": self.user_agent})
        except httpx.HTTPError:
            return []
        if response.status_code != 200:
            return []
        found = [normalize_url(u) for u in _SITEMAP_LOC_RE.findall(response.text)]
        # A sitemap index lists more sitemaps; expand one level.
        nested: list[str] = []
        for entry in found[:20]:
            if entry.lower().endswith(".xml") and same_site(root, entry):
                try:
                    inner = await client.get(entry, headers={"User-Agent": self.user_agent})
                except httpx.HTTPError:
                    continue
                if inner.status_code == 200:
                    nested.extend(normalize_url(u) for u in _SITEMAP_LOC_RE.findall(inner.text))
        return [u for u in [*found, *nested] if same_site(root, u) and _crawlable(u)]

    async def crawl(self, start_url: str) -> AsyncIterator[CrawlPage]:
        root = normalize_url(start_url)
        client = self._client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
        owns_client = self._client is None
        try:
            queue: list[tuple[str, int]] = [(root, 0)]
            for url in await self._sitemap_urls(client, root):
                queue.append((url, 1))
            seen: set[str] = set()
            fetched = 0
            while queue and fetched < self.max_pages:
                url, depth = queue.pop(0)
                if (
                    url in seen
                    or depth > self.max_depth
                    or not same_site(root, url)
                    or not _crawlable(url)
                ):
                    continue
                seen.add(url)
                if not await is_allowed(url, self.user_agent):
                    logger.info("knowledge.crawl.disallowed", url=url)
                    continue
                await asyncio.sleep(random.uniform(self.min_delay_s, self.max_delay_s))  # noqa: S311 - pacing jitter
                status, html, error = await self._fetch(client, url)
                fetched += 1
                if html is None:
                    yield CrawlPage(
                        url=url,
                        depth=depth,
                        page=None,
                        content_hash=None,
                        status_code=status,
                        error=error,
                    )
                    continue
                page = html_to_page(url, html)
                discovered = tuple(
                    dict.fromkeys(
                        normalize_url(link)
                        for link in page.links
                        if same_site(root, link) and _crawlable(normalize_url(link))
                    )
                )
                for link in discovered:
                    if link not in seen:
                        queue.append((link, depth + 1))
                yield CrawlPage(
                    url=url,
                    depth=depth,
                    page=page,
                    content_hash=content_hash(page.text),
                    status_code=status,
                    discovered=discovered,
                )
        finally:
            if owns_client:
                await client.aclose()
