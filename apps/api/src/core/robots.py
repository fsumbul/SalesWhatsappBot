"""robots.txt compliance checker.

Fetches robots.txt asynchronously (httpx) and interprets it with the
stdlib's RobotFileParser in parse-only mode (no blocking I/O — its own
`.read()` would do a synchronous urlopen(), which is why we fetch
ourselves and only use `.parse()`). Per-origin results are cached
in-process for a TTL so a crawl hitting many pages on the same host
doesn't refetch robots.txt every time.

Fails closed on genuine failures (network error, timeout, unexpected
status): "couldn't check" must never be treated as permission to
proceed. A real 404 (no robots.txt at all) is not a failure — it's the
standard web convention for "no restrictions" — so that case allows.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

logger = structlog.get_logger(__name__)

_CACHE_TTL_S = 3600.0
_FETCH_TIMEOUT_S = 8.0

# None means "fetch failed / disallow everything"; a parser with empty
# rules (from a 404) means "no restrictions found".
_cache: dict[str, tuple[RobotFileParser | None, float]] = {}


def clear_cache() -> None:
    """Test hook — production code never needs to call this."""
    _cache.clear()


async def _get_parser(origin: str, user_agent: str) -> RobotFileParser | None:
    cached = _cache.get(origin)
    if cached is not None:
        cached_parser, fetched_at = cached
        if time.monotonic() - fetched_at < _CACHE_TTL_S:
            return cached_parser

    parser: RobotFileParser | None
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.get(f"{origin}/robots.txt", headers={"User-Agent": user_agent})
        if resp.status_code == 404:
            rfp = RobotFileParser()
            rfp.parse([])
            parser = rfp
        elif resp.status_code == 200:
            rfp = RobotFileParser()
            rfp.parse(resp.text.splitlines())
            parser = rfp
        else:
            logger.warning(
                "robots_fetch_unexpected_status", origin=origin, status=resp.status_code
            )
            parser = None
    except httpx.HTTPError as e:
        logger.warning("robots_fetch_error", origin=origin, error=str(e))
        parser = None

    _cache[origin] = (parser, time.monotonic())
    return parser


async def is_allowed(url: str, user_agent: str = "LeadPulseBot/1.0") -> bool:
    """Return True only if robots.txt explicitly permits fetching `url`.

    Fails closed: an unreachable or unparseable robots.txt means False,
    never True. A confirmed-missing robots.txt (404) means True.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    parser = await _get_parser(origin, user_agent)
    if parser is None:
        return False
    return parser.can_fetch(user_agent, url)
