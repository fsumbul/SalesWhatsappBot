"""Redis-backed token bucket for per-connector rate limiting."""

from __future__ import annotations

import time

import redis.asyncio as aioredis

from src.core.config import get_settings


class TokenBucket:
    """Simple async token-bucket implemented in Redis.

    Not perfectly atomic; good enough for connector-level throttling.
    """

    def __init__(self, key: str, capacity: int, refill_per_sec: float) -> None:
        self.key = f"tb:{key}"
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._client: aioredis.Redis | None = None

    async def _r(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(
                str(get_settings().redis_url), decode_responses=True
            )
        return self._client

    async def acquire(self, cost: int = 1) -> float:
        """Return seconds the caller should wait before proceeding (0 if immediate)."""
        r = await self._r()
        now = time.time()
        pipe = r.pipeline()
        pipe.hgetall(self.key)
        state = (await pipe.execute())[0]
        tokens = float(state.get("tokens", self.capacity)) if state else self.capacity
        last = float(state.get("ts", now)) if state else now
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_sec)
        wait = 0.0
        if tokens < cost:
            wait = (cost - tokens) / self.refill_per_sec
            tokens = 0.0
        else:
            tokens -= cost
        await r.hset(self.key, mapping={"tokens": tokens, "ts": now})
        await r.expire(self.key, 3600)
        return wait
