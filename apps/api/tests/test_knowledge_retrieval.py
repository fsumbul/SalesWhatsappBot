"""Hybrid retrieval fusion tests with an in-memory graph store double."""

from typing import Any
from uuid import uuid4

import pytest

from src.modules.knowledge.ports import RetrievalRequest, RetrievalUnavailableError
from src.modules.knowledge.retrieval import (
    FalkorGraphFactRetriever,
    ScopedFactRetriever,
    rrf_fuse,
)

_DOCS = {
    "bearing": "Rulman 6211 55 mm mil",
    "cast_dims": "Döküm kasnak 320 mm çap",
    "nylon_dims": "MC Nylon 320 mm çap",
    "contact": "İletişim bilgileri",
    "social": "Sohbet davranışı",
}


class _FakeStore:
    """Answers each retrieval channel from canned rows; records every query."""

    def __init__(
        self,
        *,
        exists: bool = True,
        vector: list[str] | None = None,
        fulltext: list[str] | None = None,
        exact: list[str] | None = None,
        graph: list[str] | None = None,
        failing: set[str] | None = None,
    ) -> None:
        self.exists = exists
        self.rows = {
            "vector": vector or [],
            "fulltext": fulltext or [],
            "exact": exact or [],
            "graph": graph or [],
        }
        self.failing = failing or set()
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    @staticmethod
    def kb_graph_name(tenant_id: Any, version_id: Any) -> str:
        return f"kb_{tenant_id.hex}_{version_id.hex}"

    async def graph_exists(self, name: str) -> bool:
        return self.exists

    async def query(self, graph: str, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        self.queries.append((cypher, params))
        if "db.idx.vector" in cypher:
            channel = "vector"
        elif "db.idx.fulltext" in cypher:
            channel = "fulltext"
        elif "CODE_OF" in cypher:
            channel = "exact"
        elif "ABOUT" in cypher:
            channel = "graph"
        elif "search_document" in cypher:
            return [[fact_id, _DOCS.get(fact_id, fact_id)] for fact_id in (params or {})["ids"]]
        else:
            raise AssertionError(cypher)
        if channel in self.failing:
            raise RuntimeError(f"{channel} down")
        if channel == "graph" and "REL*" in cypher:
            return []
        return [[fact_id, 1.0] for fact_id in self.rows[channel]]


class _FakeEmbeddings:
    model_name = "fake"
    dimension = 3

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def embed_query(self, text: str) -> list[float]:
        if self.fail:
            raise RuntimeError("embedding down")
        return [1.0, 0.0, 0.0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class _FakeReranker:
    model_name = "fake-reranker"
    available = True

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        by_doc = {doc: fact_id for fact_id, doc in _DOCS.items()}
        return [self.scores.get(by_doc.get(doc, ""), 0.0) for doc in documents]


def _request(query: str, **kwargs: Any) -> RetrievalRequest:
    return RetrievalRequest(tenant_id=uuid4(), agent_version_id=uuid4(), query=query, **kwargs)


def test_rrf_fuse_rewards_agreement_and_is_deterministic() -> None:
    fused = rrf_fuse({"vector": ["a", "b", "c"], "fulltext": ["b", "a"], "exact": []})

    assert [fact_id for fact_id, _, _ in fused] == ["a", "b", "c"]
    assert fused[0][2] == ("vector", "fulltext")
    assert fused[2][2] == ("vector",)


def test_rrf_fuse_applies_channel_weights() -> None:
    fused = rrf_fuse({"vector": ["a"], "fulltext": ["b"]}, weights={"vector": 1.0, "fulltext": 3.0})
    assert fused[0][0] == "b"


@pytest.mark.asyncio
async def test_retrieve_fuses_channels_pins_exact_codes_and_reranks() -> None:
    store = _FakeStore(
        vector=["nylon_dims", "cast_dims", "social"],
        fulltext=["cast_dims", "nylon_dims", "contact"],
        exact=["bearing"],
    )
    reranker = _FakeReranker({"cast_dims": 0.9, "nylon_dims": 0.4, "bearing": 0.1, "contact": 0.2})
    retriever = FalkorGraphFactRetriever(store, _FakeEmbeddings(), reranker, rerank_pool=10)  # type: ignore[arg-type]

    result = await retriever.retrieve(_request("6211 için 320 mm döküm kasnak", max_candidates=3))

    assert result.query_codes == ("6211", "320MM")
    assert result.reranked is True and result.degraded is False
    assert result.fact_ids == ("bearing", "cast_dims", "nylon_dims")
    assert result.candidates[0].exact_code is True
    assert result.candidates[1].channels == ("vector", "fulltext")
    assert reranker.calls and reranker.calls[0][0] == "6211 için 320 mm döküm kasnak"
    assert {"vector", "fulltext", "exact", "total", "rerank"} <= set(result.timings_ms)
    assert any("db.idx.fulltext" in cypher for cypher, _ in store.queries)


@pytest.mark.asyncio
async def test_retrieve_respects_allowed_fact_scope() -> None:
    store = _FakeStore(vector=["nylon_dims", "cast_dims"], fulltext=["cast_dims"])
    retriever = FalkorGraphFactRetriever(store, _FakeEmbeddings())  # type: ignore[arg-type]

    result = await retriever.retrieve(_request("çap", allowed_fact_ids=frozenset({"cast_dims"})))

    assert result.fact_ids == ("cast_dims",)


@pytest.mark.asyncio
async def test_retrieve_degrades_when_one_channel_fails() -> None:
    store = _FakeStore(fulltext=["contact"], vector=["cast_dims"])
    retriever = FalkorGraphFactRetriever(store, _FakeEmbeddings(fail=True))  # type: ignore[arg-type]

    result = await retriever.retrieve(_request("iletişim"))

    assert result.degraded is True
    assert result.fact_ids == ("contact",)


@pytest.mark.asyncio
async def test_retrieve_is_unavailable_without_an_index_or_any_channel() -> None:
    missing = FalkorGraphFactRetriever(_FakeStore(exists=False), _FakeEmbeddings())  # type: ignore[arg-type]
    with pytest.raises(RetrievalUnavailableError, match="missing"):
        await missing.retrieve(_request("soru"))

    dead = FalkorGraphFactRetriever(
        _FakeStore(failing={"vector", "fulltext", "exact", "graph"}),
        _FakeEmbeddings(fail=True),
    )  # type: ignore[arg-type]
    with pytest.raises(RetrievalUnavailableError, match="every retrieval channel failed"):
        await dead.retrieve(_request("6211", anchor_subject_ids=("x",)))


@pytest.mark.asyncio
async def test_reranker_failure_keeps_fused_order() -> None:
    class _BrokenReranker:
        model_name = "broken"
        available = True

        async def rerank(self, query: str, documents: list[str]) -> list[float]:
            raise RuntimeError("no gpu")

    store = _FakeStore(vector=["nylon_dims", "cast_dims"])
    retriever = FalkorGraphFactRetriever(store, _FakeEmbeddings(), _BrokenReranker())  # type: ignore[arg-type]

    result = await retriever.retrieve(_request("çap"))

    assert result.reranked is False and result.degraded is True
    assert result.fact_ids == ("nylon_dims", "cast_dims")


@pytest.mark.asyncio
async def test_scoped_retriever_binds_server_resolved_identity() -> None:
    captured: list[RetrievalRequest] = []

    class _Backend:
        backend = "fake"

        async def retrieve(self, request: RetrievalRequest) -> Any:
            captured.append(request)
            from src.modules.knowledge.ports import RetrievalResult

            return RetrievalResult(candidates=[], backend="fake")

    tenant_id, version_id = uuid4(), uuid4()
    scoped = ScopedFactRetriever(_Backend(), tenant_id=tenant_id, agent_version_id=version_id)

    await scoped.retrieve("soru", anchor_subject_ids=("p",), memory_subject_ids=("m",), max_candidates=5)

    assert captured[0].tenant_id == tenant_id
    assert captured[0].agent_version_id == version_id
    assert captured[0].anchor_subject_ids == ("p",)
    assert captured[0].memory_subject_ids == ("m",)
    assert captured[0].max_candidates == 5
    assert scoped.backend == "fake"


@pytest.mark.asyncio
async def test_anchor_lineage_gets_a_bonus_after_fusion_and_rerank() -> None:
    # Vector/full-text prefer another product's dimensions; the anchored
    # product's own dimensions fact must still win, and memory subjects get a
    # weaker bonus than trusted anchors.
    store = _FakeStore(vector=["nylon_dims", "cast_dims"], fulltext=["nylon_dims"], graph=["cast_dims"])
    reranker = _FakeReranker({"nylon_dims": 0.6, "cast_dims": 0.5})
    retriever = FalkorGraphFactRetriever(store, _FakeEmbeddings(), reranker)  # type: ignore[arg-type]

    anchored = await retriever.retrieve(_request("ölçüleri neler", anchor_subject_ids=("deflection",)))
    assert anchored.fact_ids[:2] == ("cast_dims", "nylon_dims")
    assert "graph" in anchored.candidates[0].channels
    assert "memory" not in {c for cand in anchored.candidates for c in cand.channels}

    remembered = await retriever.retrieve(_request("ölçüleri neler", memory_subject_ids=("deflection",)))
    assert remembered.fact_ids[:2] == ("cast_dims", "nylon_dims")
    assert "memory" in remembered.candidates[0].channels

    plain = await retriever.retrieve(_request("ölçüleri neler"))
    assert plain.fact_ids[:2] == ("nylon_dims", "cast_dims")
    assert "graph" not in plain.timings_ms and "memory" not in plain.timings_ms
