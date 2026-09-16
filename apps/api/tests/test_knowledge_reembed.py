# ruff: noqa: RUF001
"""Embedding profile on the tenant chunk graph and the re-embed task (NIM plan WP4)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.db import session_scope
from src.modules.knowledge import service as knowledge_service
from src.modules.knowledge.evidence import EmbeddingProfileMismatchError, EvidenceGraph
from src.modules.knowledge.ingest import KnowledgeIngestService
from src.modules.knowledge.models import KnowledgeChunk
from src.workers import knowledge as knowledge_worker
from tests.test_knowledge_ingest import (
    _ChunkGuard,
    _document_source,
    _ExtractingLLM,
    _FakeGraph,
    _install_live,
)
from tests.test_whatsapp_runtime_integration import (
    _seed_runtime_tenant,
    runtime_database,  # noqa: F401 - pytest fixture
)


class _Embeddings:
    def __init__(self, model_name: str = "fake", dimension: int = 3) -> None:
        self.model_name, self.dimension = model_name, dimension

    async def embed_query(self, text: str) -> list[float]:
        return [1.0] * self.dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] * self.dimension for _ in texts]


class _Store:
    def __init__(self, *, exists: bool = False, profile: str | None = None) -> None:
        self.exists = exists
        self.profile = profile
        self.queries: list[tuple[str, str, dict[str, Any] | None]] = []
        self.deleted: list[str] = []

    async def graph_exists(self, name: str) -> bool:
        return self.exists

    async def delete_graph(self, name: str) -> None:
        self.deleted.append(name)
        self.exists = False
        self.profile = None

    async def query(self, graph: str, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        self.queries.append((graph, cypher, params))
        if "MATCH (m:Meta)" in cypher:
            return [[self.profile]] if self.profile else []
        if cypher.startswith("CREATE") or cypher.startswith("CALL"):
            raise RuntimeError("index already exists")
        return []


async def test_evidence_graph_records_its_profile_and_refuses_another_one() -> None:
    tenant_id = uuid4()
    store = _Store()
    graph = EvidenceGraph(store, _Embeddings())  # type: ignore[arg-type]
    assert graph.profile == "fake#3"
    await graph.ensure_indexes(tenant_id, "tr")
    meta = [q for q in store.queries if "MERGE (m:Meta" in q[1]]
    assert len(meta) == 1 and meta[0][2] == {"profile": "fake#3", "model": "fake", "dimension": 3}

    other = _Store(exists=True, profile="bge-m3#1024")
    graph = EvidenceGraph(other, _Embeddings("nvidia/nemotron-3-embed-1b", 2048))  # type: ignore[arg-type]
    with pytest.raises(EmbeddingProfileMismatchError):
        await graph.ensure_indexes(tenant_id, "tr")
    assert not any("MERGE (m:Meta" in q[1] for q in other.queries)

    # Same profile: nothing rewritten; rebuild drops the graph and re-stamps it.
    same = _Store(exists=True, profile="fake#3")
    graph = EvidenceGraph(same, _Embeddings())  # type: ignore[arg-type]
    await graph.ensure_indexes(tenant_id, "tr")
    assert not any("MERGE (m:Meta" in q[1] for q in same.queries)
    await graph.rebuild(tenant_id, "tr")
    assert same.deleted == [f"kn_{tenant_id.hex}"]
    assert any("MERGE (m:Meta" in q[1] for q in same.queries)


class _MismatchGraph(_FakeGraph):
    async def ensure_indexes(self, tenant_id: UUID, locale: str = "tr") -> None:
        raise EmbeddingProfileMismatchError("graph built with bge-m3#1024, configured fake#3")


@pytest.mark.asyncio
async def test_ingest_keeps_going_without_the_graph_on_profile_mismatch(
    runtime_database: None,  # noqa: F811
) -> None:
    get_settings.cache_clear()
    tenant_id, _owner = await _seed_runtime_tenant(with_agent=True)
    agent_id = await _install_live(tenant_id)
    source_id = await _document_source(tenant_id, agent_id, "upload://mismatch")
    graph = _MismatchGraph()
    async with session_scope(tenant_id) as session:
        result = await KnowledgeIngestService(session, llm=_ExtractingLLM(), graph=graph).sync_source(tenant_id, source_id)  # type: ignore[arg-type]
    assert result["status"] == "synced" and result["stats"]["graph_profile_mismatch"] == 1
    assert result["stats"]["chunks"] == 2 and graph.rows == []
    async with session_scope(tenant_id) as session:
        chunks = list((await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id))).scalars())
        assert all(c.extracted and not c.embedded for c in chunks)


class _ReembedGraph:
    profile = "nvidia/nemotron-3-embed-1b#2048"

    def __init__(self) -> None:
        self.rebuilt: list[tuple[UUID, str]] = []
        self.batches: list[list[dict[str, Any]]] = []

    @staticmethod
    def graph_name(tenant_id: UUID) -> str:
        return f"kn_{tenant_id.hex}"

    async def rebuild(self, tenant_id: UUID, locale: str = "tr") -> None:
        self.rebuilt.append((tenant_id, locale))

    async def upsert_chunks(self, tenant_id: UUID, rows: list[dict[str, Any]]) -> int:
        self.batches.append(rows)
        return len(rows)


@pytest.mark.asyncio
async def test_reembed_task_rebuilds_the_graph_from_postgres_and_skips_blocked_chunks(
    runtime_database: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    tenant_id, _owner = await _seed_runtime_tenant(with_agent=True)
    agent_id = await _install_live(tenant_id)
    source_id = await _document_source(tenant_id, agent_id, "upload://reembed")
    async with session_scope(tenant_id) as session:
        await KnowledgeIngestService(session, llm=_ExtractingLLM(), graph=_FakeGraph(), guard=_ChunkGuard()).sync_source(tenant_id, source_id)  # type: ignore[arg-type]

    graph = _ReembedGraph()
    monkeypatch.setattr(knowledge_service, "build_evidence_graph", lambda store=None: graph)
    summary = await knowledge_worker._reembed_knowledge_graph(tenant_id)

    assert summary == {
        "status": "reembedded",
        "graph": f"kn_{tenant_id.hex}",
        "profile": "nvidia/nemotron-3-embed-1b#2048",
        "chunks": 1,
        "skipped": 1,
    }
    assert graph.rebuilt == [(tenant_id, "tr")]
    assert len(graph.batches) == 1 and "Palanga" not in graph.batches[0][0]["text"]
    assert graph.batches[0][0]["subject_ids"] == [] and graph.batches[0][0]["title"] == "Firma"
    async with session_scope(tenant_id) as session:
        chunks = list((await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id))).scalars())
        blocked = next(c for c in chunks if "Palanga" in c.text)
        allowed = next(c for c in chunks if "Palanga" not in c.text)
        assert allowed.embedded is True and blocked.embedded is False

    monkeypatch.setattr(knowledge_service, "build_evidence_graph", lambda store=None: None)
    assert await knowledge_worker._reembed_knowledge_graph(tenant_id) == {"status": "disabled"}
