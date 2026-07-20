"""İYS (İleti Yönetim Sistemi) — Turkey commercial-message registry stub.

Real integration requires an İYS Marka partner account. This client returns
`unknown` (i.e. allow with caution) when unconfigured; production must wire
real credentials + Redis 24h cache.
"""

from __future__ import annotations

from enum import StrEnum

import structlog

from src.core.config import get_settings

logger = structlog.get_logger(__name__)


class IYSDecision(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class IYSClient:
    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.iys_api_url
        self.api_key = s.iys_api_key

    async def check(self, phone_e164: str) -> IYSDecision:
        if not self.base_url or not self.api_key:
            logger.debug("iys_not_configured", phone=phone_e164)
            return IYSDecision.UNKNOWN
        # Real HTTP call omitted — reserved for production integration.
        # NEVER default to ALLOWED without a real check for TR numbers.
        return IYSDecision.UNKNOWN
