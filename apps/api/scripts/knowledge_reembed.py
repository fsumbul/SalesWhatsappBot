# ruff: noqa: E402
"""Rebuild a tenant's evidence graph (``kn_<tenant>``) for the configured embedding profile.

Needed after changing ``EMBEDDING_PROVIDER`` / ``EMBEDDING_MODEL`` /
``EMBEDDING_DIMENSION``: the per-version fact index (``kb_*``) rebuilds itself
from its fingerprint, the chunk graph does not. Run from ``apps/api``::

    poetry run python scripts/knowledge_reembed.py --tenant-slug kasnak

Blocked chunks (guardrail verdict) are skipped; everything else is re-embedded
in batches and marked ``embedded`` again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import models_registry  # noqa: F401
from src.core.db import get_sessionmaker
from src.modules.auth.models import Tenant
from src.modules.knowledge.service import knowledge_enabled
from src.workers.knowledge import _reembed_knowledge_graph


async def _async_main(tenant_slug: str) -> None:
    if not knowledge_enabled():
        raise SystemExit("KNOWLEDGE_BACKEND=falkordb and EMBEDDING_PROVIDER=ollama|nim are required")
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"Tenant '{tenant_slug}' was not found")
        tenant_id = tenant.id
    print(json.dumps(await _reembed_knowledge_graph(tenant_id), ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", required=True)
    args = parser.parse_args()
    asyncio.run(_async_main(args.tenant_slug))


if __name__ == "__main__":
    main()
