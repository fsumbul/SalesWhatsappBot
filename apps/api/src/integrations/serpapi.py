"""SerpAPI Google Search connector."""

# The separator characters below (en dash, em dash) match real punctuation
# Google uses in result titles — not typos to "fix".
# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import structlog

from src.core.config import get_settings

from .base import RawLead
from .rate_limit import TokenBucket

logger = structlog.get_logger(__name__)


class SerpAPIConnector:
    name = "serpapi"

    def __init__(self) -> None:
        self.key = get_settings().serpapi_key
        self._bucket = TokenBucket("serpapi", capacity=5, refill_per_sec=0.5)

    async def search(
        self, query: str, country: str, language: str
    ) -> AsyncIterator[RawLead]:
        if not self.key:
            logger.warning("serpapi_no_key")
            return
        wait = await self._bucket.acquire()
        if wait > 0:
            await asyncio.sleep(wait)
        params = {
            "engine": "google",
            "q": query,
            "hl": language,
            "gl": country.lower(),
            "num": 20,
            "api_key": self.key,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get("https://serpapi.com/search", params=params)
                if resp.status_code != 200:
                    logger.warning("serpapi_error", status=resp.status_code)
                    return
                data = resp.json()
        except httpx.HTTPError as e:
            logger.warning("serpapi_http_error", error=str(e))
            return

        for item in data.get("organic_results", []):
            title = item.get("title") or "Unknown"
            link = item.get("link")
            snippet = item.get("snippet", "")
            yield RawLead(
                company_name=_clean_title(title),
                source=self.name,
                source_url=link,
                website=link,
                country=country,
                address=snippet[:255],
                raw=item,
            )


def _clean_title(t: str) -> str:
    for sep in [" | ", " - ", " – ", " — "]:
        if sep in t:
            t = t.split(sep)[0]
    return t.strip()[:255]
