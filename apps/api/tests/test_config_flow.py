"""Structural-totality tests for the universal configuration flow registry."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.modules.agents.company_config import CompanyAgentConfig, DomainModule
from src.modules.agents.config_flow import (
    CORE_EDITABLE_PATHS,
    CORE_FLOW_SPECS,
    ConfigurationFlowRegistry,
    FlowCompilationError,
    FlowGoal,
    FlowKind,
    FlowSpec,
    Requiredness,
    compile_configuration_flow,
    module_config_path,
    schema_manifest_errors,
)


@dataclass(frozen=True)
class AppointmentModuleProvider:
    """A tiny registered-module fixture, not a sector-specific core dependency."""

    module_id: str = "clinic.appointments"
    schema_version: str = "1.0.0"
    runtime_adapter_id: str = "appointment-runtime/1"

    def validate_config(self, module: DomainModule) -> None:
        duration = module.config.get("default_duration_minutes")
        if not isinstance(duration, int) or duration <= 0:
            raise ValueError("default_duration_minutes must be a positive integer")

    def flow_specs(self, module: DomainModule) -> tuple[FlowSpec, ...]:
        return (
            FlowSpec(
                id=f"module.{module.id}.configuration",
                target_paths=frozenset({module_config_path(module)}),
                kind=FlowKind.MODULE_CONFIG,
                goal=FlowGoal.CONFIGURE_MODULE,
                anchor=("modules", module.id),
                dependencies=frozenset({"module-registry"}),
                requiredness=Requiredness.OPTIONAL,
                can_defer=True,
                requires_review_before_apply=True,
            ),
        )


def _clinic_draft(*, with_module: bool = False) -> CompanyAgentConfig:
    payload: dict[str, object] = {
        "lifecycle": "draft",
        "organization": {
            "id": "clinic",
            "display_names": {"tr-TR": "Kuzey Klinik"},
            "markets": ["TR"],
        },
        "parties": [
            {
                "id": "doctor_ayse",
                "kind": "person",
                "roles": ["practitioner"],
                "display_names": {"tr-TR": "Dr. Ayşe"},
            }
        ],
        "customer_profiles": [
            {
                "id": "patient",
                "applies_to": ["person"],
                "fields": [
                    {"id": "birth_date", "type": "date", "required": False},
                    {
                        "id": "visit_type",
                        "type": "enum",
                        "required": True,
                        "allowed_values": ["new", "follow_up"],
                    },
                ],
            }
        ],
        "processes": [
            {
                "id": "appointment",
                "states": ["requested", "confirmed", "cancelled"],
                "initial_state": "requested",
                "allowed_actions": ["confirm", "cancel"],
                "transitions": [
                    {
                        "from_state": "requested",
                        "event": "slot_selected",
                        "to_state": "confirmed",
                        "action": "confirm",
                    }
                ],
            }
        ],
        "policies": [
            {
                "id": "medical-advice",
                "when": [{"field": "intent", "operator": "equals", "value": "diagnosis"}],
                "effect": "handoff",
            }
        ],
        "agent": {
            "purposes": ["appointment", "support"],
            "supported_locales": ["tr-TR"],
            "default_locale": "tr-TR",
        },
    }
    if with_module:
        payload["modules"] = [
            {
                "id": "clinic.appointments",
                "schema_version": "1.0.0",
                "config": {"default_duration_minutes": 30},
            }
        ]
    return CompanyAgentConfig.model_validate(payload)


def test_core_manifest_matches_real_pydantic_models() -> None:
    """A new core field cannot silently evade the universal flow manifest."""

    assert schema_manifest_errors() == ()


def test_every_core_editable_path_has_exactly_one_flow_owner() -> None:
    compiled = compile_configuration_flow(CompanyAgentConfig.empty())

    assert compiled.coverage.is_complete
    assert compiled.coverage.expected_paths == CORE_EDITABLE_PATHS
    assert compiled.coverage.covered_paths == CORE_EDITABLE_PATHS
    assert not compiled.coverage.uncovered_paths
    assert not compiled.coverage.ambiguous_paths
    assert not compiled.coverage.unexpected_paths

    owners = [path for spec in CORE_FLOW_SPECS for path in spec.target_paths]
    assert len(owners) == len(set(owners)) == len(CORE_EDITABLE_PATHS)


def test_all_generic_collections_keep_an_explicit_close_gate() -> None:
    collection_kinds = {
        FlowKind.COLLECTION,
        FlowKind.NESTED_COLLECTION,
        FlowKind.GRAPH_EDGE_COLLECTION,
        FlowKind.EVIDENCE_COLLECTION,
        FlowKind.PROCESS_BUILDER,
        FlowKind.POLICY_BUILDER,
    }
    collection_specs = [spec for spec in CORE_FLOW_SPECS if spec.kind in collection_kinds]

    assert collection_specs
    assert all(spec.requires_explicit_collection_close for spec in collection_specs)
    assert "offerings" in {spec.id for spec in collection_specs}
    assert "parties" in {spec.id for spec in collection_specs}
    assert "process-transitions" in {spec.id for spec in collection_specs}
    assert "policy-conditions" in {spec.id for spec in collection_specs}


def test_reference_fields_are_deferred_until_their_graph_neighbourhood_exists() -> None:
    compiled = compile_configuration_flow(_clinic_draft())
    order = {spec.id: index for index, spec in enumerate(compiled.specs)}

    for spec in compiled.specs:
        for dependency in spec.dependencies:
            assert order[dependency] < order[spec.id]

    questions = {question.id: question for question in compiled.questions}
    assert questions["party-profile-bindings"].goal == FlowGoal.BIND_REFERENCES
    assert questions["offering-provider-bindings"].goal == FlowGoal.BIND_REFERENCES
    assert "apply_json_patch" not in questions["fact-subject-bindings"].allowed_actions
    assert order["offerings"] < order["fact-subject-bindings"]


def test_identity_is_first_and_lifecycle_review_is_the_terminal_gate() -> None:
    compiled = compile_configuration_flow(CompanyAgentConfig.empty())

    assert compiled.specs[0].id == "organization"
    assert compiled.specs[-1].id == "lifecycle-review"


def test_catalog_free_clinic_is_structurally_spanned_without_forcing_offerings() -> None:
    compiled = compile_configuration_flow(_clinic_draft())
    specs = {spec.id: spec for spec in compiled.specs}

    assert compiled.is_structurally_complete
    assert specs["offerings"].requiredness == Requiredness.OPTIONAL
    assert specs["customer-profiles"].requiredness == Requiredness.OPTIONAL
    assert specs["processes"].requiredness == Requiredness.OPTIONAL
    assert specs["policies"].requiredness == Requiredness.OPTIONAL
    assert specs["organization"].requiredness == Requiredness.REQUIRED_FOR_LIVE
    assert specs["agent-policy"].requiredness == Requiredness.REQUIRED_FOR_LIVE


def test_unregistered_module_is_an_explicit_coverage_failure_not_a_generic_blob() -> None:
    config = _clinic_draft(with_module=True)
    compiled = compile_configuration_flow(config)
    expected_path = module_config_path(config.modules[0])

    assert not compiled.is_structurally_complete
    assert expected_path in compiled.coverage.uncovered_paths
    assert compiled.coverage.blockers[0].required_path == expected_path
    assert "FlowSpec provider" in compiled.coverage.blockers[0].reason
    with pytest.raises(FlowCompilationError, match="module blockers"):
        compiled.require_structural_completeness()


def test_registered_module_needs_validator_runtime_adapter_and_its_own_flow_spec() -> None:
    config = _clinic_draft(with_module=True)
    compiled = compile_configuration_flow(config, module_providers=[AppointmentModuleProvider()])

    assert compiled.is_structurally_complete
    assert module_config_path(config.modules[0]) in compiled.coverage.covered_paths
    module_question = next(
        question for question in compiled.questions if question.id == "module.clinic.appointments.configuration"
    )
    assert module_question.goal == FlowGoal.CONFIGURE_MODULE
    assert module_question.requires_review_before_apply


def test_invalid_registered_module_config_fails_closed() -> None:
    config = _clinic_draft(with_module=True)
    bad_module = config.modules[0].model_copy(
        update={"config": {"default_duration_minutes": 0}}
    )
    invalid = config.model_copy(update={"modules": [bad_module]})

    compiled = compile_configuration_flow(invalid, module_providers=[AppointmentModuleProvider()])

    assert not compiled.is_structurally_complete
    assert compiled.coverage.blockers[0].reason.startswith("module validation failed")
    assert any(spec.id == "module.clinic.appointments.configuration" for spec in compiled.specs)


def test_module_provider_cannot_hide_uncovered_config_root() -> None:
    @dataclass(frozen=True)
    class IncompleteProvider(AppointmentModuleProvider):
        def flow_specs(self, module: DomainModule) -> tuple[FlowSpec, ...]:
            return (
                FlowSpec(
                    id="module.clinic.appointments.side-question",
                    target_paths=frozenset(),
                    kind=FlowKind.MODULE_CONFIG,
                    goal=FlowGoal.CONFIGURE_MODULE,
                    anchor=("modules", module.id),
                    dependencies=frozenset({"module-registry"}),
                ),
            )

    config = _clinic_draft(with_module=True)
    compiled = compile_configuration_flow(config, module_providers=[IncompleteProvider()])

    assert module_config_path(config.modules[0]) in compiled.coverage.uncovered_paths
    assert compiled.coverage.blockers[0].reason.startswith("provider must own the module config root")
    assert not compiled.is_structurally_complete


def test_broken_module_flow_provider_is_an_explicit_blocker() -> None:
    @dataclass(frozen=True)
    class BrokenProvider(AppointmentModuleProvider):
        def flow_specs(self, module: DomainModule) -> tuple[FlowSpec, ...]:
            raise RuntimeError("provider import failed")

    config = _clinic_draft(with_module=True)
    compiled = compile_configuration_flow(config, module_providers=[BrokenProvider()])

    assert not compiled.is_structurally_complete
    assert module_config_path(config.modules[0]) in compiled.coverage.uncovered_paths
    assert compiled.coverage.blockers[0].reason.startswith("module flow compilation failed")
