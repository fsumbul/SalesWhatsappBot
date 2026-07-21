"""Bing web search connector.

If BING_SEARCH_KEY is set → use official Bing Web Search API v7.
Otherwise → scrape the free https://www.bing.com/search endpoint (no key).
The free path is polite (in-process semaphore + inter-request delay) so
we don't get soft-blocked.
"""

# The separator characters below (en dash, angle quote, …) match real
# punctuation Bing uses in titles/breadcrumbs — not typos to "fix".
# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
import html as htmlmod
import re
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx
import structlog

from src.core.config import get_settings

from .base import RawLead
from .rate_limit import TokenBucket

logger = structlog.get_logger(__name__)

_API_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
_FREE_ENDPOINT = "https://www.bing.com/search"

_MKT = {
    ("TR", "tr"): "tr-TR",
    ("TR", "en"): "en-TR",
    ("DE", "de"): "de-DE",
    ("DE", "en"): "en-DE",
    ("GB", "en"): "en-GB",
    ("AE", "ar"): "ar-AE",
    ("AE", "en"): "en-AE",
    ("SA", "ar"): "ar-SA",
    ("SA", "en"): "en-SA",
    ("RU", "ru"): "ru-RU",
}

_FREE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/16.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

_HOST_BLACKLIST = {
    "bing.com", "microsoft.com", "msn.com",
    "wikipedia.org", "youtube.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "linkedin.com", "google.com", "maps.google.com",
    "reddit.com", "amazon.com", "amazon.de", "amazon.co.uk", "ebay.com",
    "sahibinden.com", "yandex.com", "yandex.com.tr", "gittigidiyor.com",
    "n11.com", "hepsiburada.com", "trendyol.com", "pinterest.com",
    "tiktok.com", "medium.com", "quora.com", "yelp.com",
    "armut.com", "aliexpress.com", "alibaba.com", "indiamart.com",
    "made-in-china.com",
}

# Allow a few SERP fetches in flight at once (a single slow response no longer
# stalls the whole discovery batch) while still pacing request *dispatch* so we
# stay polite and avoid soft-blocks.
_FREE_LOCK = asyncio.Semaphore(3)
_PACE_LOCK = asyncio.Lock()
_MIN_INTERVAL_S = 0.5
_last_call_ts: float = 0.0


_BLOCK_RE = re.compile(
    r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>',
    re.IGNORECASE | re.DOTALL,
)
_H2A_RE = re.compile(
    r'<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_CITE_RE = re.compile(r'<cite[^>]*>(.*?)</cite>', re.IGNORECASE | re.DOTALL)
_SNIPPET_RE = re.compile(
    r'<div[^>]*class="[^"]*b_caption[^"]*"[^>]*>.*?<p[^>]*>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def _clean_title(t: str) -> str:
    t = htmlmod.unescape(_strip_html(t))
    for sep in [" | ", " - ", " – ", " — ", " » ", " · "]:
        if sep in t:
            t = t.split(sep)[0].strip()
            break
    return t[:255] or "Unknown"


def _cite_to_url(cite_html: str) -> str | None:
    # Bing bolds matched query terms inside <cite> with <strong>; stripping the
    # tags leaves stray spaces mid-URL. Take the host/path before the first
    # breadcrumb separator and drop internal whitespace so we don't truncate the
    # hostname (e.g. "www. company.com" -> "www.company.com").
    text = _strip_html(cite_html)
    if not text:
        return None
    host = text.split("›")[0].replace(" ", "").strip()
    if not host:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host


def _blacklisted(host: str) -> bool:
    host = host.lower()
    return any(host == b or host.endswith("." + b) for b in _HOST_BLACKLIST)


def _valid_host(host: str) -> bool:
    """Reject malformed hosts like 'www.' from broken cite parsing."""
    if not host or " " in host:
        return False
    parts = host.rsplit(".", 1)
    return len(parts) == 2 and len(parts[1]) >= 2 and parts[1].isalpha()


class BingSearchConnector:
    name = "bing"

    def __init__(self) -> None:
        self.key = get_settings().bing_search_key
        self._bucket = TokenBucket("bing", capacity=3, refill_per_sec=0.3)

    async def search(
        self, query: str, country: str, language: str
    ) -> AsyncIterator[RawLead]:
        if self.key:
            async for r in self._search_api(query, country, language):
                yield r
        else:
            async for r in self._search_free(query, country, language):
                yield r

    async def _search_api(
        self, query: str, country: str, language: str
    ) -> AsyncIterator[RawLead]:
        wait = await self._bucket.acquire()
        if wait > 0:
            await asyncio.sleep(wait)
        headers = {"Ocp-Apim-Subscription-Key": self.key}
        params: dict[str, str | int] = {
            "q": query,
            "mkt": f"{language}-{country.upper()}",
            "count": 20,
            "responseFilter": "Webpages",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(_API_ENDPOINT, headers=headers, params=params)
                if resp.status_code != 200:
                    logger.warning("bing_api_error", status=resp.status_code)
                    return
                data = resp.json()
        except httpx.HTTPError as e:
            logger.warning("bing_api_http_error", error=str(e))
            return

        for w in data.get("webPages", {}).get("value", []):
            yield RawLead(
                company_name=_clean_title(w.get("name") or "Unknown"),
                source=self.name,
                source_url=w.get("url"),
                website=w.get("url"),
                country=country,
                address=(w.get("snippet") or "")[:255] or None,
                raw=w,
            )

    async def _search_free(
        self, query: str, country: str, language: str
    ) -> AsyncIterator[RawLead]:
        global _last_call_ts
        mkt = _MKT.get((country.upper(), language.lower()), "en-US")
        params: dict[str, str | int] = {"q": query, "cc": country.upper(), "count": 30, "mkt": mkt}

        # Pace request dispatch under a short lock, then run the actual fetch
        # concurrently (bounded by _FREE_LOCK) so slow SERP responses overlap
        # instead of serializing the whole discovery run.
        async with _PACE_LOCK:
            now = asyncio.get_event_loop().time()
            wait = max(0.0, _MIN_INTERVAL_S - (now - _last_call_ts))
            if wait > 0:
                await asyncio.sleep(wait)
            _last_call_ts = asyncio.get_event_loop().time()

        async with _FREE_LOCK:
            try:
                async with httpx.AsyncClient(
                    timeout=12.0, headers=_FREE_HEADERS, follow_redirects=True
                ) as client:
                    resp = await client.get(_FREE_ENDPOINT, params=params)
                    if resp.status_code != 200:
                        logger.warning(
                            "bing_free_error",
                            status=resp.status_code,
                            length=len(resp.text),
                        )
                        return
                    html = resp.text
            except httpx.HTTPError as e:
                logger.warning("bing_free_http_error", error=str(e))
                return

        blocks = _BLOCK_RE.findall(html)
        seen_hosts: set[str] = set()
        yielded = 0
        for b in blocks:
            m = _H2A_RE.search(b)
            if not m:
                continue
            title = _clean_title(m.group(2))
            url: str | None = None
            cite_m = _CITE_RE.search(b)
            if cite_m:
                url = _cite_to_url(cite_m.group(1))
            if not url:
                href = m.group(1)
                if href.startswith("http"):
                    url = href
            if not url:
                continue
            host = (urlparse(url).hostname or "").lower()
            if not _valid_host(host) or _blacklisted(host):
                continue
            if host in seen_hosts:
                continue
            seen_hosts.add(host)
            sn = _SNIPPET_RE.search(b)
            snippet = htmlmod.unescape(_strip_html(sn.group(1))) if sn else ""
            yield RawLead(
                company_name=title,
                source=self.name,
                source_url=url,
                website=url,
                country=country.upper(),
                address=snippet[:255] or None,
                raw={
                    "title": title,
                    "snippet": snippet,
                    "mkt": mkt,
                    "query": query,
                },
            )
            yielded += 1
            if yielded >= 15:
                break
