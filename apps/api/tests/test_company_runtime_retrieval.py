# ruff: noqa: RUF001
"""The GraphRAG retriever may only re-order approved facts; every gate stays."""

import json
from pathlib import Path
from typing import Any

import pytest

from src.integrations.llm import LLMMessage
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import (
    CompanyAgentRuntime,
    CustomerReplyAction,
    _visible_facts,
    build_customer_decision_schema,
    build_customer_system_prompt,
)
from src.modules.agents.semantic_dialogue import (
    Request,
    RequestPlan,
    request_candidates,
    retrieve_for_plan,
)
from src.modules.knowledge.memory import CustomerMemory
from src.modules.knowledge.ports import FactCandidate, RetrievalResult, RetrievalUnavailableError


def _config() -> CompanyAgentConfig:
    return CompanyAgentConfig.model_validate(
        {
            "lifecycle": "approved",
            "organization": {"display_names": {"tr": "Örnek Şirket"}},
            "offerings": [
                {"id": "alpha", "kind": "physical_product", "display_names": {"tr": "Alfa Kasnak"}},
                {"id": "beta", "kind": "physical_product", "display_names": {"tr": "Beta Kasnak"}},
            ],
            "facts": [
                {
                    "id": "alpha-material",
                    "subject_id": "alpha",
                    "category": "specification",
                    "value": "cast",
                    "source": "catalog",
                    "customer_visible": True,
                    "customer_text": {"tr": "Alfa kasnak GG-25 dökümdür."},
                },
                {
                    "id": "alpha-noise",
                    "subject_id": "alpha",
                    "category": "capability",
                    "value": "quiet",
                    "source": "catalog",
                    "customer_visible": True,
                    "customer_text": {"tr": "Alfa kasnak sessiz çalışır."},
                },
                {
                    "id": "beta-material",
                    "subject_id": "beta",
                    "category": "specification",
                    "value": "nylon",
                    "source": "catalog",
                    "customer_visible": True,
                    "customer_text": {"tr": "Beta kasnak MC Nylon'dur."},
                },
                {
                    "id": "support-contact",
                    "subject_id": "company",
                    "category": "support",
                    "value": "phone",
                    "source": "website",
                    "customer_visible": True,
                    "customer_text": {"tr": "Destek için 0212 000 00 00."},
                    "search_terms": ["destek", "iletişim"],
                },
                {
                    "id": "internal-margin",
                    "subject_id": "alpha",
                    "category": "other",
                    "value": "secret",
                    "source": "finance",
                },
            ],
            "agent": {
                "purposes": ["information", "sales"],
                "supported_locales": ["tr"],
                "default_locale": "tr",
                "unknown_fact_action": "handoff",
                "handoff_fact_id": "support-contact",
            },
        }
    )


def _arti_kasnak() -> CompanyAgentConfig:
    path = Path(__file__).resolve().parents[1] / "config" / "arti_kasnak.production.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["whatsapp_presentation"] = None
    data["agent"]["semantic_dialogue"] = None
    return CompanyAgentConfig.model_validate(data)


class _StubRetriever:
    backend = "stub"

    def __init__(self, *fact_ids: str, fail: bool = False) -> None:
        self.fact_ids = fact_ids
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def retrieve(
        self,
        query: str,
        *,
        anchor_subject_ids: tuple[str, ...] = (),
        memory_subject_ids: tuple[str, ...] = (),
        allowed_fact_ids: frozenset[str] | None = None,
        max_candidates: int = 12,
    ) -> RetrievalResult:
        self.calls.append(
            {
                "query": query,
                "anchors": anchor_subject_ids,
                "memory": memory_subject_ids,
                "max": max_candidates,
            }
        )
        if self.fail:
            raise RetrievalUnavailableError("index missing")
        return RetrievalResult(
            candidates=[
                FactCandidate(fact_id=fact_id, score=1.0 - 0.1 * rank, channels=("vector",))
                for rank, fact_id in enumerate(self.fact_ids)
            ],
            backend="stub",
            timings_ms={"total": 12.0},
        )


class _RecordingLLM:
    def __init__(self, *fact_ids: str) -> None:
        self.fact_ids = fact_ids
        self.system = ""
        self.schema: dict[str, object] | None = None

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        self.system = system
        self.schema = response_schema
        return json.dumps({"action": "reply", "fact_ids": list(self.fact_ids)})


def _ids(facts: list[dict[str, str]]) -> list[str]:
    return [fact["id"] for fact in facts]


def test_candidate_order_is_honoured_and_unknown_ids_are_ignored() -> None:
    selected = _visible_facts(
        _config(),
        customer_message="sessiz çalışıyor mu",
        candidate_fact_ids=("support-contact", "alpha-noise", "internal-margin", "ghost"),
    )

    assert _ids(selected) == ["support-contact", "alpha-noise"]


def test_named_product_keeps_its_own_facts_first_and_excludes_siblings() -> None:
    selected = _visible_facts(
        _config(),
        customer_message="Alfa kasnak malzemesi nedir?",
        candidate_fact_ids=("beta-material", "support-contact", "alpha-material"),
    )

    ids = _ids(selected)
    assert "beta-material" not in ids
    assert ids[:2] == ["alpha-material", "alpha-noise"]
    assert "support-contact" in ids


def test_protected_price_intent_stays_fail_closed_with_candidates() -> None:
    config = _config()
    with_candidates = _visible_facts(
        config,
        customer_message="Alfa kasnak fiyatı ne kadar?",
        candidate_fact_ids=("alpha-material", "alpha-noise", "support-contact"),
    )
    lexical = _visible_facts(config, customer_message="Alfa kasnak fiyatı ne kadar?")

    assert _ids(with_candidates) == _ids(lexical) == []


def test_arti_kasnak_candidates_never_cross_product_families() -> None:
    config = _arti_kasnak()
    selected = _visible_facts(
        config,
        customer_message="Kayış kasnağı hangi malzemeden üretiliyor?",
        candidate_fact_ids=(
            "cast_pulley_materials",
            "steel_belt_pulley_details",
            "plastic_belt_pulley_details",
            "mc_nylon_standard_dimensions",
        ),
    )

    ids = _ids(selected)
    assert "cast_pulley_materials" not in ids
    assert "mc_nylon_standard_dimensions" not in ids
    assert ids[:2] == ["steel_belt_pulley_details", "plastic_belt_pulley_details"]


def test_prompt_and_schema_share_the_retrieved_candidate_set() -> None:
    config = _config()
    candidates = ("support-contact", "alpha-noise")
    prompt = build_customer_system_prompt(
        config,
        customer_message="destek",
        candidate_fact_ids=candidates,
        customer_memory=CustomerMemory(subject_ids=("beta",), turns=3),
    )
    schema = build_customer_decision_schema(
        config, customer_message="destek", candidate_fact_ids=candidates
    )

    fact_ids: Any = schema["properties"]["fact_ids"]  # type: ignore[index]
    assert fact_ids["items"]["enum"] == ["support-contact", "alpha-noise"]
    assert '"customer_memory"' in prompt
    assert "Beta Kasnak" in prompt
    assert "internal-margin" not in prompt


def test_empty_memory_is_not_projected() -> None:
    prompt = build_customer_system_prompt(
        _config(), customer_message="destek", customer_memory=CustomerMemory()
    )
    assert "customer_memory" not in prompt.split("Approved context:")[1]


@pytest.mark.asyncio
async def test_runtime_uses_retriever_once_and_records_the_trace() -> None:
    retriever = _StubRetriever("alpha-noise", "support-contact")
    llm = _RecordingLLM("alpha-noise")
    runtime = CompanyAgentRuntime(
        _config(),
        llm,
        fact_retriever=retriever,
        customer_memory=CustomerMemory(subject_ids=("alpha",), turns=1),
    )

    turn = await runtime.reply("sessiz mi", context_fact_ids=("alpha-material",))

    assert turn.action == CustomerReplyAction.REPLY
    assert turn.reply == "Alfa kasnak sessiz çalışır."
    assert len(retriever.calls) == 1
    assert retriever.calls[0]["anchors"] == ("alpha",)
    assert retriever.calls[0]["memory"] == ("alpha",)
    assert turn.retrieval is not None
    assert turn.retrieval["status"] == "ok"
    assert turn.retrieval["backend"] == "stub"
    assert [c["id"] for c in turn.retrieval["candidates"]] == ["alpha-noise", "support-contact"]  # type: ignore[index]
    enum: Any = llm.schema["properties"]["fact_ids"]["items"]["enum"]  # type: ignore[index]
    assert enum == ["alpha-noise", "support-contact"]


@pytest.mark.asyncio
async def test_runtime_falls_back_to_lexical_when_retrieval_is_unavailable() -> None:
    llm = _RecordingLLM("support-contact")
    runtime = CompanyAgentRuntime(
        _config(), llm, fact_retriever=_StubRetriever(fail=True)
    )

    turn = await runtime.reply("destek nasıl alırım")

    assert turn.reply == "Destek için 0212 000 00 00."
    assert turn.retrieval == {
        "backend": "stub",
        "status": "fallback_lexical",
        "error": "RetrievalUnavailableError",
    }


@pytest.mark.asyncio
async def test_runtime_without_retriever_has_no_trace() -> None:
    turn = await CompanyAgentRuntime(_config(), _RecordingLLM("support-contact")).reply("destek")
    assert turn.retrieval is None


def test_semantic_request_candidates_rerank_only_inside_scope() -> None:
    config = _arti_kasnak()
    request = Request(subject_id="cast_elevator_pulley", topic="details", question="malzemesi nedir")
    baseline = request_candidates(config, request)
    own = [fact.id for fact in baseline if fact.subject_id == request.subject_id]
    assert len(own) >= 2
    reversed_own = tuple(reversed(own))

    reranked = request_candidates(config, request, (*reversed_own, "contact_information"))

    # The retriever re-orders the product's own facts, but the subject-first
    # rule and the lineage/category gates are untouched.
    assert [fact.id for fact in reranked[: len(own)]] == list(reversed_own)
    assert [fact.id for fact in reranked[len(own) :]] == [
        fact.id for fact in baseline[len(own) :]
    ]
    assert "contact_information" not in {fact.id for fact in reranked}


@pytest.mark.asyncio
async def test_retrieve_for_plan_is_fail_open_and_skips_social() -> None:
    config = _arti_kasnak()
    plan = RequestPlan(
        requests=[
            Request(subject_id="cast_elevator_pulley", topic="details", question="ölçüleri"),
            Request(subject_id="company", topic="social", question="teşekkürler"),
        ]
    )
    failing = _StubRetriever(fail=True)
    ranked, audit = await retrieve_for_plan(failing, config, plan, None)

    assert ranked == {}
    assert audit is not None
    assert [entry["index"] for entry in audit["requests"]] == [0]  # type: ignore[index]
    assert audit["requests"][0]["status"] == "fallback_lexical"  # type: ignore[index]

    working = _StubRetriever("cast_pulley_materials")
    ranked, audit = await retrieve_for_plan(
        working, config, plan, CustomerMemory(subject_ids=("deflection_pulley",))
    )
    assert ranked == {0: ("cast_pulley_materials",)}
    assert working.calls[0]["anchors"] == ("cast_elevator_pulley",)
    assert working.calls[0]["memory"] == ("deflection_pulley",)
    assert audit is not None and audit["memory_subjects"] == ["deflection_pulley"]
