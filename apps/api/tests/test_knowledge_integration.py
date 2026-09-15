# ruff: noqa: RUF001
"""Live FalkorDB + Ollama (bge-m3) retrieval on the Artı Kasnak config.

Skipped unless both services answer; run locally with ``make up`` and Ollama.
"""

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.integrations.embeddings import OllamaEmbeddingClient
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.knowledge.graph_store import GraphStore
from src.modules.knowledge.indexer import KnowledgeIndexer
from src.modules.knowledge.memory import ConversationMemoryStore, MemoryExtraction
from src.modules.knowledge.ports import RetrievalRequest
from src.modules.knowledge.retrieval import FalkorGraphFactRetriever

pytestmark = pytest.mark.integration

_FALKOR_HOST = os.environ.get("FALKORDB_HOST", "localhost")
_FALKOR_PORT = int(os.environ.get("FALKORDB_PORT", "6381"))
_OLLAMA = os.environ.get("EMBEDDING_BASE_URL", "http://127.0.0.1:11434")
_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")


def _services_available() -> bool:
    try:
        tags = httpx.get(f"{_OLLAMA.rstrip('/')}/api/tags", timeout=2.0).json()
        if not any(str(m.get("name", "")).startswith(_MODEL) for m in tags.get("models", [])):
            return False
        GraphStore(host=_FALKOR_HOST, port=_FALKOR_PORT)._ping_sync()
        return True
    except Exception:
        return False


requires_services = pytest.mark.skipif(
    not _services_available(), reason="FalkorDB or Ollama bge-m3 is not reachable"
)


def _config() -> CompanyAgentConfig:
    path = Path(__file__).resolve().parents[1] / "config" / "arti_kasnak.production.json"
    return CompanyAgentConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))


@requires_services
@pytest.mark.asyncio
async def test_index_and_hybrid_retrieval_on_live_services() -> None:
    store = GraphStore(host=_FALKOR_HOST, port=_FALKOR_PORT)
    embeddings = OllamaEmbeddingClient(base_url=_OLLAMA, model=_MODEL, dimension=1024)
    tenant_id, version_id = uuid4(), uuid4()
    graph = store.kb_graph_name(tenant_id, version_id)
    try:
        report = await KnowledgeIndexer(store, embeddings).index_version(
            tenant_id=tenant_id, agent_version_id=version_id, config=_config()
        )
        assert report.fact_count > 50 and report.code_count > 10 and not report.reused
        reused = await KnowledgeIndexer(store, embeddings).index_version(
            tenant_id=tenant_id, agent_version_id=version_id, config=_config()
        )
        assert reused.reused is True

        retriever = FalkorGraphFactRetriever(store, embeddings, None, timeout_seconds=30.0)
        golden = [
            ("6211 rulman hangi mil çapına uygundur?", "bearing_compatibility"),
            ("TS180118 kaç halat için?", "traction_sheave_available_dimensions"),
            ("Yan desteklerde hangi mil çapları var?", "side_support_standard_dimensions"),
            ("kasnaklarınız sessiz mi çalışıyo", "plastic_pulley_performance"),
            ("İhracat ekibine nasıl ulaşabilirim?", "contact_information"),
        ]
        for question, expected in golden:
            result = await retriever.retrieve(
                RetrievalRequest(
                    tenant_id=tenant_id, agent_version_id=version_id, query=question, max_candidates=3
                )
            )
            assert expected in result.fact_ids, (question, result.fact_ids)
        exact = await retriever.retrieve(
            RetrievalRequest(tenant_id=tenant_id, agent_version_id=version_id, query="6211", max_candidates=3)
        )
        assert exact.candidates[0].fact_id == "bearing_compatibility" and exact.candidates[0].exact_code

        anchored = await retriever.retrieve(
            RetrievalRequest(
                tenant_id=tenant_id,
                agent_version_id=version_id,
                query="ölçüleri neler",
                anchor_subject_ids=("deflection_pulley",),
                max_candidates=6,
            )
        )
        assert {"cast_standard_dimensions", "cast_eco_dimensions"} & set(anchored.fact_ids)

        other_tenant = await store.graph_exists(store.kb_graph_name(uuid4(), version_id))
        assert other_tenant is False
    finally:
        await store.delete_graph(graph)


@requires_services
@pytest.mark.asyncio
async def test_memory_graph_round_trip_on_live_service() -> None:
    store = GraphStore(host=_FALKOR_HOST, port=_FALKOR_PORT)
    memory = ConversationMemoryStore(store)
    tenant_id = uuid4()
    key = "a" * 32
    try:
        extraction = MemoryExtraction(
            subject_ids=["cast_elevator_pulley"],
            requirements=[{"field": "pulley_diameter_mm", "value": "320"}],  # type: ignore[list-item]
            intent="quote",
            sentiment="positive",
        )
        assert await memory.record_turn(tenant_id=tenant_id, key=key, extraction=extraction, job_id="j1")
        assert not await memory.record_turn(tenant_id=tenant_id, key=key, extraction=extraction, job_id="j1")
        assert await memory.record_turn(
            tenant_id=tenant_id,
            key=key,
            extraction=MemoryExtraction(subject_ids=["belt_pulley"], intent="information"),
            job_id="j2",
        )
        recalled = await memory.customer_memory(tenant_id=tenant_id, key=key)
        assert recalled.turns == 2
        assert recalled.subject_ids[0] == "belt_pulley"
        assert set(recalled.subject_ids) == {"belt_pulley", "cast_elevator_pulley"}
        assert recalled.requirements == (("pulley_diameter_mm", "320"),)
        assert recalled.last_intent == "information"
        assert (await memory.customer_memory(tenant_id=tenant_id, key="b" * 32)).empty
        assert await memory.forget(tenant_id=tenant_id, key=key) == 1
        assert (await memory.customer_memory(tenant_id=tenant_id, key=key)).empty
    finally:
        await store.delete_graph(store.memory_graph_name(tenant_id))
