"""Pure validation tests for the universal CompanyAgentConfig foundation."""

from __future__ import annotations

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
    assert schema["properties"]["schema_version"]["const"] == "company-agent-config/1.0"


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
