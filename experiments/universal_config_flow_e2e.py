#!/usr/bin/env python3
"""Deterministic structural E2E for the universal configuration flow.

This does not claim to enumerate every real-world company.  It proves the
stronger useful invariant that the flow is total over the *declared* core
company schema, plus each registered module's declared configuration root.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from src.modules.agents.company_config import (  # noqa: E402
    AgentReplyPolicy,
    CompanyAgentConfig,
    ConfigurationLifecycle,
    DomainModule,
    Organization,
)
from src.modules.agents.config_flow import (  # noqa: E402
    CORE_EDITABLE_PATHS,
    CORE_FLOW_SPECS,
    FlowGoal,
    FlowKind,
    FlowSpec,
    Requiredness,
    compile_configuration_flow,
    module_config_path,
)
from src.modules.agents.configuration_session import (  # noqa: E402
    AddCustomerFieldCommand,
    AddCustomerProfileCommand,
    AddFactCommand,
    AddOfferingCommand,
    AddPartyCommand,
    AddPolicyCommand,
    AddPolicyConditionCommand,
    AddProcessCommand,
    AddProcessTransitionCommand,
    BindFactSubjectsCommand,
    BindOfferingProvidersCommand,
    BindPartyProfilesCommand,
    BindPolicyTemplatesCommand,
    ConfigurationFlowSession,
    ConfigurationSessionError,
    ConfigureModuleCommand,
    CustomerProfileCore,
    DraftKind,
    FactCore,
    FactSubjectBinding,
    ModuleCore,
    OfferingCore,
    OfferingProviderBinding,
    PartyCore,
    PartyProfileBinding,
    PolicyCore,
    PolicyTemplateBinding,
    ProcessCore,
    RegisterModuleCommand,
    RelationshipCommand,
    SetAgentPolicyCommand,
    SetLifecycleCommand,
    SetOrganizationCommand,
    typed_reducer_coverage_errors,
)


@dataclass(frozen=True)
class Check:
    id: str
    holds: bool
    detail: str


@dataclass(frozen=True)
class AppointmentModuleProvider:
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
                requires_review_before_apply=True,
            ),
        )


@dataclass(frozen=True)
class BrokenAppointmentModuleProvider(AppointmentModuleProvider):
    """Models an unavailable extension package without making the core unsafe."""

    def flow_specs(self, module: DomainModule) -> tuple[FlowSpec, ...]:
        raise RuntimeError("module package could not load")


def add_check(checks: list[Check], check_id: str, holds: bool, detail: str) -> None:
    checks.append(Check(check_id, holds, detail))


def clinic_draft(*, with_module: bool = False) -> CompanyAgentConfig:
    payload: dict[str, Any] = {
        "lifecycle": "draft",
        "organization": {
            "id": "north-clinic",
            "display_names": {"tr-TR": "Kuzey Klinik"},
            "markets": ["TR"],
        },
        "parties": [
            {
                "id": "doctor-ayse",
                "kind": "person",
                "roles": ["practitioner"],
                "display_names": {"tr-TR": "Dr. Ayşe"},
                "profile_ids": ["patient"],
            }
        ],
        "customer_profiles": [
            {
                "id": "patient",
                "applies_to": ["person"],
                "fields": [
                    {"id": "birth-date", "type": "date"},
                    {
                        "id": "visit-type",
                        "type": "enum",
                        "required": True,
                        "allowed_values": ["new", "follow-up"],
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
                        "event": "slot-selected",
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


def marketplace_draft() -> CompanyAgentConfig:
    return CompanyAgentConfig.model_validate(
        {
            "lifecycle": "draft",
            "organization": {"id": "market", "display_names": {"en-GB": "Mosaic Market"}},
            "parties": [
                {
                    "id": "seller-ada",
                    "kind": "organization",
                    "roles": ["seller"],
                    "display_names": {"en-GB": "Ada Goods"},
                }
            ],
            "offerings": [
                {
                    "id": "linen-set",
                    "kind": "marketplace_listing",
                    "display_names": {"en-GB": "Linen set"},
                    "provider_id": "seller-ada",
                }
            ],
            "relationships": [
                {"subject_id": "seller-ada", "predicate": "lists", "object_id": "linen-set"}
            ],
            "facts": [
                {
                    "id": "linen-availability",
                    "subject_id": "linen-set",
                    "category": "availability",
                    "value": {"in_stock": True},
                    "source": "admin-confirmed",
                }
            ],
            "agent": {
                "purposes": ["sales", "support"],
                "supported_locales": ["en-GB"],
                "default_locale": "en-GB",
            },
        }
    )


_TENANT = "structural-e2e-tenant"
_ADMIN = "structural-e2e-admin"


def _new_session(*, providers: tuple[AppointmentModuleProvider, ...] = ()) -> ConfigurationFlowSession:
    return ConfigurationFlowSession(
        tenant_id=_TENANT,
        admin_id=_ADMIN,
        session_id="structural-e2e-session",
        signing_key=b"structural-e2e-session-signing-key",
        module_providers=providers,
    )


def _accept(session: ConfigurationFlowSession, command: Any) -> None:
    action = session.issue_command(type(command), tenant_id=_TENANT, admin_id=_ADMIN)
    preview = session.propose(action, command, tenant_id=_TENANT, admin_id=_ADMIN)
    if preview is None:
        raise AssertionError("new proposal unexpectedly treated as a duplicate")
    approval = session.issue_accept(preview.id, tenant_id=_TENANT, admin_id=_ADMIN)
    if not session.accept(approval, tenant_id=_TENANT, admin_id=_ADMIN):
        raise AssertionError("new proposal acceptance unexpectedly treated as a duplicate")


def _close(session: ConfigurationFlowSession, scope: Any = None) -> None:
    action = session.issue_close_collection(tenant_id=_TENANT, admin_id=_ADMIN, scope=scope)
    if not session.close_collection(action, tenant_id=_TENANT, admin_id=_ADMIN):
        raise AssertionError("new collection closure unexpectedly treated as a duplicate")


def _defer(session: ConfigurationFlowSession) -> None:
    action = session.issue_defer(tenant_id=_TENANT, admin_id=_ADMIN)
    if not session.defer(action, tenant_id=_TENANT, admin_id=_ADMIN):
        raise AssertionError("new defer transition unexpectedly treated as a duplicate")


def constructive_marketplace() -> CompanyAgentConfig:
    """Build a nontrivial target graph through accepted typed admin events."""

    session = _new_session()
    _accept(
        session,
        SetOrganizationCommand(
            organization=Organization(id="market", display_names={"en-GB": "Mosaic Market"})
        ),
    )
    _accept(
        session,
        AddPartyCommand(
            party=PartyCore(
                id="seller-ada",
                kind="organization",
                roles=["seller"],
                display_names={"en-GB": "Ada Goods"},
            )
        ),
    )
    _close(session)
    _accept(
        session,
        AddOfferingCommand(
            offering=OfferingCore(
                id="linen-set", kind="marketplace_listing", display_names={"en-GB": "Linen set"}
            )
        ),
    )
    _close(session)
    _close(session)  # customer_profiles: explicit empty closure
    _defer(session)  # no profile-specific child scopes exist
    _accept(
        session,
        BindPartyProfilesCommand(
            bindings=[PartyProfileBinding(party=session.ref_for(DraftKind.PARTY, "seller-ada"), profiles=[])]
        ),
    )
    _accept(
        session,
        BindOfferingProvidersCommand(
            bindings=[
                OfferingProviderBinding(
                    offering=session.ref_for(DraftKind.OFFERING, "linen-set"),
                    provider=session.ref_for(DraftKind.PARTY, "seller-ada"),
                )
            ]
        ),
    )
    _accept(
        session,
        RelationshipCommand(
            subject=session.ref_for(DraftKind.PARTY, "seller-ada"),
            predicate="lists",
            object=session.ref_for(DraftKind.OFFERING, "linen-set"),
        ),
    )
    _close(session)
    _accept(
        session,
        AddFactCommand(
            fact=FactCore(
                id="linen-availability",
                category="availability",
                value={"in_stock": True},
                source="admin-confirmed",
            )
        ),
    )
    _close(session)
    _accept(
        session,
        BindFactSubjectsCommand(
            bindings=[
                FactSubjectBinding(
                    fact=session.ref_for(DraftKind.FACT, "linen-availability"),
                    subject=session.ref_for(DraftKind.OFFERING, "linen-set"),
                )
            ]
        ),
    )
    _close(session)  # processes: explicit empty closure
    _defer(session)  # no process transition child scopes exist
    _close(session)  # policies: explicit empty closure
    _defer(session)  # no policy condition child scopes exist
    _accept(session, BindPolicyTemplatesCommand(bindings=[]))
    _accept(
        session,
        SetAgentPolicyCommand(
            agent=AgentReplyPolicy(
                purposes=["sales", "support"],
                supported_locales=["en-GB"],
                default_locale="en-GB",
            )
        ),
    )
    _close(session)  # module registry: explicit empty closure
    _accept(session, SetLifecycleCommand(lifecycle=ConfigurationLifecycle.APPROVED))
    if not session.is_complete:
        raise AssertionError(f"session is not terminal: {session.current_question_id()}")
    return session.materialize()


def constructive_clinic_with_module() -> CompanyAgentConfig:
    """Exercise profiles, FSM transitions, policy references and a module root."""

    session = _new_session(providers=(AppointmentModuleProvider(),))
    _accept(
        session,
        SetOrganizationCommand(
            organization=Organization(
                id="north-clinic", display_names={"tr-TR": "Kuzey Klinik"}, markets=["TR"]
            )
        ),
    )
    _accept(
        session,
        AddPartyCommand(
            party=PartyCore(
                id="doctor-ayse",
                kind="person",
                roles=["practitioner"],
                display_names={"tr-TR": "Dr. Ayşe"},
            )
        ),
    )
    _close(session)
    _close(session)  # catalogue-free clinic
    _accept(
        session,
        AddCustomerProfileCommand(
            profile=CustomerProfileCore(id="patient", applies_to=["person"])
        ),
    )
    _close(session)
    patient = session.ref_for(DraftKind.CUSTOMER_PROFILE, "patient")
    _accept(
        session,
        AddCustomerFieldCommand(
            profile=patient,
            field={
                "id": "visit-type",
                "type": "enum",
                "required": True,
                "allowed_values": ["new", "follow-up"],
            },
        ),
    )
    _close(session, patient)
    _accept(
        session,
        BindPartyProfilesCommand(
            bindings=[
                PartyProfileBinding(
                    party=session.ref_for(DraftKind.PARTY, "doctor-ayse"), profiles=[patient]
                )
            ]
        ),
    )
    _accept(session, BindOfferingProvidersCommand(bindings=[]))
    _close(session)  # relationships
    _accept(
        session,
        AddFactCommand(
            fact=FactCore(
                id="appointment-confirmation",
                category="support",
                value={"kind": "template"},
                source="admin-confirmed",
                customer_visible=True,
                customer_text={"tr-TR": "Randevunuz onaylandı."},
            )
        ),
    )
    _close(session)
    _accept(
        session,
        BindFactSubjectsCommand(
            bindings=[
                FactSubjectBinding(
                    fact=session.ref_for(DraftKind.FACT, "appointment-confirmation"),
                    subject=session.ref_for(DraftKind.ORGANIZATION, "north-clinic"),
                )
            ]
        ),
    )
    _accept(
        session,
        AddProcessCommand(
            process=ProcessCore(
                id="appointment",
                states=["requested", "confirmed", "cancelled"],
                initial_state="requested",
                allowed_actions=["confirm", "cancel"],
            )
        ),
    )
    _close(session)
    appointment = session.ref_for(DraftKind.PROCESS, "appointment")
    _accept(
        session,
        AddProcessTransitionCommand(
            process=appointment,
            transition={
                "from_state": "requested",
                "event": "slot-selected",
                "to_state": "confirmed",
                "action": "confirm",
            },
        ),
    )
    _close(session, appointment)
    _accept(session, AddPolicyCommand(policy=PolicyCore(id="appointment-template", effect="use_template")))
    _close(session)
    policy = session.ref_for(DraftKind.POLICY, "appointment-template")
    _accept(
        session,
        AddPolicyConditionCommand(
            policy=policy,
            condition={"field": "intent", "operator": "equals", "value": "appointment"},
        ),
    )
    _close(session, policy)
    _accept(
        session,
        BindPolicyTemplatesCommand(
            bindings=[
                PolicyTemplateBinding(
                    policy=policy,
                    template_fact=session.ref_for(DraftKind.FACT, "appointment-confirmation"),
                )
            ]
        ),
    )
    _accept(
        session,
        SetAgentPolicyCommand(
            agent=AgentReplyPolicy(
                purposes=["appointment", "support"],
                supported_locales=["tr-TR"],
                default_locale="tr-TR",
            )
        ),
    )
    _accept(
        session,
        RegisterModuleCommand(module=ModuleCore(id="clinic.appointments", schema_version="1.0.0")),
    )
    _close(session)
    if session.current_question_id() != "module.clinic.appointments.configuration":
        raise AssertionError(f"expected module repair step, got {session.current_question_id()!r}")
    _accept(
        session,
        ConfigureModuleCommand(
            module=session.ref_for(DraftKind.MODULE, "clinic.appointments"),
            config={"default_duration_minutes": 30},
        ),
    )
    _accept(session, SetLifecycleCommand(lifecycle=ConfigurationLifecycle.APPROVED))
    if not session.is_complete:
        raise AssertionError(f"session is not terminal: {session.current_question_id()}")
    return session.materialize()


def run_e2e() -> dict[str, Any]:
    checks: list[Check] = []
    empty = compile_configuration_flow(CompanyAgentConfig.empty())
    add_check(
        checks,
        "core-slot-manifest-is-total",
        empty.coverage.is_complete
        and empty.coverage.covered_paths == CORE_EDITABLE_PATHS
        and len(empty.coverage.covered_paths) == len(CORE_EDITABLE_PATHS),
        f"covered={len(empty.coverage.covered_paths)} expected={len(CORE_EDITABLE_PATHS)}",
    )

    owners = [path for spec in CORE_FLOW_SPECS for path in spec.target_paths]
    add_check(
        checks,
        "every-core-slot-has-one-owner",
        len(owners) == len(set(owners)) == len(CORE_EDITABLE_PATHS),
        f"owners={len(owners)} unique={len(set(owners))}",
    )

    collection_kinds = {
        FlowKind.COLLECTION,
        FlowKind.NESTED_COLLECTION,
        FlowKind.GRAPH_EDGE_COLLECTION,
        FlowKind.EVIDENCE_COLLECTION,
        FlowKind.PROCESS_BUILDER,
        FlowKind.POLICY_BUILDER,
    }
    collection_specs = [spec for spec in CORE_FLOW_SPECS if spec.kind in collection_kinds]
    add_check(
        checks,
        "all-collections-require-explicit-closure",
        all(spec.requires_explicit_collection_close for spec in collection_specs),
        ",".join(spec.id for spec in collection_specs),
    )

    clinic = compile_configuration_flow(clinic_draft())
    clinic_specs = {spec.id: spec for spec in clinic.specs}
    add_check(
        checks,
        "catalog-free-clinic-is-covered",
        clinic.is_structurally_complete
        and clinic_specs["offerings"].requiredness == Requiredness.OPTIONAL
        and "customer-profile-fields" in clinic_specs
        and "process-transitions" in clinic_specs
        and "policy-conditions" in clinic_specs,
        "profiles/processes/policies are reachable without a catalogue",
    )

    clinic_order = {spec.id: index for index, spec in enumerate(clinic.specs)}
    dependencies_hold = all(
        clinic_order[dependency] < clinic_order[spec.id]
        for spec in clinic.specs
        for dependency in spec.dependencies
    )
    add_check(
        checks,
        "all-reference-and-nested-dependencies-are-topological",
        dependencies_hold,
        "every dependency precedes its dependent question",
    )

    marketplace = compile_configuration_flow(marketplace_draft())
    marketplace_questions = {question.id: question for question in marketplace.questions}
    add_check(
        checks,
        "marketplace-graph-is-covered",
        marketplace.is_structurally_complete
        and marketplace_questions["relationships"].goal == FlowGoal.COLLECT_COLLECTION
        and marketplace_questions["offering-provider-bindings"].goal == FlowGoal.BIND_REFERENCES
        and marketplace_questions["fact-subject-bindings"].requires_review_before_apply is False,
        "parties/offerings/edges/facts remain distinct structural flows",
    )

    module_config = clinic_draft(with_module=True)
    blocked = compile_configuration_flow(module_config)
    module_path = module_config_path(module_config.modules[0])
    add_check(
        checks,
        "unregistered-module-fails-closed",
        not blocked.is_structurally_complete
        and module_path in blocked.coverage.uncovered_paths
        and len(blocked.coverage.blockers) == 1,
        blocked.coverage.blockers[0].reason if blocked.coverage.blockers else "no blocker",
    )

    registered = compile_configuration_flow(
        module_config,
        module_providers=[AppointmentModuleProvider()],
    )
    add_check(
        checks,
        "registered-module-restores-totality",
        registered.is_structurally_complete
        and module_path in registered.coverage.covered_paths
        and any(question.id == "module.clinic.appointments.configuration" for question in registered.questions),
        f"covered={len(registered.coverage.covered_paths)}",
    )

    invalid_module = module_config.modules[0].model_copy(
        update={"config": {"default_duration_minutes": 0}}
    )
    invalid_module_config = module_config.model_copy(update={"modules": [invalid_module]})
    invalid = compile_configuration_flow(
        invalid_module_config,
        module_providers=[AppointmentModuleProvider()],
    )
    add_check(
        checks,
        "registered-module-validator-is-required",
        not invalid.is_structurally_complete
        and invalid.coverage.blockers[0].reason.startswith("module validation failed")
        and any(spec.id == "module.clinic.appointments.configuration" for spec in invalid.specs),
        invalid.coverage.blockers[0].reason if invalid.coverage.blockers else "no blocker",
    )

    broken = compile_configuration_flow(
        module_config,
        module_providers=[BrokenAppointmentModuleProvider()],
    )
    add_check(
        checks,
        "broken-module-provider-fails-closed",
        not broken.is_structurally_complete
        and module_path in broken.coverage.uncovered_paths
        and broken.coverage.blockers[0].reason.startswith("module flow compilation failed"),
        broken.coverage.blockers[0].reason if broken.coverage.blockers else "no blocker",
    )

    add_check(
        checks,
        "every-core-flow-owner-has-a-typed-reducer",
        typed_reducer_coverage_errors() == (),
        "; ".join(typed_reducer_coverage_errors()) or "all core FlowSpecs have a reducer",
    )

    try:
        constructed_marketplace = constructive_marketplace()
        target_marketplace = CompanyAgentConfig.model_validate(
            {**marketplace_draft().model_dump(mode="json"), "lifecycle": "approved"}
        )
        marketplace_constructive_holds = (
            constructed_marketplace.model_dump(mode="json") == target_marketplace.model_dump(mode="json")
        )
        marketplace_constructive_detail = (
            "accepted typed trace materializes exactly the target marketplace graph"
        )
    except Exception as exc:
        marketplace_constructive_holds = False
        marketplace_constructive_detail = f"{type(exc).__name__}: {exc}"
    add_check(
        checks,
        "constructive-marketplace-trace-round-trips",
        marketplace_constructive_holds,
        marketplace_constructive_detail,
    )

    try:
        constructed_clinic = constructive_clinic_with_module()
        clinic_constructive_holds = (
            constructed_clinic.is_publishable()
            and constructed_clinic.offerings == []
            and constructed_clinic.customer_profiles[0].id == "patient"
            and constructed_clinic.processes[0].transitions[0].action == "confirm"
            and constructed_clinic.policies[0].template_fact_id == "appointment-confirmation"
            and constructed_clinic.modules[0].config == {"default_duration_minutes": 30}
        )
        clinic_constructive_detail = "clinic profile/FSM/policy/module graph materialized through typed events"
    except Exception as exc:
        clinic_constructive_holds = False
        clinic_constructive_detail = f"{type(exc).__name__}: {exc}"
    add_check(
        checks,
        "constructive-clinic-trace-covers-nested-and-module-slots",
        clinic_constructive_holds,
        clinic_constructive_detail,
    )

    safety_session = _new_session()
    organization_command = SetOrganizationCommand(
        organization=Organization(id="safety-co", display_names={"en-GB": "Safety Co"})
    )
    organization_action = safety_session.issue_command(
        SetOrganizationCommand, tenant_id=_TENANT, admin_id=_ADMIN
    )
    organization_preview = safety_session.propose(
        organization_action,
        organization_command,
        tenant_id=_TENANT,
        admin_id=_ADMIN,
    )
    preview_is_non_mutating = organization_preview is not None and safety_session.staged.organization is None
    cross_admin_rejected = False
    try:
        safety_session.propose(
            organization_action,
            organization_command,
            tenant_id=_TENANT,
            admin_id="different-admin",
        )
    except ConfigurationSessionError:
        cross_admin_rejected = True
    assert organization_preview is not None
    organization_accept = safety_session.issue_accept(
        organization_preview.id, tenant_id=_TENANT, admin_id=_ADMIN
    )
    first_accept = safety_session.accept(organization_accept, tenant_id=_TENANT, admin_id=_ADMIN)
    duplicate_accept_is_noop = not safety_session.accept(
        organization_accept, tenant_id=_TENANT, admin_id=_ADMIN
    )
    _accept(
        safety_session,
        AddPartyCommand(
            party=PartyCore(
                id="supplier", kind="organization", roles=["supplier"], display_names={"en-GB": "Supplier"}
            )
        ),
    )
    distant_step_rejected = False
    try:
        safety_session.issue_command(AddOfferingCommand, tenant_id=_TENANT, admin_id=_ADMIN)
    except ConfigurationSessionError:
        distant_step_rejected = True
    add_check(
        checks,
        "proposal-review-action-binding-and-open-collection-gates-are-enforced",
        preview_is_non_mutating
        and cross_admin_rejected
        and first_accept
        and duplicate_accept_is_noop
        and distant_step_rejected,
        "preview is inert; refs bind admin/revision; duplicate accepts are no-ops; open parties blocks offerings",
    )

    return {
        "design": {
            "claim": "Flow totality is measured over CompanyAgentConfig core plus registered module roots.",
            "theorem_shape": "every editable semantic path has exactly one FlowSpec owner and one typed reducer",
            "module_rule": "unregistered or invalid module configs remain explicit blockers",
            "construction_rule": "a proposal is inert until its state-bound, explicitly accepted action reduces the staged graph",
        },
        "metrics": {
            "check_count": len(checks),
            "pass_count": sum(check.holds for check in checks),
            "pass_rate": sum(check.holds for check in checks) / len(checks),
            "core_slot_count": len(CORE_EDITABLE_PATHS),
            "core_flow_spec_count": len(CORE_FLOW_SPECS),
        },
        "checks": [asdict(check) for check in checks],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "experiments" / "universal-config-flow-e2e-report.json",
    )
    args = parser.parse_args()
    report = run_e2e()
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["metrics"]["pass_count"] == report["metrics"]["check_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
