"""Hybrid graph + vector + full-text retrieval with RRF fusion and reranking.

Channels (run concurrently, each returning an ordered fact-id list):

    exact     product/bearing codes and unit measures -> (:Code)-[:CODE_OF]->(:Fact)
    vector    BGE-M3 cosine KNN over Fact.embedding (HNSW)
    fulltext  RediSearch (Turkish stemmer) over Fact.search_document
    graph     facts attached to trusted anchor subjects and their inheritance
              lineage (is_variant_of / part_of)
    memory    the same lineage walk for products from the customer's memory graph

Reciprocal Rank Fusion merges the lists; exact-code hits are pinned first;
an optional cross-encoder reranks the fused pool. The result is a *candidate*
list: the trusted runtime still applies its commercial-intent gates and the
LLM still only chooses ids from it.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from time import perf_counter
from typing import Any
from uuid import UUID

from src.integrations.embeddings import EmbeddingClient
from src.integrations.reranker import Reranker

from .compiler import extract_codes, fts_query
from .graph_store import GraphStore
from .ports import (
    FactCandidate,
    FactRetriever,
    RetrievalRequest,
    RetrievalResult,
    RetrievalUnavailableError,
)

_INHERITANCE_PREDICATES = ["is_variant_of", "part_of"]
_DEFAULT_WEIGHTS = {"exact": 2.0, "vector": 1.0, "fulltext": 0.9, "graph": 0.8, "memory": 0.5}
_RRF_K = 60
# Graph locality must survive fusion and reranking: a fact attached to the
# conversation's trusted subject (or, more weakly, to a product the customer
# discussed before) gets a fixed bonus on top of its score. Rerank scores are
# sigmoid probabilities, RRF scores are ~1/(k + rank), hence two scales.
_ANCHOR_BONUS = {"graph": 0.20, "memory": 0.10}
_RRF_ANCHOR_BONUS = {"graph": 0.006, "memory": 0.003}


def rrf_fuse(
    channels: dict[str, list[str]],
    *,
    weights: dict[str, float] | None = None,
    k: int = _RRF_K,
) -> list[tuple[str, float, tuple[str, ...]]]:
    """Weighted Reciprocal Rank Fusion; deterministic for equal scores."""

    weights = weights or _DEFAULT_WEIGHTS
    scores: dict[str, float] = defaultdict(float)
    provenance: dict[str, list[str]] = defaultdict(list)
    for channel, fact_ids in channels.items():
        weight = weights.get(channel, 1.0)
        for rank, fact_id in enumerate(dict.fromkeys(fact_ids), start=1):
            scores[fact_id] += weight / (k + rank)
            provenance[fact_id].append(channel)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [(fact_id, score, tuple(provenance[fact_id])) for fact_id, score in ordered]


class FalkorGraphFactRetriever:
    """``HybridFactRetriever`` from the architecture doc, backed by FalkorDB."""

    backend = "falkordb"

    def __init__(
        self,
        store: GraphStore,
        embeddings: EmbeddingClient,
        reranker: Reranker | None = None,
        *,
        max_candidates: int = 12,
        rerank_pool: int = 24,
        timeout_seconds: float = 4.0,
        channel_weights: dict[str, float] | None = None,
    ) -> None:
        self.store = store
        self.embeddings = embeddings
        self.reranker = reranker
        self.max_candidates = max_candidates
        self.rerank_pool = rerank_pool
        self.timeout_seconds = timeout_seconds
        self.channel_weights = channel_weights or _DEFAULT_WEIGHTS

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        started = perf_counter()
        graph = self.store.kb_graph_name(request.tenant_id, request.agent_version_id)
        timings: dict[str, float] = {}
        try:
            exists = await asyncio.wait_for(
                self.store.graph_exists(graph), timeout=self.timeout_seconds
            )
        except Exception as exc:
            raise RetrievalUnavailableError("knowledge graph is unreachable") from exc
        if not exists:
            raise RetrievalUnavailableError("knowledge index is missing for this agent version")

        limit = max(request.max_candidates, self.rerank_pool)
        codes = extract_codes(request.query)
        fts = fts_query(request.query)
        tasks: dict[str, asyncio.Future[list[str]]] = {
            "vector": asyncio.ensure_future(
                self._timed(timings, "vector", self._vector(graph, request.query, limit))
            ),
            "fulltext": asyncio.ensure_future(
                self._timed(timings, "fulltext", self._fulltext(graph, fts, limit))
            ),
            "exact": asyncio.ensure_future(
                self._timed(timings, "exact", self._exact(graph, codes))
            ),
        }
        memory_subjects = tuple(
            subject
            for subject in request.memory_subject_ids
            if subject not in request.anchor_subject_ids
        )
        if request.anchor_subject_ids:
            tasks["graph"] = asyncio.ensure_future(
                self._timed(timings, "graph", self._locality(graph, request.anchor_subject_ids))
            )
        if memory_subjects:
            tasks["memory"] = asyncio.ensure_future(
                self._timed(timings, "memory", self._locality(graph, memory_subjects))
            )
        try:
            outcomes = await asyncio.wait_for(
                asyncio.gather(*tasks.values(), return_exceptions=True),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            for task in tasks.values():
                task.cancel()
            raise RetrievalUnavailableError("retrieval timed out") from exc

        channels: dict[str, list[str]] = {}
        failures: dict[str, str] = {}
        for name, outcome in zip(tasks, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                failures[name] = type(outcome).__name__
                channels[name] = []
            else:
                channels[name] = outcome
        if len(failures) == len(tasks):
            raise RetrievalUnavailableError(
                "every retrieval channel failed: " + ", ".join(failures)
            )

        fused = rrf_fuse(channels, weights=self.channel_weights)
        if request.allowed_fact_ids is not None:
            fused = [item for item in fused if item[0] in request.allowed_fact_ids]
        exact_ids = set(channels["exact"])
        pool = fused[: self.rerank_pool]

        reranked = False
        rerank_scores: dict[str, float] = {}
        if self.reranker is not None and self.reranker.available and pool:
            rerank_started = perf_counter()
            try:
                documents = await self._documents(graph, [fact_id for fact_id, _, _ in pool])
                ids = [fact_id for fact_id, _, _ in pool if fact_id in documents]
                scores = await self.reranker.rerank(request.query, [documents[i] for i in ids])
                rerank_scores = dict(zip(ids, scores, strict=True))
                reranked = True
            except Exception as exc:
                failures["rerank"] = type(exc).__name__
            timings["rerank"] = (perf_counter() - rerank_started) * 1000

        def _final_score(fact_id: str, rrf_score: float, provenance: tuple[str, ...]) -> float:
            bonuses = _ANCHOR_BONUS if reranked else _RRF_ANCHOR_BONUS
            base = rerank_scores.get(fact_id, 0.0) if reranked else rrf_score
            return base + max((bonuses.get(channel, 0.0) for channel in provenance), default=0.0)

        scored = [
            (fact_id, _final_score(fact_id, rrf_score, provenance), provenance)
            for fact_id, rrf_score, provenance in pool
        ]
        ranked = sorted(
            scored,
            key=lambda item: (0 if item[0] in exact_ids else 1, -item[1], item[0]),
        )
        candidates = [
            FactCandidate(
                fact_id=fact_id,
                score=score,
                channels=provenance,
                exact_code=fact_id in exact_ids,
            )
            for fact_id, score, provenance in ranked[: request.max_candidates]
        ]
        timings["total"] = (perf_counter() - started) * 1000
        return RetrievalResult(
            candidates=candidates,
            backend=self.backend,
            timings_ms=timings,
            query_codes=codes,
            degraded=bool(failures),
            reranked=reranked,
        )

    # --- channels -----------------------------------------------------------

    @staticmethod
    async def _timed(timings: dict[str, float], name: str, coroutine: Any) -> list[str]:
        started = perf_counter()
        try:
            result: list[str] = await coroutine
            return result
        finally:
            timings[name] = (perf_counter() - started) * 1000

    async def _vector(self, graph: str, query: str, limit: int) -> list[str]:
        vector = await self.embeddings.embed_query(query)
        rows = await self.store.query(
            graph,
            "CALL db.idx.vector.queryNodes('Fact', 'embedding', $k, vecf32($q)) "
            "YIELD node, score RETURN node.id AS id, score ORDER BY score ASC",
            {"k": limit, "q": vector},
        )
        return [str(row[0]) for row in rows]

    async def _fulltext(self, graph: str, fts: str, limit: int) -> list[str]:
        if not fts:
            return []
        rows = await self.store.query(
            graph,
            "CALL db.idx.fulltext.queryNodes('Fact', $q) YIELD node, score "
            f"RETURN node.id AS id, score ORDER BY score DESC LIMIT {int(limit)}",
            {"q": fts},
        )
        return [str(row[0]) for row in rows]

    async def _exact(self, graph: str, codes: tuple[str, ...]) -> list[str]:
        if not codes:
            return []
        rows = await self.store.query(
            graph,
            "MATCH (c:Code)-[:CODE_OF]->(f:Fact) WHERE c.value IN $codes "
            "RETURN f.id AS id, count(c) AS hits ORDER BY hits DESC, id ASC",
            {"codes": list(codes)},
        )
        return [str(row[0]) for row in rows]

    async def _locality(self, graph: str, subjects_in: tuple[str, ...]) -> list[str]:
        """Facts attached to the subjects, then to their inheritance lineage."""

        ordered: list[str] = []
        for subjects in [list(subjects_in)] if subjects_in else []:
            direct = await self.store.query(
                graph,
                "MATCH (f:Fact)-[:ABOUT]->(s) WHERE s.id IN $subjects "
                "RETURN DISTINCT f.id AS id ORDER BY id",
                {"subjects": subjects},
            )
            lineage = await self.store.query(
                graph,
                "MATCH p = (s:Offering)-[:REL*1..3]->(t) WHERE s.id IN $subjects "
                "AND all(r IN relationships(p) WHERE r.predicate IN $inherit) "
                "MATCH (f:Fact)-[:ABOUT]->(t) RETURN DISTINCT f.id AS id ORDER BY id",
                {"subjects": subjects, "inherit": _INHERITANCE_PREDICATES},
            )
            for row in [*direct, *lineage]:
                fact_id = str(row[0])
                if fact_id not in ordered:
                    ordered.append(fact_id)
        return ordered

    async def _documents(self, graph: str, fact_ids: list[str]) -> dict[str, str]:
        rows = await self.store.query(
            graph,
            "MATCH (f:Fact) WHERE f.id IN $ids RETURN f.id, f.search_document",
            {"ids": fact_ids},
        )
        return {str(row[0]): str(row[1]) for row in rows}


class ScopedFactRetriever:
    """Bind a backend retriever to one server-resolved tenant + LIVE version."""

    def __init__(
        self,
        retriever: FactRetriever,
        *,
        tenant_id: UUID,
        agent_version_id: UUID,
    ) -> None:
        self._retriever = retriever
        self._tenant_id = tenant_id
        self._agent_version_id = agent_version_id

    @property
    def backend(self) -> str:
        return self._retriever.backend

    async def retrieve(
        self,
        query: str,
        *,
        anchor_subject_ids: tuple[str, ...] = (),
        memory_subject_ids: tuple[str, ...] = (),
        allowed_fact_ids: frozenset[str] | None = None,
        max_candidates: int = 12,
    ) -> RetrievalResult:
        return await self._retriever.retrieve(
            RetrievalRequest(
                tenant_id=self._tenant_id,
                agent_version_id=self._agent_version_id,
                query=query,
                anchor_subject_ids=anchor_subject_ids,
                memory_subject_ids=memory_subject_ids,
                allowed_fact_ids=allowed_fact_ids,
                max_candidates=max_candidates,
            )
        )
