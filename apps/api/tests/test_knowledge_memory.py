# ruff: noqa: RUF001
"""Conversation memory: constrained extraction and graph bookkeeping."""

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.integrations.llm import LLMMessage
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.knowledge.memory import (
    ConversationMemoryStore,
    CustomerMemory,
    MemoryExtraction,
    customer_key,
    extract_memory,
    memory_extraction_schema,
)


def _config() -> CompanyAgentConfig:
    return CompanyAgentConfig.model_validate(
        {
            "lifecycle": "approved",
            "organization": {"display_names": {"tr": "Örnek Şirket"}},
            "parties": [
                {"id": "customer", "kind": "person", "roles": ["customer"], "display_names": {"tr": "Müşteri"}, "profile_ids": ["quote"]}
            ],
            "offerings": [
                {"id": "alpha", "kind": "physical_product", "display_names": {"tr": "Alfa Kasnak"}},
                {"id": "beta", "kind": "physical_product", "display_names": {"tr": "Beta Kasnak"}, "active": False},
            ],
            "customer_profiles": [
                {
                    "id": "quote",
                    "applies_to": ["person"],
                    "fields": [
                        {"id": "diameter_mm", "type": "number"},
                        {"id": "city", "type": "string"},
                    ],
                }
            ],
            "agent": {
                "purposes": ["sales"],
                "supported_locales": ["tr"],
                "default_locale": "tr",
                "unknown_fact_action": "handoff",
            },
        }
    )


def test_customer_key_is_keyed_hash_without_phone() -> None:
    tenant = UUID("11111111-1111-1111-1111-111111111111")
    key = customer_key(tenant, "+905551112233", "secret")

    assert len(key) == 32 and "5551112233" not in key
    assert key == customer_key(tenant, " +905551112233 ", "secret")
    assert key != customer_key(uuid4(), "+905551112233", "secret")
    assert key != customer_key(tenant, "+905551112233", "other-secret")


def test_extraction_schema_is_constrained_to_approved_vocabulary() -> None:
    schema = memory_extraction_schema(_config())

    assert schema["properties"]["subject_ids"]["items"]["enum"] == ["alpha"]
    assert schema["$defs"]["MemoryRequirement"]["properties"]["field"]["enum"] == [
        "diameter_mm",
        "city",
    ]


@pytest.mark.asyncio
async def test_extract_memory_drops_unknown_ids_and_keeps_customer_values() -> None:
    class _LLM:
        async def complete(self, messages: list[LLMMessage], **kwargs: Any) -> str:
            assert kwargs["response_schema"]["properties"]["subject_ids"]["items"]["enum"] == ["alpha"]
            assert messages[-1].role == "assistant"
            return json.dumps(
                {
                    "subject_ids": ["alpha", "beta", "gamma"],
                    "requirements": [
                        {"field": "diameter_mm", "value": "320"},
                        {"field": "made_up", "value": "x"},
                    ],
                    "intent": "quote",
                    "sentiment": "positive",
                }
            )

    extraction = await extract_memory(
        _LLM(),
        _config(),
        [
            LLMMessage(role="user", content="Alfa kasnak 320 mm lazım"),
            LLMMessage(role="assistant", content="Teşekkürler."),
        ],
    )

    assert extraction.subject_ids == ["alpha"]
    assert [(r.field, r.value) for r in extraction.requirements] == [("diameter_mm", "320")]
    assert extraction.intent == "quote" and extraction.sentiment == "positive"


def test_prompt_context_uses_approved_labels_only() -> None:
    memory = CustomerMemory(
        subject_ids=("alpha", "unknown"), requirements=(("city", "İzmir"),), last_intent="quote", turns=2
    )
    context = memory.as_prompt_context(_config())

    assert context["previous_products"] == [{"id": "alpha", "name": "Alfa Kasnak"}]
    assert context["stated_requirements"] == {"city": "İzmir"}
    assert context["turns"] == 2
    assert CustomerMemory().empty is True and memory.empty is False


class _FakeStore:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any] | None]] = []
        self.seen_jobs: set[str] = set()

    @staticmethod
    def memory_graph_name(tenant_id: UUID) -> str:
        return f"mem_{tenant_id.hex}"

    async def graph_exists(self, name: str) -> bool:
        return True

    async def query(self, graph: str, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        self.queries.append((cypher, params))
        if cypher.startswith("CREATE INDEX"):
            raise RuntimeError("Attribute 'key' is already indexed")
        if "WHERE c.last_job_id = $job" in cypher:
            return [["key"]] if (params or {})["job"] in self.seen_jobs else []
        if cypher.startswith("MERGE (c:Customer"):
            self.seen_jobs.add((params or {})["job"])
        return []


@pytest.mark.asyncio
async def test_record_turn_is_idempotent_per_job_and_writes_only_structured_data() -> None:
    store = _FakeStore()
    memory = ConversationMemoryStore(store)  # type: ignore[arg-type]
    extraction = MemoryExtraction(
        subject_ids=["alpha"],
        requirements=[{"field": "diameter_mm", "value": "320"}],  # type: ignore[list-item]
        intent="quote",
        sentiment="neutral",
    )

    assert await memory.record_turn(tenant_id=uuid4(), key="k", extraction=extraction, job_id="job-1")
    assert not await memory.record_turn(tenant_id=uuid4(), key="k", extraction=extraction, job_id="job-1")

    written = " ".join(cypher for cypher, _ in store.queries)
    assert "INTERESTED_IN" in written and "STATED" in written
    assert "body" not in written and "phone" not in written
    subject_params = next(p for c, p in store.queries if "INTERESTED_IN" in c)
    assert subject_params is not None and subject_params["subjects"] == ["alpha"]
