# ruff: noqa: E402
"""Build (or verify) the GraphRAG knowledge index for a tenant's LIVE agent.

Idempotent: an index whose fingerprint (approved fact ids + search documents
+ embedding model) is unchanged is reused. Run from ``apps/api``::

    poetry run python scripts/knowledge_index.py --tenant-slug kasnak
    poetry run python scripts/knowledge_index.py --tenant-slug kasnak --rebuild
    poetry run python scripts/knowledge_index.py --config config/arti_kasnak.production.json \
        --tenant-id 00000000-0000-0000-0000-00000000aaaa --version-id 00000000-0000-0000-0000-00000000bbbb

The second form indexes a config file without a database (local evaluation).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import models_registry  # noqa: F401
from src.core.db import get_sessionmaker, reset_tenant_context, set_tenant_context
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.models import Agent, AgentVersion, AgentVersionStatus
from src.modules.auth.models import Tenant
from src.modules.knowledge.service import build_indexer, knowledge_enabled


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug")
    parser.add_argument("--config", type=Path, help="index a config file instead of the database")
    parser.add_argument("--tenant-id", type=UUID)
    parser.add_argument("--version-id", type=UUID)
    parser.add_argument("--rebuild", action="store_true")
    return parser.parse_args()


async def _live_versions(tenant_slug: str) -> list[tuple[UUID, UUID, CompanyAgentConfig]]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"Tenant '{tenant_slug}' was not found")
        await set_tenant_context(session, tenant.id)
        try:
            rows = (
                await session.execute(
                    select(AgentVersion)
                    .join(Agent, Agent.id == AgentVersion.agent_id)
                    .where(
                        AgentVersion.tenant_id == tenant.id,
                        AgentVersion.status == AgentVersionStatus.LIVE,
                        Agent.is_active.is_(True),
                    )
                )
            ).scalars().all()
            # Reset rolls back the transaction and expires ORM attributes.
            # Materialize the immutable indexing inputs before that boundary.
            return [
                (tenant.id, row.id, CompanyAgentConfig.model_validate(row.company_config))
                for row in rows
            ]
        finally:
            await reset_tenant_context(session)


async def _async_main(args: argparse.Namespace) -> None:
    if not knowledge_enabled():
        raise SystemExit("KNOWLEDGE_BACKEND=falkordb and EMBEDDING_PROVIDER=ollama are required")
    targets: list[tuple[UUID, UUID, CompanyAgentConfig]]
    if args.config is not None:
        if args.tenant_id is None or args.version_id is None:
            raise SystemExit("--config requires --tenant-id and --version-id")
        config = CompanyAgentConfig.model_validate(json.loads(args.config.read_text(encoding="utf-8")))
        targets = [(args.tenant_id, args.version_id, config)]
    elif args.tenant_slug:
        targets = await _live_versions(args.tenant_slug)
        if not targets:
            raise SystemExit("no LIVE agent version for this tenant")
    else:
        raise SystemExit("pass --tenant-slug or --config")

    indexer = build_indexer()
    for tenant_id, version_id, config in targets:
        report = await indexer.index_version(
            tenant_id=tenant_id, agent_version_id=version_id, config=config, rebuild=args.rebuild
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False))


def main() -> None:
    asyncio.run(_async_main(_parse_args()))


if __name__ == "__main__":
    main()
