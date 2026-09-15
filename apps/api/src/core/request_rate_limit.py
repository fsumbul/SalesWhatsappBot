"""Small, explicit request limits for the few expensive/mutating private APIs.

This is intentionally not a global middleware or a generic policy framework.
Only login, a chat turn, a workflow action, and campaign-file upload use it.
The existing Redis ``TokenBucket`` remains the single storage implementation.
"""

from __future__ import annotations

import math
from hashlib import sha256

from fastapi import HTTPException
from redis.exceptions import RedisError

from src.core.config import get_settings
from src.integrations.rate_limit import TokenBucket

# capacity, tokens/second.  Chat is intentionally substantially tighter than
# forms because it invokes the self-hosted model; imports are large and put a
# parsing job on the worker. Keys include the authenticated tenant/user where
# possible, so one company's activity cannot drain another's quota.
_LIMITS: dict[str, tuple[int, float]] = {
    # A BFF can be the immediate API peer for many people, so pair a modest
    # shared-source brake with a tighter account key rather than accidentally
    # rate-limiting an office of normal users as one IP.
    "login_source": (60, 60 / 60),
    "login_account": (10, 10 / 60),
    "chat_turn": (20, 20 / 60),
    "workflow_action": (60, 60 / 60),
    "campaign_import": (6, 6 / 60),
}


async def enforce_request_rate_limit(scope: str, subject: str) -> None:
    """Raise a retryable 429 when the named private request exceeds its quota."""

    settings = get_settings()
    if not settings.api_rate_limit_enabled:
        return
    try:
        capacity, refill_per_sec = _LIMITS[scope]
    except KeyError as exc:  # programming error; never create an unbounded bucket
        raise RuntimeError(f"Unknown request rate-limit scope: {scope}") from exc
    # Redis keys are operational data too. Do not persist raw e-mail/IP
    # strings merely to identify a private request bucket.
    safe_subject = sha256(subject.encode("utf-8")).hexdigest()
    try:
        wait = await TokenBucket(
            f"private_api:{scope}:{safe_subject}", capacity=capacity, refill_per_sec=refill_per_sec
        ).acquire()
    except RedisError as exc:
        # A production Redis outage must not convert costly private endpoints
        # into an unbounded abuse path. Operators get a clear 503 instead.
        raise HTTPException(503, "İstek sınırı servisi kullanılamıyor.") from exc
    if wait > 0:
        retry_after = max(1, math.ceil(wait))
        raise HTTPException(
            429,
            "Çok fazla istek. Lütfen kısa süre sonra yeniden deneyin.",
            headers={"Retry-After": str(retry_after)},
        )
