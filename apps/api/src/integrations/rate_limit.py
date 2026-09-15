"""Redis-backed token bucket for bounded connector and private-API requests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, ClassVar, cast

import redis.asyncio as aioredis

from src.core.config import get_settings


class TokenBucket:
    """Simple async token-bucket implemented in Redis.

    The refill/debit transition is one Redis Lua command. This matters now
    that login, chat, workflow and import routes share the primitive: a burst
    of concurrent requests must not all observe the same pre-debit balance.
    """

    _ACQUIRE_SCRIPT = """
local raw = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local cost = tonumber(ARGV[3])
local tokens = tonumber(raw[1]) or capacity
local last = tonumber(raw[2]) or now
tokens = math.min(capacity, tokens + (now - last) * refill)
local wait = 0
if tokens < cost then
  wait = (cost - tokens) / refill
else
  tokens = tokens - cost
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], 3600)
return tostring(wait)
"""
    _clients: ClassVar[dict[str, aioredis.Redis]] = {}

    def __init__(self, key: str, capacity: int, refill_per_sec: float) -> None:
        self.key = f"tb:{key}"
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec

    async def _r(self) -> aioredis.Redis:
        url = str(get_settings().redis_url)
        client = self._clients.get(url)
        if client is None:
            factory: Any = aioredis.from_url
            client = cast(aioredis.Redis, factory(url, decode_responses=True))
            self._clients[url] = client
        return client

    @classmethod
    async def close_clients(cls) -> None:
        """Release the shared Redis pools on application shutdown."""

        clients = list(cls._clients.values())
        cls._clients.clear()
        if clients:
            await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)

    async def acquire(self, cost: int = 1) -> float:
        """Return seconds the caller should wait before proceeding (0 if immediate)."""

        if cost <= 0 or self.capacity <= 0 or self.refill_per_sec <= 0:
            raise ValueError("Token bucket capacity, refill rate and cost must be positive.")
        r = await self._r()
        result = await cast(
            Awaitable[str],
            r.eval(
                self._ACQUIRE_SCRIPT,
                1,
                self.key,
                str(self.capacity),
                str(self.refill_per_sec),
                str(cost),
            ),
        )
        return float(result)


async def close_token_bucket_clients() -> None:
    """Lifecycle-friendly facade that avoids exporting cache internals."""

    await TokenBucket.close_clients()
