"""Build the per-version knowledge graph from an approved company config.

Graph layout (one FalkorDB graph per tenant + agent version):

    (:Meta {fingerprint, fact_count, embedding_model, dimension, built_at})
    (:Company {id, name})   (:Offering {id, name, kind, active})   (:Party {id})
    (:Offering)-[:REL {predicate}]->(:Offering|:Company)     -- config.relationships
    (:Fact {id, subject_id, category, text, search_document, embedding, ...})
    (:Fact)-[:ABOUT]->(subject)
    (:Code {value})-[:CODE_OF]->(:Fact)                        -- exact-match keys

Only customer-visible facts with approved customer text are indexed; internal
``Fact.value`` and sources are never written. The graph is rebuilt atomically
per version (delete + create) and skipped when the fingerprint is unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID

from src.integrations.embeddings import EmbeddingClient
from src.modules.agents.company_config import CompanyAgentConfig

from .compiler import compile_search_document, content_hash, fact_codes, index_fingerprint
from .graph_store import GraphStore

_LANGUAGE_BY_LOCALE = {
    "tr": "turkish",
    "en": "english",
    "de": "german",
    "ru": "russian",
    "ar": "arabic",
}
_FACT_BATCH = 32


@dataclass(frozen=True)
class IndexReport:
    graph_name: str
    fact_count: int
    offering_count: int
    relationship_count: int
    code_count: int
    embedding_model: str
    dimension: int
    fingerprint: str
    elapsed_ms: float
    reused: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def fulltext_language(locale: str) -> str:
    return _LANGUAGE_BY_LOCALE.get(locale.split("-")[0].lower(), "english")


class KnowledgeIndexer:
    def __init__(self, store: GraphStore, embeddings: EmbeddingClient) -> None:
        self.store = store
        self.embeddings = embeddings

    async def current_fingerprint(self, graph_name: str) -> str | None:
        if not await self.store.graph_exists(graph_name):
            return None
        rows = await self.store.query(graph_name, "MATCH (m:Meta) RETURN m.fingerprint LIMIT 1")
        if not rows or not isinstance(rows[0][0], str):
            return None
        return rows[0][0]

    async def index_version(
        self,
        *,
        tenant_id: UUID,
        agent_version_id: UUID,
        config: CompanyAgentConfig,
        rebuild: bool = False,
    ) -> IndexReport:
        assert config.agent is not None
        started = perf_counter()
        graph_name = self.store.kb_graph_name(tenant_id, agent_version_id)
        locale = config.agent.default_locale
        visible = [fact for fact in config.facts if fact.customer_visible and fact.customer_text]
        documents = [(fact.id, compile_search_document(config, fact)) for fact in visible]
        fingerprint = index_fingerprint(documents, self.embeddings.model_name)

        if not rebuild and await self.current_fingerprint(graph_name) == fingerprint:
            counts = await self.store.query(
                graph_name,
                "MATCH (m:Meta) RETURN m.fact_count, m.offering_count, "
                "m.relationship_count, m.code_count LIMIT 1",
            )
            row = counts[0] if counts else [len(visible), 0, 0, 0]
            return IndexReport(
                graph_name=graph_name,
                fact_count=int(row[0] or 0),
                offering_count=int(row[1] or 0),
                relationship_count=int(row[2] or 0),
                code_count=int(row[3] or 0),
                embedding_model=self.embeddings.model_name,
                dimension=self.embeddings.dimension,
                fingerprint=fingerprint,
                elapsed_ms=(perf_counter() - started) * 1000,
                reused=True,
            )

        # Embed before touching the graph so an embedding outage never leaves
        # a half-built index behind.
        vectors = await self.embeddings.embed_documents([document for _, document in documents])

        await self.store.delete_graph(graph_name)
        await self._create_indexes(graph_name, locale)

        company_id = config.organization.id if config.organization else "company"
        company_name = ""
        if config.organization is not None:
            company_name = config.organization.display_names.get(locale) or next(
                iter(config.organization.display_names.values()), ""
            )
        await self.store.query(
            graph_name,
            "MERGE (c:Company {id: $id}) SET c.name = $name",
            {"id": company_id, "name": company_name},
        )
        offering_rows = [
            {
                "id": offering.id,
                "name": offering.display_names.get(locale)
                or next(iter(offering.display_names.values()), offering.id),
                "kind": offering.kind.value,
                "active": offering.active,
            }
            for offering in config.offerings
        ]
        if offering_rows:
            await self.store.query(
                graph_name,
                "UNWIND $rows AS r MERGE (o:Offering {id: r.id}) "
                "SET o.name = r.name, o.kind = r.kind, o.active = r.active",
                {"rows": offering_rows},
            )
        party_rows = [{"id": party.id} for party in config.parties]
        if party_rows:
            await self.store.query(
                graph_name,
                "UNWIND $rows AS r MERGE (p:Party {id: r.id})",
                {"rows": party_rows},
            )
        relationship_rows = [
            {
                "subject_id": relationship.subject_id,
                "predicate": relationship.predicate,
                "object_id": relationship.object_id,
            }
            for relationship in config.relationships
        ]
        if relationship_rows:
            await self.store.query(
                graph_name,
                "UNWIND $rows AS r MATCH (s {id: r.subject_id}), (t {id: r.object_id}) "
                "MERGE (s)-[:REL {predicate: r.predicate}]->(t)",
                {"rows": relationship_rows},
            )

        fact_rows: list[dict[str, Any]] = []
        code_rows: list[dict[str, str]] = []
        for fact, (_, document), vector in zip(visible, documents, vectors, strict=True):
            fact_rows.append(
                {
                    "id": fact.id,
                    "subject_id": fact.subject_id,
                    "category": fact.category.value,
                    "text": (fact.customer_text or {}).get(locale)
                    or next(iter((fact.customer_text or {}).values()), ""),
                    "search_document": document,
                    "search_terms": list(fact.search_terms),
                    "guidance": (fact.selection_guidance or {}).get(locale, ""),
                    "content_hash": content_hash(document, self.embeddings.model_name),
                    "embedding": vector,
                }
            )
            for code in fact_codes(config, fact):
                code_rows.append({"fact_id": fact.id, "code": code})

        for start in range(0, len(fact_rows), _FACT_BATCH):
            batch = fact_rows[start : start + _FACT_BATCH]
            await self.store.query(
                graph_name,
                "UNWIND $rows AS r CREATE (f:Fact {id: r.id, subject_id: r.subject_id, "
                "category: r.category, text: r.text, search_document: r.search_document, "
                "search_terms: r.search_terms, guidance: r.guidance, "
                "content_hash: r.content_hash}) SET f.embedding = vecf32(r.embedding)",
                {"rows": batch},
            )
        if fact_rows:
            await self.store.query(
                graph_name,
                "UNWIND $rows AS r MATCH (f:Fact {id: r.id}) MATCH (s {id: r.subject_id}) "
                "MERGE (f)-[:ABOUT]->(s)",
                {"rows": [{"id": row["id"], "subject_id": row["subject_id"]} for row in fact_rows]},
            )
        if code_rows:
            await self.store.query(
                graph_name,
                "UNWIND $rows AS r MATCH (f:Fact {id: r.fact_id}) "
                "MERGE (c:Code {value: r.code}) MERGE (c)-[:CODE_OF]->(f)",
                {"rows": code_rows},
            )
        await self.store.query(
            graph_name,
            "CREATE (:Meta {fingerprint: $fingerprint, fact_count: $fact_count, "
            "offering_count: $offering_count, relationship_count: $relationship_count, "
            "code_count: $code_count, embedding_model: $model, dimension: $dimension, "
            "built_at: $built_at, tenant_id: $tenant_id, agent_version_id: $version_id})",
            {
                "fingerprint": fingerprint,
                "fact_count": len(fact_rows),
                "offering_count": len(offering_rows),
                "relationship_count": len(relationship_rows),
                "code_count": len(code_rows),
                "model": self.embeddings.model_name,
                "dimension": self.embeddings.dimension,
                "built_at": datetime.now(UTC).isoformat(),
                "tenant_id": str(tenant_id),
                "version_id": str(agent_version_id),
            },
        )
        return IndexReport(
            graph_name=graph_name,
            fact_count=len(fact_rows),
            offering_count=len(offering_rows),
            relationship_count=len(relationship_rows),
            code_count=len(code_rows),
            embedding_model=self.embeddings.model_name,
            dimension=self.embeddings.dimension,
            fingerprint=fingerprint,
            elapsed_ms=(perf_counter() - started) * 1000,
            reused=False,
        )

    async def _create_indexes(self, graph_name: str, locale: str) -> None:
        dimension = int(self.embeddings.dimension)
        language = fulltext_language(locale)
        statements = [
            "CREATE INDEX FOR (f:Fact) ON (f.id)",
            "CREATE INDEX FOR (o:Offering) ON (o.id)",
            "CREATE INDEX FOR (c:Code) ON (c.value)",
            (
                "CREATE VECTOR INDEX FOR (f:Fact) ON (f.embedding) "
                f"OPTIONS {{dimension: {dimension}, similarityFunction: 'cosine'}}"
            ),
            (
                "CALL db.idx.fulltext.createNodeIndex("
                f"{{label: 'Fact', language: '{language}'}}, 'search_document')"
            ),
        ]
        for statement in statements:
            await self.store.query(graph_name, statement)
