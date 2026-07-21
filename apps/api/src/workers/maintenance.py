"""Housekeeping tasks."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import update

from src.core.celery_app import celery_app
from src.core.db import get_sessionmaker
from src.modules.outreach.models import SenderProfile

from ._asyncrun import run_async

logger = structlog.get_logger(__name__)


@celery_app.task(name="src.workers.maintenance.reset_daily_send_counters")
def reset_daily_send_counters() -> dict[str, Any]:
    return run_async(_reset())


async def _reset() -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(update(SenderProfile).values(daily_sent=0))
        await session.commit()
    logger.info("daily_counters_reset")
    return {"ok": True}


@celery_app.task(name="src.workers.maintenance.refresh_iys_cache")
def refresh_iys_cache() -> dict[str, Any]:
    # Real implementation would fetch delta from İYS and update local cache.
    logger.info("iys_cache_refresh_noop")
    return {"ok": True}
