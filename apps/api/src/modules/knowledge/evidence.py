"""Per-tenant evidence graph of document/website chunks (``kn_<tenant>``).

Separate from the per-version runtime index (``kb_<tenant>_<version>``, ADR-002):
chunks are raw-ish evidence with locators, never approved customer text. They
serve (a) mention-aware retrieval for the hybrid "grounded" answer mode and
(b) admin-facing provenance. Layout::

    (:Chunk {id, source_id, snapshot_id, locator, title, text, content_hash, embedding})
    (:Chunk)-[:MENTIONS]->(:Subject {id})
    (:Chunk)-[:FROM]->(:Source {id})
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID

from src.integrations.embeddings import EmbeddingClient, embedding_profile
from src.integrations.reranker import Reranker

from .compiler import extract_codes, fts_query
from .graph_store import GraphStore
from .indexer import fulltext_language
from .ports import EvidencePassage
from .retrieval import rrf_fuse

_BATCH = 24
_WEIGHTS = {"vector": 1.0, "fulltext": 0.9, "subject": 0.6}


@dataclass(frozen=True)
class EvidenceChunk:
    id: str
    text: str
    locator: str
    title: str
    subject_ids: tuple[str, ...]
    score: float
    channels: tuple[str, ...]


class EmbeddingProfileMismatchError(RuntimeError):
    """The tenant's chunk graph was embedded with another model/dimension.

    Vectors from two embedding spaces never mix: ingestion stops writing to
    the graph until ``reembed_knowledge_graph`` rebuilt it (plan WP4).
    """


class EvidenceGraph:
    def __init__(self, store: GraphStore, embeddings: EmbeddingClient) -> None:
        self.store = store
        self.embeddings = embeddings

    @staticmethod
    def graph_name(tenant_id: UUID) -> str:
        return f"kn_{tenant_id.hex}"

    @property
    def profile(self) -> str:
        return embedding_profile(self.embeddings)

    async def current_profile(self, tenant_id: UUID) -> str | None:
        """Embedding profile recorded on the graph, ``None`` when absent (legacy or new)."""

        graph = self.graph_name(tenant_id)
        if not await self.store.graph_exists(graph):
            return None
        rows = await self.store.query(graph, "MATCH (m:Meta) RETURN m.profile LIMIT 1")
        if not rows or not isinstance(rows[0][0], str):
            return None
        return rows[0][0]

    async def rebuild(self, tenant_id: UUID, locale: str = "tr") -> None:
        """Drop the tenant graph and recreate its indexes for the current profile."""

        await self.store.delete_graph(self.graph_name(tenant_id))
        await self.ensure_indexes(tenant_id, locale)

    async def ensure_indexes(self, tenant_id: UUID, locale: str = "tr") -> None:
        graph = self.graph_name(tenant_id)
        existing = await self.current_profile(tenant_id)
        if existing is not None and existing != self.profile:
            raise EmbeddingProfileMismatchError(
                f"graph built with {existing}, configured {self.profile}; run knowledge-reembed"
            )
        statements = [
            "CREATE INDEX FOR (c:Chunk) ON (c.id)",
            "CREATE INDEX FOR (c:Chunk) ON (c.source_id)",
            "CREATE INDEX FOR (s:Subject) ON (s.id)",
            (
                "CREATE VECTOR INDEX FOR (c:Chunk) ON (c.embedding) "
                f"OPTIONS {{dimension: {int(self.embeddings.dimension)}, similarityFunction: 'cosine'}}"
            ),
            (
                "CALL db.idx.fulltext.createNodeIndex("
                f"{{label: 'Chunk', language: '{fulltext_language(locale)}'}}, 'text')"
            ),
        ]
        for statement in statements:
            try:
                await self.store.query(graph, statement)
            except Exception as exc:  # already exists
                message = str(exc).lower()
                if "already" not in message and "exist" not in message:
                    raise
        if existing is None:
            await self.store.query(
                graph,
                "MERGE (m:Meta {kind: 'evidence'}) SET m.profile = $profile, "
                "m.embedding_model = $model, m.dimension = $dimension",
                {
                    "profile": self.profile,
                    "model": self.embeddings.model_name,
                    "dimension": int(self.embeddings.dimension),
                },
            )

    async def upsert_chunks(self, tenant_id: UUID, rows: list[dict[str, Any]]) -> int:
        """Rows: id, source_id, snapshot_id, locator, title, text, content_hash, subject_ids."""

        if not rows:
            return 0
        graph = self.graph_name(tenant_id)
        vectors = await self.embeddings.embed_documents([str(row["text"]) for row in rows])
        for start in range(0, len(rows), _BATCH):
            batch = [
                {**row, "embedding": vector}
                for row, vector in zip(
                    rows[start : start + _BATCH], vectors[start : start + _BATCH], strict=True
                )
            ]
            await self.store.query(
                graph,
                "UNWIND $rows AS r MERGE (c:Chunk {id: r.id}) SET c.source_id = r.source_id, "
                "c.snapshot_id = r.snapshot_id, c.locator = r.locator, c.title = r.title, "
                "c.text = r.text, c.content_hash = r.content_hash, c.subject_ids = r.subject_ids, "
                "c.embedding = vecf32(r.embedding) "
                "MERGE (s:Source {id: r.source_id}) MERGE (c)-[:FROM]->(s)",
                {"rows": batch},
            )
            mention_rows = [
                {"id": row["id"], "subject": subject}
                for row in batch
                for subject in row.get("subject_ids", [])
            ]
            if mention_rows:
                await self.store.query(
                    graph,
                    "UNWIND $rows AS r MATCH (c:Chunk {id: r.id}) MERGE (s:Subject {id: r.subject}) "
                    "MERGE (c)-[:MENTIONS]->(s)",
                    {"rows": mention_rows},
                )
        return len(rows)

    async def set_mentions(self, tenant_id: UUID, chunk_id: str, subject_ids: list[str]) -> None:
        graph = self.graph_name(tenant_id)
        await self.store.query(
            graph,
            "MATCH (c:Chunk {id: $id}) SET c.subject_ids = $subjects "
            "WITH c OPTIONAL MATCH (c)-[m:MENTIONS]->() DELETE m",
            {"id": chunk_id, "subjects": subject_ids},
        )
        if subject_ids:
            await self.store.query(
                graph,
                "MATCH (c:Chunk {id: $id}) UNWIND $subjects AS sid MERGE (s:Subject {id: sid}) "
                "MERGE (c)-[:MENTIONS]->(s)",
                {"id": chunk_id, "subjects": subject_ids},
            )

    async def delete_source(self, tenant_id: UUID, source_id: UUID) -> None:
        graph = self.graph_name(tenant_id)
        if not await self.store.graph_exists(graph):
            return
        await self.store.query(
            graph,
            "MATCH (c:Chunk {source_id: $sid}) DETACH DELETE c",
            {"sid": str(source_id)},
        )
        await self.store.query(
            graph, "MATCH (s:Source {id: $sid}) DETACH DELETE s", {"sid": str(source_id)}
        )

    async def delete_snapshot(self, tenant_id: UUID, snapshot_id: UUID) -> None:
        graph = self.graph_name(tenant_id)
        if not await self.store.graph_exists(graph):
            return
        await self.store.query(
            graph, "MATCH (c:Chunk {snapshot_id: $sid}) DETACH DELETE c", {"sid": str(snapshot_id)}
        )


class EvidenceRetriever:
    """Hybrid chunk retrieval for grounded answers (vector + full-text + mentions)."""

    def __init__(
        self,
        store: GraphStore,
        embeddings: EmbeddingClient,
        reranker: Reranker | None = None,
        *,
        timeout_seconds: float = 4.0,
    ) -> None:
        self.store = store
        self.embeddings = embeddings
        self.reranker = reranker
        self.timeout_seconds = timeout_seconds

    async def retrieve(
        self,
        tenant_id: UUID,
        query: str,
        *,
        subject_ids: tuple[str, ...] = (),
        k: int = 6,
        pool: int = 16,
    ) -> tuple[list[EvidenceChunk], dict[str, float]]:
        graph = EvidenceGraph.graph_name(tenant_id)
        timings: dict[str, float] = {}
        started = perf_counter()
        if not await self.store.graph_exists(graph):
            return [], {"total": 0.0}

        async def vector() -> list[str]:
            embedding = await self.embeddings.embed_query(query)
            rows = await self.store.query(
                graph,
                "CALL db.idx.vector.queryNodes('Chunk', 'embedding', $k, vecf32($q)) "
                "YIELD node, score RETURN node.id ORDER BY score ASC",
                {"k": pool, "q": embedding},
            )
            return [str(r[0]) for r in rows]

        async def fulltext() -> list[str]:
            fts = fts_query(query)
            if not fts:
                return []
            rows = await self.store.query(
                graph,
                "CALL db.idx.fulltext.queryNodes('Chunk', $q) YIELD node, score "
                f"RETURN node.id ORDER BY score DESC LIMIT {int(pool)}",
                {"q": fts},
            )
            return [str(r[0]) for r in rows]

        async def subject() -> list[str]:
            if not subject_ids:
                return []
            rows = await self.store.query(
                graph,
                "MATCH (c:Chunk)-[:MENTIONS]->(s:Subject) WHERE s.id IN $subjects "
                f"RETURN DISTINCT c.id LIMIT {int(pool)}",
                {"subjects": list(subject_ids)},
            )
            return [str(r[0]) for r in rows]

        try:
            outcomes = await asyncio.wait_for(
                asyncio.gather(vector(), fulltext(), subject(), return_exceptions=True),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return [], {"total": (perf_counter() - started) * 1000, "timeout": 1.0}
        channels: dict[str, list[str]] = {}
        for name, outcome in zip(("vector", "fulltext", "subject"), outcomes, strict=True):
            channels[name] = [] if isinstance(outcome, BaseException) else outcome
        fused = rrf_fuse(channels, weights=_WEIGHTS)[:pool]
        if not fused:
            return [], {"total": (perf_counter() - started) * 1000}
        ids = [fact_id for fact_id, _, _ in fused]
        rows = await self.store.query(
            graph,
            "MATCH (c:Chunk) WHERE c.id IN $ids RETURN c.id, c.text, c.locator, c.title, c.subject_ids",
            {"ids": ids},
        )
        by_id = {str(r[0]): r for r in rows}
        scores = {fact_id: score for fact_id, score, _ in fused}
        provenance = {fact_id: channels_ for fact_id, _, channels_ in fused}
        if self.reranker is not None and self.reranker.available:
            rerank_started = perf_counter()
            try:
                present = [i for i in ids if i in by_id]
                rerank = await self.reranker.rerank(query, [str(by_id[i][1]) for i in present])
                scores = dict(zip(present, rerank, strict=True))
            except Exception as exc:  # fused order is still a valid ranking
                timings["rerank_error"] = float(hash(type(exc).__name__) % 1000)
            timings["rerank"] = (perf_counter() - rerank_started) * 1000
        query_codes = set(extract_codes(query))
        results: list[EvidenceChunk] = []
        for chunk_id in ids:
            row = by_id.get(chunk_id)
            if row is None:
                continue
            bonus = 0.0
            if "subject" in provenance[chunk_id]:
                bonus += 0.15
            if query_codes and query_codes & set(extract_codes(str(row[1]))):
                bonus += 0.25
            results.append(
                EvidenceChunk(
                    id=chunk_id,
                    text=str(row[1]),
                    locator=str(row[2]),
                    title=str(row[3] or ""),
                    subject_ids=tuple(row[4] or []),
                    score=scores.get(chunk_id, 0.0) + bonus,
                    channels=provenance[chunk_id],
                )
            )
        results.sort(key=lambda c: -c.score)
        timings["total"] = (perf_counter() - started) * 1000
        return results[:k], timings


class ScopedEvidenceRetriever:
    """Bind ``EvidenceRetriever`` to one server-resolved tenant for the runtime."""

    backend = "falkordb"

    def __init__(self, retriever: EvidenceRetriever, *, tenant_id: UUID) -> None:
        self._retriever = retriever
        self._tenant_id = tenant_id

    async def retrieve(
        self,
        query: str,
        *,
        subject_ids: tuple[str, ...] = (),
        k: int = 4,
    ) -> list[EvidencePassage]:
        chunks, _ = await self._retriever.retrieve(
            self._tenant_id, query, subject_ids=subject_ids, k=k
        )
        return [
            EvidencePassage(
                id=chunk.id,
                text=chunk.text,
                locator=chunk.locator,
                subject_ids=chunk.subject_ids,
                score=chunk.score,
            )
            for chunk in chunks
        ]
