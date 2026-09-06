# ruff: noqa: RUF001
"""Regression coverage for the isolated Denizkolik company-space fixture."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from src.integrations.llm import NullLLMClient
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import (
    CompanyAgentRuntime,
    RuntimeInteractionKind,
    build_customer_decision_schema,
)

_CONFIG_PATH = Path(__file__).parents[1] / "config" / "denizkolik.test.json"


@lru_cache
def _config() -> CompanyAgentConfig:
    return CompanyAgentConfig.model_validate_json(_CONFIG_PATH.read_text())


def _candidate_fact_ids(message: str) -> list[str]:
    schema = build_customer_decision_schema(_config(), customer_message=message)
    return schema["properties"]["fact_ids"]["items"].get("enum", [])


def test_denizkolik_config_is_publishable_and_keeps_whatsapp_menu_bounded() -> None:
    config = _config()
    catalog_children = [
        relationship
        for relationship in config.relationships
        if relationship.predicate == "part_of"
        and relationship.object_id == "product_catalog"
    ]

    assert config.is_publishable() is True
    assert config.publishability_errors() == []
    assert config.organization is not None
    assert config.organization.display_names["tr"] == "Denizkolik"
    assert len(catalog_children) == 10


@pytest.mark.asyncio
async def test_plain_product_command_opens_the_ten_category_list() -> None:
    turn = await CompanyAgentRuntime(_config(), NullLLMClient()).reply("ürünleri göster")

    assert turn.fact_ids == ("all_product_groups",)
    assert turn.interaction is not None
    assert turn.interaction.kind == RuntimeInteractionKind.LIST
    assert len(turn.interaction.options) == 10


@pytest.mark.parametrize(
    ("message", "expected_fact_id", "excluded_fact_id"),
    [
        ("Balık bulucu arıyorum", "fish_finders_overview", "transducers_overview"),
        ("Sanal çapa seçenekleri neler?", "virtual_anchors_overview", "pivot_overview"),
        ("Tekne için akü ve şarj ekipmanı var mı?", "energy_storage_overview", "itlp_lifepo4_overview"),
    ],
)
def test_generic_category_queries_do_not_jump_to_a_child_product(
    message: str,
    expected_fact_id: str,
    excluded_fact_id: str,
) -> None:
    fact_ids = _candidate_fact_ids(message)

    assert expected_fact_id in fact_ids
    assert excluded_fact_id not in fact_ids


def test_turkish_lira_phrase_routes_to_live_price_policy() -> None:
    assert _candidate_fact_ids("XPLORE bugün kaç TL?") == ["current_price_policy"]


def test_company_name_question_routes_only_to_company_overview() -> None:
    assert _candidate_fact_ids("Şirketinizin adı nedir?") == ["company_overview"]


@pytest.mark.parametrize(
    ("message", "expected_first_fact_id"),
    [
        ("Selam, biraz bakınıyorum", "conversation_greeting"),
        ("Niye cevap vermiyorsunuz?", "conversation_waiting"),
    ],
)
def test_explicit_social_language_ranks_the_matching_behavior_first(
    message: str,
    expected_first_fact_id: str,
) -> None:
    assert _candidate_fact_ids(message)[0] == expected_first_fact_id


def test_dynamic_commerce_values_are_not_frozen_into_the_fixture() -> None:
    raw = _CONFIG_PATH.read_text()

    assert "790.00 USD" not in raw
    assert "2,399.00 USD" not in raw
    assert "TR79 0006" not in raw
    assert '"static_prices_allowed": false' in raw
    assert '"static_stock_allowed": false' in raw
