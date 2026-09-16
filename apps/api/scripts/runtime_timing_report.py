"""Read per-message timings without exposing message bodies or phone numbers."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from src.core.db import session_scope, set_tenant_context
from src.modules.agents.runtime_models import AgentRuntimeJob
from src.modules.auth.models import Tenant


def elapsed_ms(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        value = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
        return round(value, 2) if value >= 0 else None
    except (ValueError, TypeError):
        return None


async def report(tenant_slug: str, limit: int, job_id: UUID | None) -> list[dict]:
    async with session_scope() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one()
        await set_tenant_context(session, tenant.id)
        stmt = select(AgentRuntimeJob).where(AgentRuntimeJob.tenant_id == tenant.id)
        if job_id:
            stmt = stmt.where(AgentRuntimeJob.id == job_id)
        jobs = (await session.execute(stmt.order_by(AgentRuntimeJob.created_at.desc()).limit(limit))).scalars().all()
        results = []
        for job in jobs:
            audit = job.audit or {}
            received = audit.get("webhook_received_at") or job.created_at.isoformat()
            send_end = audit.get("send_completed_at")
            results.append({
                "job_id": str(job.id), "created_at": job.created_at.isoformat(),
                "status": job.status, "action": job.action, "used_fallback": job.used_fallback,
                "response_source": audit.get("response_source"),
                "answer_origin": audit.get("answer_origin"),
                "inbound_to_meta_ack_ms": elapsed_ms(received, send_end),
                # Meta timestamps have second precision and a different clock.
                "delivery_callbacks": [{
                    "status": e.get("status"), "timestamp_source": e.get("timestamp_source"),
                    "meta_ack_to_callback_ms_approx": elapsed_ms(send_end, e.get("callback_at")),
                } for e in audit.get("delivery_history", [])],
                "timing": audit.get("timing"),
            })
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", required=True)
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--job-id", type=UUID)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(report(args.tenant_slug, args.limit, args.job_id)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
