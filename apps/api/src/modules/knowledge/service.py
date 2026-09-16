"""Factories wiring settings to the knowledge ports.

Every caller goes through here so the deployment (backend, embedding profile,
reranker) is described by ``Settings`` alone and can be switched off to the
lexical retriever without touching the runtime.
"""

from __future__ import annotations

from uuid import UUID

from src.core.config import get_settings
from src.integrations.embeddings import get_embedding_client
from src.integrations.reranker import get_reranker

from .evidence import EvidenceGraph, EvidenceRetriever, ScopedEvidenceRetriever
from .graph_store import GraphStore, get_graph_store
from .indexer import KnowledgeIndexer
from .memory import ConversationMemoryStore, customer_key
from .ports import EvidenceSearch, FactRetriever, KnowledgeRetriever
from .retrieval import FalkorGraphFactRetriever, ScopedFactRetriever


def knowledge_enabled() -> bool:
    s = get_settings()
    return s.knowledge_backend == "falkordb" and s.embedding_provider in {"ollama", "nim"}


def build_fact_retriever(store: GraphStore | None = None) -> FactRetriever | None:
    if not knowledge_enabled():
        return None
    s = get_settings()
    return FalkorGraphFactRetriever(
        store or get_graph_store(),
        get_embedding_client(),
        get_reranker() if s.reranker_enabled else None,
        max_candidates=s.retrieval_max_candidates,
        rerank_pool=s.retrieval_rerank_pool,
        timeout_seconds=s.retrieval_timeout_seconds,
    )


def build_scoped_retriever(*, tenant_id: UUID, agent_version_id: UUID) -> KnowledgeRetriever | None:
    retriever = build_fact_retriever()
    if retriever is None:
        return None
    return ScopedFactRetriever(retriever, tenant_id=tenant_id, agent_version_id=agent_version_id)


def build_evidence_graph(store: GraphStore | None = None) -> EvidenceGraph | None:
    """Per-tenant chunk graph for ingestion; ``None`` keeps ingestion text-only."""

    if not knowledge_enabled():
        return None
    return EvidenceGraph(store or get_graph_store(), get_embedding_client())


def build_evidence_retriever(store: GraphStore | None = None) -> EvidenceRetriever | None:
    if not knowledge_enabled():
        return None
    s = get_settings()
    return EvidenceRetriever(
        store or get_graph_store(),
        get_embedding_client(),
        get_reranker() if s.reranker_enabled else None,
        timeout_seconds=s.retrieval_timeout_seconds,
    )


def build_scoped_evidence_retriever(*, tenant_id: UUID) -> EvidenceSearch | None:
    retriever = build_evidence_retriever()
    if retriever is None:
        return None
    return ScopedEvidenceRetriever(retriever, tenant_id=tenant_id)


def build_indexer(store: GraphStore | None = None) -> KnowledgeIndexer:
    return KnowledgeIndexer(store or get_graph_store(), get_embedding_client())


def build_memory_store(store: GraphStore | None = None) -> ConversationMemoryStore | None:
    if not knowledge_enabled() or not get_settings().memory_enrichment_enabled:
        return None
    return ConversationMemoryStore(store or get_graph_store())


def memory_key(tenant_id: UUID, contact_identity: str) -> str:
    return customer_key(tenant_id, contact_identity, get_settings().app_secret_key)
