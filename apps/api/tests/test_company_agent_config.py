"""Pure validation tests for the universal CompanyAgentConfig foundation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.modules.agents.company_config import CompanyAgentConfig


def _valid_config() -> dict[str, object]:
    return {
        "schema_version": "company-agent-config/1.0",
        "lifecycle": "approved",
        "organization": {
            "id": "company",
            "display_names": {"tr-TR": "Örnek Şirket"},
            "markets": ["TR"],
            "customer_links": [
                {
                    "id": "website",
                    "kind": "website",
                    "display_names": {"tr-TR": "Web sitesi"},
                    "url": "https://example.test",
                }
            ],
        },
        "parties": [
            {
                "id": "b2b_buyer",
                "kind": "organization",
                "roles": ["buyer"],
                "profile_ids": ["b2b_customer"],
            }
        ],
        "offerings": [
            {
                "id": "consulting",
                "kind": "professional_service",
                "display_names": {"tr-TR": "Satis Danismanligi"},
                "provider_id": "company",
                "customer_links": [
                    {
                        "id": "service-page",
                        "kind": "product_page",
                        "display_names": {"tr-TR": "Hizmeti incele"},
                        "url": "https://example.test/services/consulting",
                    }
                ],
            }
        ],
        "relationships": [
            {"subject_id": "company", "predicate": "offers", "object_id": "consulting"}
        ],
        "facts": [
            {
                "id": "minimum_project_size",
                "subject_id": "consulting",
                "category": "commercial_rule",
                "value": {"minimum_days": 5},
                "customer_visible": True,
                "customer_text": {"tr-TR": "Minimum calisma kapsami 5 gundur."},
                "source": "admin_confirmed",
            }
        ],
        "customer_profiles": [
            {
                "id": "b2b_customer",
                "applies_to": ["organization"],
                "fields": [
                    {"id": "employee_count", "type": "integer", "required": False},
                    {
                        "id": "industry",
                        "type": "enum",
                        "required": True,
                        "allowed_values": ["construction", "retail", "saas"],
                    },
                ],
            }
        ],
        "processes": [
            {
                "id": "sales_inquiry",
                "states": ["new", "qualified", "handoff"],
                "initial_state": "new",
                "allowed_actions": ["ask_slot", "answer_fact", "handoff"],
                "transitions": [
                    {
                        "from_state": "new",
                        "event": "customer_message",
                        "to_state": "qualified",
                        "action": "ask_slot",
                    }
                ],
            }
        ],
        "policies": [
            {
                "id": "unknown_price",
                "when": [{"field": "intent", "operator": "equals", "value": "price"}],
                "effect": "use_template",
                "template_fact_id": "minimum_project_size",
            }
        ],
        "agent": {
            "response_mode": "grounded",
            "purposes": ["sales", "lead_capture"],
            "supported_locales": ["tr-TR", "en-GB"],
            "default_locale": "tr-TR",
        },
        "modules": [
            {
                "id": "professional-services",
                "schema_version": "1.0.0",
                "config": {"billing_unit": "day"},
            }
        ],
    }


def test_universal_config_accepts_a_non_manufacturing_company() -> None:
    config = CompanyAgentConfig.model_validate(_valid_config())

    assert config.organization is not None
    assert config.organization.display_names["tr-TR"] == "Örnek Şirket"
    assert config.facts[0].value == {"minimum_days": 5}
    assert config.is_publishable() is True


def test_offering_can_define_a_customer_visible_overview_and_menu_label() -> None:
    payload = _valid_config()
    offering = payload["offerings"][0]
    assert isinstance(offering, dict)
    offering["interaction_labels"] = {"tr-TR": "Hizmet detayi"}
    offering["overview_fact_id"] = "minimum_project_size"

    config = CompanyAgentConfig.model_validate(payload)

    assert config.offerings[0].interaction_labels == {"tr-TR": "Hizmet detayi"}
    assert config.offerings[0].overview_fact_id == "minimum_project_size"


def test_offering_overview_fact_must_be_visible_and_share_subject() -> None:
    payload = _valid_config()
    offering = payload["offerings"][0]
    assert isinstance(offering, dict)
    offering["overview_fact_id"] = "minimum_project_size"
    fact = payload["facts"][0]
    assert isinstance(fact, dict)
    fact["subject_id"] = "company"

    with pytest.raises(ValidationError, match="overview fact must have the same subject"):
        CompanyAgentConfig.model_validate(payload)


def test_whatsapp_presentation_accepts_an_approved_product_image() -> None:
    payload = _valid_config()
    payload["schema_version"] = "company-agent-config/1.2"
    payload["whatsapp_presentation"] = {
        "assets": [
            {
                "id": "consulting-image",
                "kind": "image",
                "url": "https://media.example.test/consulting.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 12345,
                "provenance": "company CMS product record",
            }
        ],
        "offering_media": {"consulting": "consulting-image"},
    }

    config = CompanyAgentConfig.model_validate(payload)

    assert config.whatsapp_presentation is not None
    assert config.whatsapp_presentation.offering_media == {
        "consulting": "consulting-image"
    }


@pytest.mark.parametrize(
    ("presentation", "error"),
    [
        (
            {
                "assets": [],
                "offering_media": {"consulting": "missing-image"},
            },
            "known media asset",
        ),
        (
            {
                "assets": [
                    {
                        "id": "bad-image",
                        "kind": "image",
                        "url": "https://media.example.test/product.webp",
                        "mime_type": "image/webp",
                        "size_bytes": 12345,
                        "provenance": "company CMS product record",
                    }
                ],
                "offering_media": {},
            },
            "unsupported WhatsApp image MIME type",
        ),
        (
            {
                "assets": [
                    {
                        "id": "other-image",
                        "kind": "image",
                        "url": "https://media.example.test/product.jpg",
                        "mime_type": "image/jpeg",
                        "size_bytes": 12345,
                        "provenance": "company CMS product record",
                    }
                ],
                "offering_media": {"unknown-offering": "other-image"},
            },
            "known offering",
        ),
    ],
)
def test_whatsapp_presentation_rejects_unsafe_references(
    presentation: dict[str, object],
    error: str,
) -> None:
    payload = _valid_config()
    payload["schema_version"] = "company-agent-config/1.2"
    payload["whatsapp_presentation"] = presentation

    with pytest.raises(ValidationError, match=error):
        CompanyAgentConfig.model_validate(payload)


def test_empty_envelope_is_valid_but_not_publishable() -> None:
    config = CompanyAgentConfig.empty()

    assert config.lifecycle == "draft"
    assert config.organization is None
    assert config.is_publishable() is False


def test_draft_can_be_built_out_of_order_by_the_conversational_builder() -> None:
    config = CompanyAgentConfig.model_validate(
        {
            "lifecycle": "draft",
            "offerings": [
                {
                    "id": "future_service",
                    "kind": "professional_service",
                    "display_names": {"tr-TR": "Hizmet"},
                }
            ],
        }
    )

    assert config.offerings[0].provider_id == "company"


def test_schema_rejects_unknown_top_level_fields() -> None:
    payload = _valid_config()
    payload["unbounded_blob"] = {"anything": "goes"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CompanyAgentConfig.model_validate(payload)


def test_customer_links_require_https_and_unique_ids() -> None:
    payload = _valid_config()
    organization = payload["organization"]
    assert isinstance(organization, dict)
    links = organization["customer_links"]
    assert isinstance(links, list)
    link = links[0]
    assert isinstance(link, dict)
    link["url"] = "http://example.test"

    with pytest.raises(ValidationError, match="String should match pattern"):
        CompanyAgentConfig.model_validate(payload)

    link["url"] = "https://example.test"
    links.append(dict(link))
    with pytest.raises(ValidationError, match="organization link ids must be unique"):
        CompanyAgentConfig.model_validate(payload)


def test_schema_rejects_dangling_graph_edges() -> None:
    payload = _valid_config()
    relationships = payload["relationships"]
    assert isinstance(relationships, list)
    relationships[0] = {
        "subject_id": "company",
        "predicate": "offers",
        "object_id": "not_an_entity",
    }

    with pytest.raises(ValidationError, match="relationships must refer to known entity ids"):
        CompanyAgentConfig.model_validate(payload)


def test_schema_rejects_a_party_with_an_unknown_customer_profile() -> None:
    payload = _valid_config()
    parties = payload["parties"]
    assert isinstance(parties, list)
    party = parties[0]
    assert isinstance(party, dict)
    party["profile_ids"] = ["unknown_profile"]

    with pytest.raises(ValidationError, match="refers to an unknown customer profile"):
        CompanyAgentConfig.model_validate(payload)


def test_visible_fact_requires_customer_facing_wording() -> None:
    payload = _valid_config()
    facts = payload["facts"]
    assert isinstance(facts, list)
    fact = facts[0]
    assert isinstance(fact, dict)
    fact.pop("customer_text")

    with pytest.raises(ValidationError, match="customer_visible facts require customer_text"):
        CompanyAgentConfig.model_validate(payload)


def test_json_schema_has_a_closed_root_contract() -> None:
    schema = CompanyAgentConfig.model_json_schema()

    assert schema["additionalProperties"] is False
    version_schema = schema["properties"]["schema_version"]
    assert version_schema["default"] == "company-agent-config/1.2"
    assert set(version_schema["enum"]) == {
        "company-agent-config/1.0",
        "company-agent-config/1.1",
        "company-agent-config/1.2",
    }


@pytest.mark.parametrize(
    "conversation_feature",
    [
        "social_category",
        "selection_guidance",
        "semantic_fallback",
        "starter_actions",
    ],
)
def test_v1_0_rejects_v1_1_conversation_fields(conversation_feature: str) -> None:
    payload = _valid_config()
    facts = payload["facts"]
    agent = payload["agent"]
    assert isinstance(facts, list)
    assert isinstance(agent, dict)
    fact = facts[0]
    assert isinstance(fact, dict)

    if conversation_feature == "social_category":
        fact["category"] = "social"
    elif conversation_feature == "selection_guidance":
        fact["selection_guidance"] = {"tr-TR": "Bu davranisi sec."}
    elif conversation_feature == "semantic_fallback":
        agent["semantic_fallback_fact_ids"] = ["minimum_project_size"]
    else:
        agent["starter_trigger_fact_ids"] = ["minimum_project_size"]
        agent["starter_actions"] = [
            {
                "fact_id": "minimum_project_size",
                "display_names": {"tr-TR": "Detay"},
            }
        ]

    with pytest.raises(
        ValidationError,
        match=r"company-agent-config/1.0 does not support conversation fields",
    ):
        CompanyAgentConfig.model_validate(payload)


def test_approved_config_requires_an_explicit_agent_purpose() -> None:
    payload = _valid_config()
    agent = payload["agent"]
    assert isinstance(agent, dict)
    agent.pop("purposes")

    with pytest.raises(ValidationError, match="purposes"):
        CompanyAgentConfig.model_validate(payload)


def test_approved_config_must_keep_claims_fact_grounded() -> None:
    payload = _valid_config()
    agent = payload["agent"]
    assert isinstance(agent, dict)
    agent["require_fact_ids_for_claims"] = False

    with pytest.raises(ValidationError, match="must require fact ids"):
        CompanyAgentConfig.model_validate(payload)


def test_products_and_prices_are_not_universal_publish_requirements() -> None:
    payload = _valid_config()
    payload["offerings"] = []
    payload["relationships"] = []
    payload["facts"] = []
    payload["policies"] = []

    config = CompanyAgentConfig.model_validate(payload)

    assert config.is_publishable() is True


def test_handoff_contact_must_be_customer_visible_approved_text() -> None:
    payload = _valid_config()
    agent = payload["agent"]
    assert isinstance(agent, dict)
    agent["handoff_fact_id"] = "missing-contact"

    with pytest.raises(ValidationError, match="handoff_fact_id must refer to a known fact"):
        CompanyAgentConfig.model_validate(payload)

    agent["handoff_fact_id"] = "minimum_project_size"
    config = CompanyAgentConfig.model_validate(payload)
    assert config.agent is not None
    assert config.agent.handoff_fact_id == "minimum_project_size"


def test_semantic_fallback_and_starter_actions_reference_visible_facts() -> None:
    payload = _valid_config()
    payload["schema_version"] = "company-agent-config/1.1"
    facts = payload["facts"]
    agent = payload["agent"]
    assert isinstance(facts, list)
    assert isinstance(agent, dict)
    fact = facts[0]
    assert isinstance(fact, dict)
    fact["category"] = "social"
    fact["selection_guidance"] = {"tr-TR": "Belirsiz sohbet mesajinda bu davranisi sec."}
    agent["semantic_fallback_fact_ids"] = ["minimum_project_size"]
    agent["starter_trigger_fact_ids"] = ["minimum_project_size"]
    agent["starter_actions"] = [
        {
            "fact_id": "minimum_project_size",
            "display_names": {"tr-TR": "Detay"},
        }
    ]

    config = CompanyAgentConfig.model_validate(payload)

    assert config.agent is not None
    assert config.facts[0].category.value == "social"
    assert config.facts[0].selection_guidance == {
        "tr-TR": "Belirsiz sohbet mesajinda bu davranisi sec."
    }
    assert config.agent.semantic_fallback_fact_ids == ["minimum_project_size"]
    assert config.agent.starter_actions[0].fact_id == "minimum_project_size"


@pytest.mark.parametrize(
    "field_name, error_message",
    [
        (
            "semantic_fallback_fact_ids",
            "semantic fallback fact ids must refer to customer-visible facts",
        ),
        (
            "starter_trigger_fact_ids",
            "starter trigger fact ids must refer to customer-visible facts",
        ),
    ],
)
def test_conversation_policy_rejects_unknown_fact_references(
    field_name: str,
    error_message: str,
) -> None:
    payload = _valid_config()
    payload["schema_version"] = "company-agent-config/1.1"
    agent = payload["agent"]
    assert isinstance(agent, dict)
    if field_name == "starter_trigger_fact_ids":
        agent["starter_actions"] = [
            {
                "fact_id": "minimum_project_size",
                "display_names": {"tr-TR": "Detay"},
            }
        ]
    agent[field_name] = ["missing_fact"]

    with pytest.raises(ValidationError, match=error_message):
        CompanyAgentConfig.model_validate(payload)


def test_starter_action_rejects_an_unknown_or_hidden_fact() -> None:
    payload = _valid_config()
    payload["schema_version"] = "company-agent-config/1.1"
    agent = payload["agent"]
    assert isinstance(agent, dict)
    agent["starter_trigger_fact_ids"] = ["minimum_project_size"]
    agent["starter_actions"] = [{"fact_id": "missing_fact", "display_names": {"tr-TR": "Detay"}}]

    with pytest.raises(
        ValidationError,
        match="starter actions must refer to customer-visible facts",
    ):
        CompanyAgentConfig.model_validate(payload)


def test_conversation_policy_rejects_duplicate_fact_ids() -> None:
    payload = _valid_config()
    payload["schema_version"] = "company-agent-config/1.1"
    agent = payload["agent"]
    assert isinstance(agent, dict)
    agent["semantic_fallback_fact_ids"] = [
        "minimum_project_size",
        "minimum_project_size",
    ]

    with pytest.raises(ValidationError, match="semantic_fallback_fact_ids must be unique"):
        CompanyAgentConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("category", "selection_guidance"),
    [
        ("support", {"tr-TR": "Bu davranisi sec."}),
        ("social", None),
    ],
)
def test_semantic_fallback_requires_a_guided_social_fact(
    category: str,
    selection_guidance: dict[str, str] | None,
) -> None:
    payload = _valid_config()
    payload["schema_version"] = "company-agent-config/1.1"
    facts = payload["facts"]
    agent = payload["agent"]
    assert isinstance(facts, list)
    assert isinstance(agent, dict)
    fact = facts[0]
    assert isinstance(fact, dict)
    fact["category"] = category
    if selection_guidance is not None:
        fact["selection_guidance"] = selection_guidance
    agent["semantic_fallback_fact_ids"] = ["minimum_project_size"]

    with pytest.raises(
        ValidationError,
        match="semantic fallback facts must be social and define selection_guidance",
    ):
        CompanyAgentConfig.model_validate(payload)


def test_arti_kasnak_v1_2_config_is_publishable_and_social_only() -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "arti_kasnak.production.json"
    config = CompanyAgentConfig.model_validate_json(path.read_text(encoding="utf-8"))

    assert config.schema_version == "company-agent-config/1.2"
    assert config.is_publishable() is True
    assert config.agent is not None
    assert len(config.agent.semantic_fallback_fact_ids) <= 24

    fact_by_id = {fact.id: fact for fact in config.facts}
    fallback_facts = [fact_by_id[fact_id] for fact_id in config.agent.semantic_fallback_fact_ids]
    assert all(fact.category.value == "social" for fact in fallback_facts)
    assert all(fact.selection_guidance for fact in fallback_facts)
    assert "conversation_positive_mood" in config.agent.semantic_fallback_fact_ids

    quote_fact = fact_by_id["quote_product_question"]
    assert {"fiyat", "maliyet", "bedel", "kaç para"} <= set(quote_fact.search_terms)
    confusion_text = fact_by_id["conversation_confusion"].customer_text
    assert confusion_text is not None
    assert "daha sade" not in confusion_text["tr"]
