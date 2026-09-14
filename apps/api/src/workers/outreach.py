"""Legacy beat entry point, now draining only explicitly approved chat batches.

Historical outreach_jobs are preserved for delivery receipts/audit and never replayed.
"""

from typing import Any

from sqlalchemy import select

from src.core.celery_app import celery_app
from src.core.db import session_scope
from src.modules.auth.models import Tenant, TenantStatus
from src.workers.chat_outbound import dispatch

from ._asyncrun import run_async


@celery_app.task(name="src.workers.outreach.dispatch_outreach")
def dispatch_outreach() -> dict[str, Any]:
    return run_async(_dispatch())


async def _dispatch() -> dict[str, Any]:
    async with session_scope() as db:
        tenants = list(
            (await db.scalars(select(Tenant.id).where(Tenant.status == TenantStatus.ACTIVE))).all()
        )
    for tenant_id in tenants:
        await dispatch(tenant_id)
    return {"tenants": len(tenants)}
