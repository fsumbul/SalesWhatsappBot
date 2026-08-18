"""Schema-total configuration flow for :mod:`company_config`.

The previous guided-config prototype had a good *interaction* policy, but its
question graph was hand-written around offerings.  That is not sufficient for
a universal company model: a clinic with profiles and appointment processes,
or a marketplace with parties and relationships, must be just as reachable as
a product catalogue.

This module makes the boundary explicit and mechanically auditable:

* ``CORE_EDITABLE_PATHS`` is the finite, semantic mutation space of the core
  ``CompanyAgentConfig`` schema.  System-owned ``schema_version`` is excluded.
* every core path is owned by exactly one ``FlowSpec``;
* registered domain modules must supply a matching ``ModuleFlowProvider`` that
  owns that module instance's ``config`` root; and
* a compiler returns a channel-neutral question graph.  A channel renderer may
  later choose buttons, lists, Forms/Flows, document prompts, or text without
  changing the enabled transitions.

The result is intentionally not an LLM prompt generator and not a JSON-patch
API.  It is the deterministic structural contract that a conversational
builder, web UI, and WhatsApp renderer must obey.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from .company_config import (
    AgentReplyPolicy,
    BusinessProcess,
    CompanyAgentConfig,
    CustomerField,
    CustomerProfileDefinition,
    DomainModule,
    Fact,
    Offering,
    Organization,
    Party,
    PolicyCondition,
    PolicyRule,
    ProcessTransition,
    Relationship,
)


class FlowCompilationError(ValueError):
    """The declarative flow registry is internally inconsistent."""


class FlowKind(StrEnum):
    """The minimal structural editors from which company configuration flows compose."""

    SINGLETON_FORM = "singleton_form"
    COLLECTION = "collection"
    NESTED_COLLECTION = "nested_collection"
    REFERENCE_BINDING = "reference_binding"
    GRAPH_EDGE_COLLECTION = "graph_edge_collection"
    EVIDENCE_COLLECTION = "evidence_collection"
    PROCESS_BUILDER = "process_builder"
    POLICY_BUILDER = "policy_builder"
    REVIEW_GATE = "review_gate"
    MODULE_CONFIG = "module_config"


class FlowGoal(StrEnum):
    """Channel-neutral semantic goal; never a UI component name."""

    COLLECT_VALUES = "collect_values"
    COLLECT_COLLECTION = "collect_collection"
    BIND_REFERENCES = "bind_references"
    REVIEW_EVIDENCE = "review_evidence"
    REVIEW_LIFECYCLE = "review_lifecycle"
    CONFIGURE_MODULE = "configure_module"


class Requiredness(StrEnum):
    OPTIONAL = "optional"
    REQUIRED_FOR_LIVE = "required_for_live"
    LIFECYCLE_GATE = "lifecycle_gate"


@dataclass(frozen=True)
class FlowSpec:
    """One deterministic, schema-owned configuration responsibility.

    ``target_paths`` uses semantic paths, not JSON Patch paths.  A collection
    wildcard represents an item's editable fields.  For example,
    ``offerings[*].kind`` is covered by the offering collection spec, while
    ``offerings[*].provider_id`` belongs to a later reference-binding spec.
    This separation makes graph ordering explicit.
    """

    id: str
    target_paths: frozenset[str]
    kind: FlowKind
    goal: FlowGoal
    anchor: tuple[str, ...]
    dependencies: frozenset[str] = frozenset()
    requiredness: Requiredness = Requiredness.OPTIONAL
    can_defer: bool = True
    requires_explicit_collection_close: bool = False
    requires_review_before_apply: bool = False

    def question(self) -> "FlowQuestion":
        """Produce the channel-neutral question contract for this flow node."""

        actions: tuple[str, ...]
        if self.kind in {
            FlowKind.COLLECTION,
            FlowKind.NESTED_COLLECTION,
            FlowKind.GRAPH_EDGE_COLLECTION,
            FlowKind.PROCESS_BUILDER,
            FlowKind.POLICY_BUILDER,
        }:
            actions = ("add_item", "import_artifact", "close_collection")
        elif self.kind == FlowKind.EVIDENCE_COLLECTION:
            actions = ("add_item", "import_artifact", "close_collection", "review_proposals")
        elif self.kind == FlowKind.REFERENCE_BINDING:
            actions = ("select_references", "clear_references")
        elif self.kind == FlowKind.REVIEW_GATE:
            actions = ("validate_draft", "request_review", "approve")
        elif self.kind == FlowKind.MODULE_CONFIG:
            actions = ("configure_module", "review_module_config")
        else:
            actions = ("submit_values",)

        if self.can_defer and "defer" not in actions:
            actions = (*actions, "defer")

        return FlowQuestion(
            id=self.id,
            prompt_key=f"config.{self.id}",
            goal=self.goal,
            anchor=self.anchor,
            target_paths=self.target_paths,
            allowed_actions=actions,
            requires_explicit_collection_close=self.requires_explicit_collection_close,
            requires_review_before_apply=self.requires_review_before_apply,
        )


@dataclass(frozen=True)
class FlowQuestion:
    """A renderer-independent question that may be localized later."""

    id: str
    prompt_key: str
    goal: FlowGoal
    anchor: tuple[str, ...]
    target_paths: frozenset[str]
    allowed_actions: tuple[str, ...]
    requires_explicit_collection_close: bool
    requires_review_before_apply: bool


class ModuleFlowProvider(Protocol):
    """An extension owns its data only when it owns a configuration flow too.

    The core intentionally treats ``DomainModule.config`` as opaque.  A module
    provider is the explicit place where that config's validator, question
    graph and customer-facing runtime adapter can be coupled and versioned.
    """

    module_id: str
    schema_version: str
    runtime_adapter_id: str

    def validate_config(self, module: DomainModule) -> None: ...

    def flow_specs(self, module: DomainModule) -> tuple[FlowSpec, ...]: ...


@dataclass(frozen=True)
class ModuleBlocker:
    module_id: str
    schema_version: str
    required_path: str
    reason: str


@dataclass(frozen=True)
class FlowCoverageReport:
    """Mechanical evidence for the bounded coverage claim.

    A complete report proves totality only over the defined core schema plus
    the *registered* module providers.  It deliberately does not claim to
    enumerate the unbounded real-world company universe.
    """

    expected_paths: frozenset[str]
    covered_paths: frozenset[str]
    uncovered_paths: frozenset[str]
    ambiguous_paths: frozenset[str]
    unexpected_paths: frozenset[str]
    blockers: tuple[ModuleBlocker, ...]

    @property
    def is_complete(self) -> bool:
        return not (
            self.uncovered_paths
            or self.ambiguous_paths
            or self.unexpected_paths
            or self.blockers
        )

    def require_complete(self) -> None:
        if self.is_complete:
            return
        parts: list[str] = []
        if self.uncovered_paths:
            parts.append("uncovered=" + ", ".join(sorted(self.uncovered_paths)))
        if self.ambiguous_paths:
            parts.append("ambiguous=" + ", ".join(sorted(self.ambiguous_paths)))
        if self.unexpected_paths:
            parts.append("unexpected=" + ", ".join(sorted(self.unexpected_paths)))
        if self.blockers:
            parts.append(
                "module blockers="
                + ", ".join(f"{item.module_id}@{item.schema_version}" for item in self.blockers)
            )
        raise FlowCompilationError("configuration flow is not structurally complete: " + "; ".join(parts))


@dataclass(frozen=True)
class CompiledConfigurationFlow:
    """Topologically ordered question graph and the evidence backing it."""

    specs: tuple[FlowSpec, ...]
    questions: tuple[FlowQuestion, ...]
    coverage: FlowCoverageReport

    @property
    def is_structurally_complete(self) -> bool:
        return self.coverage.is_complete

    def require_structural_completeness(self) -> None:
        self.coverage.require_complete()


def _paths(prefix: str, model: type[BaseModel], *, exclude: frozenset[str] = frozenset()) -> frozenset[str]:
    return frozenset(f"{prefix}.{name}" for name in model.model_fields if name not in exclude)


# This manifest is intentionally independent from the flow specs.  If the
# Pydantic schema grows and this manifest is not updated, ``schema_manifest_errors``
# fails instead of silently treating the new slot as flow-covered.
CORE_EDITABLE_PATHS = frozenset(
    {
        "lifecycle",
        *_paths("organization", Organization),
        *_paths("parties[*]", Party),
        *_paths("offerings[*]", Offering),
        *_paths("relationships[*]", Relationship),
        *_paths("facts[*]", Fact),
        *_paths("customer_profiles[*]", CustomerProfileDefinition),
        *_paths("customer_profiles[*].fields[*]", CustomerField),
        *_paths("processes[*]", BusinessProcess),
        *_paths("processes[*].transitions[*]", ProcessTransition),
        *_paths("policies[*]", PolicyRule),
        *_paths("policies[*].when[*]", PolicyCondition),
        *_paths("agent", AgentReplyPolicy),
        *_paths("modules[*]", DomainModule, exclude=frozenset({"config"})),
    }
)


def module_config_path(module: DomainModule) -> str:
    """Return the exact extension-owned config slot for one module instance."""

    return f"modules[{module.id}@{module.schema_version}].config"


def schema_manifest_errors() -> tuple[str, ...]:
    """Detect drift between Pydantic models and the semantic core manifest.

    The field names are discovered from the schema models, while ownership is
    declared separately above.  Adding a new Pydantic field therefore causes a
    deterministic failure until a path and a FlowSpec are deliberately added.
    ``DomainModule.config`` is intentionally excluded: its owner is the
    matching module provider, not the core.
    """

    expected = frozenset(
        {
            "lifecycle",
            *_paths("organization", Organization),
            *_paths("parties[*]", Party),
            *_paths("offerings[*]", Offering),
            *_paths("relationships[*]", Relationship),
            *_paths("facts[*]", Fact),
            *_paths("customer_profiles[*]", CustomerProfileDefinition),
            *_paths("customer_profiles[*].fields[*]", CustomerField),
            *_paths("processes[*]", BusinessProcess),
            *_paths("processes[*].transitions[*]", ProcessTransition),
            *_paths("policies[*]", PolicyRule),
            *_paths("policies[*].when[*]", PolicyCondition),
            *_paths("agent", AgentReplyPolicy),
            *_paths("modules[*]", DomainModule, exclude=frozenset({"config"})),
        }
    )
    errors: list[str] = []
    if expected != CORE_EDITABLE_PATHS:
        missing = expected - CORE_EDITABLE_PATHS
        unexpected = CORE_EDITABLE_PATHS - expected
        if missing:
            errors.append("missing from manifest: " + ", ".join(sorted(missing)))
        if unexpected:
            errors.append("unknown manifest paths: " + ", ".join(sorted(unexpected)))

    top_level_editable = set(CompanyAgentConfig.model_fields) - {"schema_version"}
    expected_roots = {
        "lifecycle",
        "organization",
        "parties",
        "offerings",
        "relationships",
        "facts",
        "customer_profiles",
        "processes",
        "policies",
        "agent",
        "modules",
    }
    if top_level_editable != expected_roots:
        errors.append(
            "top-level ownership drift: expected="
            + ",".join(sorted(expected_roots))
            + " actual="
            + ",".join(sorted(top_level_editable))
        )
    return tuple(errors)


def _spec(
    spec_id: str,
    *target_paths: str,
    kind: FlowKind,
    goal: FlowGoal,
    anchor: tuple[str, ...],
    dependencies: frozenset[str] = frozenset(),
    requiredness: Requiredness = Requiredness.OPTIONAL,
    can_defer: bool = True,
    explicit_close: bool = False,
    review_before_apply: bool = False,
) -> FlowSpec:
    return FlowSpec(
        id=spec_id,
        target_paths=frozenset(target_paths),
        kind=kind,
        goal=goal,
        anchor=anchor,
        dependencies=dependencies,
        requiredness=requiredness,
        can_defer=can_defer,
        requires_explicit_collection_close=explicit_close,
        requires_review_before_apply=review_before_apply,
    )


def _without(paths: frozenset[str], *excluded: str) -> tuple[str, ...]:
    return tuple(sorted(paths - frozenset(excluded)))


# A path belongs to exactly one spec.  Reference fields are intentionally
# separated from their source collection so the planner can require the target
# objects to exist before offering a selector.
CORE_FLOW_SPECS: tuple[FlowSpec, ...] = (
    _spec(
        "organization",
        *_paths("organization", Organization),
        kind=FlowKind.SINGLETON_FORM,
        goal=FlowGoal.COLLECT_VALUES,
        anchor=("organization",),
        requiredness=Requiredness.REQUIRED_FOR_LIVE,
        can_defer=False,
    ),
    _spec(
        "parties",
        *_without(_paths("parties[*]", Party), "parties[*].profile_ids"),
        kind=FlowKind.COLLECTION,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("parties",),
        explicit_close=True,
    ),
    _spec(
        "offerings",
        *_without(_paths("offerings[*]", Offering), "offerings[*].provider_id"),
        kind=FlowKind.COLLECTION,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("offerings",),
        dependencies=frozenset({"organization"}),
        explicit_close=True,
    ),
    _spec(
        "customer-profiles",
        *_without(_paths("customer_profiles[*]", CustomerProfileDefinition), "customer_profiles[*].fields"),
        kind=FlowKind.COLLECTION,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("customer_profiles",),
        explicit_close=True,
    ),
    _spec(
        "customer-profile-fields",
        "customer_profiles[*].fields",
        *_paths("customer_profiles[*].fields[*]", CustomerField),
        kind=FlowKind.NESTED_COLLECTION,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("customer_profiles", "fields"),
        dependencies=frozenset({"customer-profiles"}),
        explicit_close=True,
    ),
    _spec(
        "party-profile-bindings",
        "parties[*].profile_ids",
        kind=FlowKind.REFERENCE_BINDING,
        goal=FlowGoal.BIND_REFERENCES,
        anchor=("parties", "profile_ids"),
        dependencies=frozenset({"parties", "customer-profiles"}),
    ),
    _spec(
        "offering-provider-bindings",
        "offerings[*].provider_id",
        kind=FlowKind.REFERENCE_BINDING,
        goal=FlowGoal.BIND_REFERENCES,
        anchor=("offerings", "provider_id"),
        dependencies=frozenset({"organization", "offerings", "parties"}),
    ),
    _spec(
        "relationships",
        *_paths("relationships[*]", Relationship),
        kind=FlowKind.GRAPH_EDGE_COLLECTION,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("relationships",),
        dependencies=frozenset({"organization", "parties", "offerings"}),
        explicit_close=True,
    ),
    _spec(
        "facts",
        *_without(_paths("facts[*]", Fact), "facts[*].subject_id"),
        kind=FlowKind.EVIDENCE_COLLECTION,
        goal=FlowGoal.REVIEW_EVIDENCE,
        anchor=("facts",),
        dependencies=frozenset({"organization"}),
        explicit_close=True,
        review_before_apply=True,
    ),
    _spec(
        "fact-subject-bindings",
        "facts[*].subject_id",
        kind=FlowKind.REFERENCE_BINDING,
        goal=FlowGoal.BIND_REFERENCES,
        anchor=("facts", "subject_id"),
        # A fact may describe the organization, a party or an offering.  The
        # selector therefore cannot be considered available until every
        # possible entity collection has reached its explicit checkpoint.
        dependencies=frozenset({"facts", "organization", "parties", "offerings"}),
    ),
    _spec(
        "processes",
        *_without(_paths("processes[*]", BusinessProcess), "processes[*].transitions"),
        kind=FlowKind.PROCESS_BUILDER,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("processes",),
        explicit_close=True,
    ),
    _spec(
        "process-transitions",
        "processes[*].transitions",
        *_paths("processes[*].transitions[*]", ProcessTransition),
        kind=FlowKind.NESTED_COLLECTION,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("processes", "transitions"),
        dependencies=frozenset({"processes"}),
        explicit_close=True,
    ),
    _spec(
        "policies",
        *_without(_paths("policies[*]", PolicyRule), "policies[*].when", "policies[*].template_fact_id"),
        kind=FlowKind.POLICY_BUILDER,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("policies",),
        explicit_close=True,
    ),
    _spec(
        "policy-conditions",
        "policies[*].when",
        *_paths("policies[*].when[*]", PolicyCondition),
        kind=FlowKind.NESTED_COLLECTION,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("policies", "when"),
        dependencies=frozenset({"policies"}),
        explicit_close=True,
    ),
    _spec(
        "policy-template-bindings",
        "policies[*].template_fact_id",
        kind=FlowKind.REFERENCE_BINDING,
        goal=FlowGoal.BIND_REFERENCES,
        anchor=("policies", "template_fact_id"),
        dependencies=frozenset({"policies", "facts"}),
    ),
    _spec(
        "agent-policy",
        *_paths("agent", AgentReplyPolicy),
        kind=FlowKind.SINGLETON_FORM,
        goal=FlowGoal.COLLECT_VALUES,
        anchor=("agent",),
        dependencies=frozenset({"organization"}),
        requiredness=Requiredness.REQUIRED_FOR_LIVE,
        can_defer=False,
    ),
    _spec(
        "module-registry",
        *_paths("modules[*]", DomainModule, exclude=frozenset({"config"})),
        kind=FlowKind.COLLECTION,
        goal=FlowGoal.COLLECT_COLLECTION,
        anchor=("modules",),
        explicit_close=True,
    ),
    _spec(
        "lifecycle-review",
        "lifecycle",
        kind=FlowKind.REVIEW_GATE,
        goal=FlowGoal.REVIEW_LIFECYCLE,
        anchor=("lifecycle",),
        dependencies=frozenset({"organization", "agent-policy"}),
        requiredness=Requiredness.LIFECYCLE_GATE,
        can_defer=False,
        review_before_apply=True,
    ),
)


def _topological_order(specs: Iterable[FlowSpec]) -> tuple[FlowSpec, ...]:
    items = tuple(specs)
    by_id = {spec.id: spec for spec in items}
    if len(by_id) != len(items):
        # ``specs`` is normally a tuple, but materialise it below for a clear
        # error should a caller provide duplicate IDs from a module adapter.
        raise FlowCompilationError("flow spec ids must be unique")
    unknown_dependencies = {
        dependency
        for spec in by_id.values()
        for dependency in spec.dependencies
        if dependency not in by_id
    }
    if unknown_dependencies:
        raise FlowCompilationError("unknown flow dependencies: " + ", ".join(sorted(unknown_dependencies)))

    ordered: list[FlowSpec] = []
    remaining = dict(by_id)
    while remaining:
        # Dict insertion order preserves the deliberately authored order of
        # independent questions (identity before optional enrichment), while
        # dependencies still remain a hard topological constraint. Choose one
        # node at a time: adding every currently-ready node as a batch would
        # make a newly enabled, earlier authored node wait for unrelated
        # optional nodes.
        completed_ids = {item.id for item in ordered}
        ready = next(
            (spec for spec in remaining.values() if spec.dependencies <= completed_ids),
            None,
        )
        if ready is None:
            raise FlowCompilationError(
                "flow dependency cycle: " + ", ".join(sorted(remaining))
            )
        ordered.append(ready)
        del remaining[ready.id]
    return tuple(ordered)


class ConfigurationFlowRegistry:
    """Compile the universal flow graph for a draft and its registered modules."""

    def __init__(self, providers: Iterable[ModuleFlowProvider] = ()) -> None:
        self._providers: dict[tuple[str, str], ModuleFlowProvider] = {}
        for provider in providers:
            key = (provider.module_id, provider.schema_version)
            if key in self._providers:
                raise FlowCompilationError(f"duplicate module flow provider: {key[0]}@{key[1]}")
            self._providers[key] = provider
        self._assert_core_registry()

    def _assert_core_registry(self) -> None:
        errors = schema_manifest_errors()
        if errors:
            raise FlowCompilationError("schema manifest drift: " + "; ".join(errors))
        core_paths = [path for spec in CORE_FLOW_SPECS for path in spec.target_paths]
        counts = Counter(core_paths)
        missing = CORE_EDITABLE_PATHS - set(counts)
        ambiguous = {path for path, count in counts.items() if count > 1}
        unexpected = set(counts) - CORE_EDITABLE_PATHS
        if missing or ambiguous or unexpected:
            parts: list[str] = []
            if missing:
                parts.append("missing=" + ", ".join(sorted(missing)))
            if ambiguous:
                parts.append("ambiguous=" + ", ".join(sorted(ambiguous)))
            if unexpected:
                parts.append("unexpected=" + ", ".join(sorted(unexpected)))
            raise FlowCompilationError("core flow registry is not total: " + "; ".join(parts))
        _topological_order(CORE_FLOW_SPECS)

    def provider_for(self, module: DomainModule) -> ModuleFlowProvider | None:
        """Return the exact registered extension provider for a module instance."""

        return self._providers.get((module.id, module.schema_version))

    def compile(self, config: CompanyAgentConfig) -> CompiledConfigurationFlow:
        """Return a total question graph or explicit, fail-closed module blockers."""

        specs: list[FlowSpec] = list(CORE_FLOW_SPECS)
        blockers: list[ModuleBlocker] = []
        expected_paths = set(CORE_EDITABLE_PATHS)

        for module in config.modules:
            expected_path = module_config_path(module)
            expected_paths.add(expected_path)
            provider = self._providers.get((module.id, module.schema_version))
            if provider is None:
                blockers.append(
                    ModuleBlocker(
                        module_id=module.id,
                        schema_version=module.schema_version,
                        required_path=expected_path,
                        reason="no registered FlowSpec provider for this module version",
                    )
                )
                continue
            if not provider.runtime_adapter_id:
                blockers.append(
                    ModuleBlocker(
                        module_id=module.id,
                        schema_version=module.schema_version,
                        required_path=expected_path,
                        reason="provider has no runtime adapter id",
                    )
                )
                continue
            try:
                provider_specs = tuple(provider.flow_specs(module))
            except Exception as exc:  # Provider code is an extension boundary; fail closed.
                blockers.append(
                    ModuleBlocker(
                        module_id=module.id,
                        schema_version=module.schema_version,
                        required_path=expected_path,
                        reason=f"module flow compilation failed: {type(exc).__name__}: {exc}",
                    )
                )
                continue
            if not provider_specs:
                blockers.append(
                    ModuleBlocker(
                        module_id=module.id,
                        schema_version=module.schema_version,
                        required_path=expected_path,
                        reason="provider returned no flow specs",
                    )
                )
                continue
            if not all(isinstance(spec, FlowSpec) for spec in provider_specs):
                blockers.append(
                    ModuleBlocker(
                        module_id=module.id,
                        schema_version=module.schema_version,
                        required_path=expected_path,
                        reason="provider returned an invalid flow spec",
                    )
                )
                continue
            if not any(
                expected_path in spec.target_paths and spec.kind == FlowKind.MODULE_CONFIG
                for spec in provider_specs
            ):
                blockers.append(
                    ModuleBlocker(
                        module_id=module.id,
                        schema_version=module.schema_version,
                        required_path=expected_path,
                        reason="provider must own the module config root with a module-config FlowSpec",
                    )
                )
                continue
            specs.extend(provider_specs)
            # Invalid module configuration must not make the module's own
            # repair flow disappear.  Its FlowSpec remains reachable, but
            # publication/totality stays blocked until its validator accepts
            # the revised module config.
            try:
                provider.validate_config(module)
            except Exception as exc:  # Provider code is an extension boundary; fail closed.
                blockers.append(
                    ModuleBlocker(
                        module_id=module.id,
                        schema_version=module.schema_version,
                        required_path=expected_path,
                        reason=f"module validation failed: {type(exc).__name__}: {exc}",
                    )
                )

        # Lifecycle is a true terminal review gate.  Core and extension
        # questions may be optional/deferred, but an approval action must
        # occur after every registered configuration responsibility has had a
        # chance to close, defer, or raise a visible blocker.
        lifecycle_index = next(
            (index for index, spec in enumerate(specs) if spec.id == "lifecycle-review"),
            None,
        )
        if lifecycle_index is not None:
            lifecycle = specs[lifecycle_index]
            lifecycle_dependencies = frozenset(spec.id for spec in specs if spec.id != lifecycle.id)
            specs[lifecycle_index] = replace(lifecycle, dependencies=lifecycle_dependencies)

        ordered = _topological_order(specs)
        paths = [path for spec in ordered for path in spec.target_paths]
        counts = Counter(paths)
        expected = frozenset(expected_paths)
        covered = frozenset(path for path in counts if path in expected)
        report = FlowCoverageReport(
            expected_paths=expected,
            covered_paths=covered,
            uncovered_paths=frozenset(expected - set(counts)),
            ambiguous_paths=frozenset(path for path, count in counts.items() if count > 1 and path in expected),
            unexpected_paths=frozenset(set(counts) - expected),
            blockers=tuple(blockers),
        )
        return CompiledConfigurationFlow(
            specs=ordered,
            questions=tuple(spec.question() for spec in ordered),
            coverage=report,
        )


def compile_configuration_flow(
    config: CompanyAgentConfig,
    *,
    module_providers: Iterable[ModuleFlowProvider] = (),
) -> CompiledConfigurationFlow:
    """Convenience entry point used by a builder, UI or channel adapter."""

    return ConfigurationFlowRegistry(module_providers).compile(config)
