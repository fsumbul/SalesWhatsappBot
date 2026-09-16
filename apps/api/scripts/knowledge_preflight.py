"""Verify production GraphRAG services, live indexes, and the dedicated worker.

Read-only apart from embedding inference; sends no customer messages.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.knowledge_index import _live_versions
from src.core.agent_celery_app import agent_celery_app
from src.integrations.embeddings import get_embedding_client
from src.modules.knowledge.graph_store import get_graph_store
from src.modules.knowledge.service import knowledge_enabled


async def inspect(tenant_slug: str) -> dict[str, object]:
    store = get_graph_store()
    enabled = knowledge_enabled()
    graph_ok = enabled and await store.ping()
    embedding_ok = False
    try:
        client = get_embedding_client()
        vector = await client.embed_query("knowledge readiness")
        embedding_ok = len(vector) == client.dimension and client.dimension > 0
    except Exception:
        embedding_ok = False
    versions = await _live_versions(tenant_slug)
    indexes = []
    for tenant_id, version_id, _ in versions:
        name = store.kb_graph_name(tenant_id, version_id)
        indexes.append(graph_ok and await store.graph_exists(name))
    queues = await asyncio.to_thread(
        lambda: agent_celery_app.control.inspect(timeout=5).active_queues() or {}
    )
    workers = [
        name
        for name, items in queues.items()
        if any(item.get("name") == "knowledge" for item in items)
    ]
    ok = enabled and graph_ok and embedding_ok and bool(indexes) and all(indexes) and bool(workers)
    return {
        "ok": bool(ok),
        "graph": graph_ok,
        "embedding": embedding_ok,
        "live_indexes": len(indexes),
        "all_live_indexes_present": bool(indexes) and all(indexes),
        "knowledge_workers": workers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-slug", required=True)
    args = parser.parse_args()
    report = asyncio.run(inspect(args.tenant_slug))
    print(json.dumps(report))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
