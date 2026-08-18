"""Deterministic, staged construction of a :class:`CompanyAgentConfig`.

``config_flow`` proves that every semantic configuration slot has a unique
question owner.  This module supplies the second half of that contract: a
typed reducer that can actually construct a company graph without giving an
LLM, browser, or channel client a JSON-Patch capability.

The session deliberately does *not* keep an incomplete graph in
``AgentVersion.company_config``.  References which are naturally collected
later (for example a fact's subject) live in a typed staging aggregate until
``materialize`` can produce one valid ``CompanyAgentConfig``.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from .company_config import (
    AgentReplyPolicy,
    BusinessProcess,
    CompanyAgentConfig,
    ConfigurationLifecycle,
    CustomerField,
    DomainModule,
    FactCategory,
    Identifier,
    LocalizedText,
    OfferingKind,
    Organization,
    PartyKind,
    PolicyCondition,
    PolicyEffect,
    ProcessTransition,
    StrictModel,
)
from .config_flow import (
    CORE_FLOW_SPECS,
    ConfigurationFlowRegistry,
    FlowCompilationError,
    FlowKind,
    FlowSpec,
    ModuleFlowProvider,
    module_config_path,
)


class ConfigurationSessionError(ValueError):
    """A submitted configuration transition is not permitted by session state."""


class MaterializationError(ConfigurationSessionError):
    """The staged graph cannot yet be converted into a company configuration."""

    def __init__(self, blockers: Iterable[str]) -> None:
        self.blockers = tuple(blockers)
        super().__init__("configuration cannot be materialized: " + "; ".join(self.blockers))


class DraftKind(StrEnum):
    ORGANIZATION = "organization"
    PARTY = "party"
    OFFERING = "offering"
    FACT = "fact"
    CUSTOMER_PROFILE = "customer_profile"
    PROCESS = "process"
    POLICY = "policy"
    MODULE = "module"


class ScopeState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    DEFERRED = "deferred"


class ActionKind(StrEnum):
    PROPOSE = "propose"
    ACCEPT = "accept"
    REJECT = "reject"
    CLOSE_COLLECTION = "close_collection"
    DEFER = "defer"


class DraftRef(StrictModel):
    """Server-issued reference to one staged instance, never a business ID."""

    kind: DraftKind
    token: UUID


class PartyCore(StrictModel):
    """A party before its profile references have been explicitly bound."""

    id: Identifier
    kind: PartyKind
    roles: list[Identifier] = Field(default_factory=list, max_length=30)
    display_names: LocalizedText | None = None


class OfferingCore(StrictModel):
    """An offering before its provider has been explicitly selected."""

    id: Identifier
    kind: OfferingKind
    display_names: LocalizedText
    active: bool = True


class FactCore(StrictModel):
    """A fact before its subject entity has been explicitly selected."""

    id: Identifier
    category: FactCategory
    value: JsonValue
    customer_visible: bool = False
    customer_text: LocalizedText | None = None
    source: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def _visible_facts_need_customer_text(self) -> "FactCore":
        if self.customer_visible and not self.customer_text:
            raise ValueError("customer_visible facts require customer_text")
        return self


class CustomerProfileCore(StrictModel):
    """A profile definition before its child field collection is entered."""

    id: Identifier
    applies_to: list[PartyKind] = Field(min_length=1, max_length=10)


class ProcessCore(StrictModel):
    """A process before its transition collection is entered."""

    id: Identifier
    states: list[Identifier] = Field(min_length=1, max_length=100)
    initial_state: Identifier
    allowed_actions: list[Identifier] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _validate_core_state_machine(self) -> "ProcessCore":
        # Reuse the canonical schema validator instead of duplicating its
        # state/action uniqueness and initial-state rules.
        BusinessProcess.model_validate({**self.model_dump(mode="json"), "transitions": []})
        return self


class PolicyCore(StrictModel):
    """A policy before conditions and an optional template binding exist."""

    id: Identifier
    effect: PolicyEffect


class ModuleCore(StrictModel):
    """A module registration before provider-owned config is collected."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$", max_length=120)
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)


class ConfigurationCommand(StrictModel):
    """Base class for the closed command grammar accepted by the reducer."""


class SetOrganizationCommand(ConfigurationCommand):
    organization: Organization


class AddPartyCommand(ConfigurationCommand):
    party: PartyCore


class AddOfferingCommand(ConfigurationCommand):
    offering: OfferingCore


class AddCustomerProfileCommand(ConfigurationCommand):
    profile: CustomerProfileCore


class AddCustomerFieldCommand(ConfigurationCommand):
    profile: DraftRef
    field: CustomerField


class PartyProfileBinding(StrictModel):
    party: DraftRef
    profiles: list[DraftRef] = Field(default_factory=list, max_length=20)


class BindPartyProfilesCommand(ConfigurationCommand):
    bindings: list[PartyProfileBinding]


class OfferingProviderBinding(StrictModel):
    offering: DraftRef
    provider: DraftRef


class BindOfferingProvidersCommand(ConfigurationCommand):
    bindings: list[OfferingProviderBinding]


class RelationshipCommand(ConfigurationCommand):
    subject: DraftRef
    predicate: Identifier
    object: DraftRef


class AddFactCommand(ConfigurationCommand):
    fact: FactCore


class FactSubjectBinding(StrictModel):
    fact: DraftRef
    subject: DraftRef


class BindFactSubjectsCommand(ConfigurationCommand):
    bindings: list[FactSubjectBinding]


class AddProcessCommand(ConfigurationCommand):
    process: ProcessCore


class AddProcessTransitionCommand(ConfigurationCommand):
    process: DraftRef
    transition: ProcessTransition


class AddPolicyCommand(ConfigurationCommand):
    policy: PolicyCore


class AddPolicyConditionCommand(ConfigurationCommand):
    policy: DraftRef
    condition: PolicyCondition


class PolicyTemplateBinding(StrictModel):
    policy: DraftRef
    template_fact: DraftRef | None = None


class BindPolicyTemplatesCommand(ConfigurationCommand):
    bindings: list[PolicyTemplateBinding]


class SetAgentPolicyCommand(ConfigurationCommand):
    agent: AgentReplyPolicy


class RegisterModuleCommand(ConfigurationCommand):
    module: ModuleCore


class ConfigureModuleCommand(ConfigurationCommand):
    module: DraftRef
    config: dict[str, JsonValue]


class SetLifecycleCommand(ConfigurationCommand):
    lifecycle: ConfigurationLifecycle


@dataclass(frozen=True)
class _StagedRelationship:
    subject: DraftRef
    predicate: str
    object: DraftRef


@dataclass
class StagedCompanyDraft:
    """Typed partial graph; absent bindings are represented by absence, not sentinels."""

    organization: Organization | None = None
    organization_ref: DraftRef | None = None
    parties: dict[UUID, PartyCore] = field(default_factory=dict)
    offerings: dict[UUID, OfferingCore] = field(default_factory=dict)
    party_profiles: dict[UUID, tuple[UUID, ...]] = field(default_factory=dict)
    offering_providers: dict[UUID, UUID] = field(default_factory=dict)
    relationships: list[_StagedRelationship] = field(default_factory=list)
    facts: dict[UUID, FactCore] = field(default_factory=dict)
    fact_subjects: dict[UUID, UUID] = field(default_factory=dict)
    customer_profiles: dict[UUID, CustomerProfileCore] = field(default_factory=dict)
    customer_fields: dict[UUID, list[CustomerField]] = field(default_factory=dict)
    processes: dict[UUID, ProcessCore] = field(default_factory=dict)
    process_transitions: dict[UUID, list[ProcessTransition]] = field(default_factory=dict)
    policies: dict[UUID, PolicyCore] = field(default_factory=dict)
    policy_conditions: dict[UUID, list[PolicyCondition]] = field(default_factory=dict)
    policy_templates: dict[UUID, UUID | None] = field(default_factory=dict)
    agent: AgentReplyPolicy | None = None
    modules: dict[UUID, ModuleCore] = field(default_factory=dict)
    module_configs: dict[UUID, dict[str, JsonValue]] = field(default_factory=dict)
    lifecycle: ConfigurationLifecycle = ConfigurationLifecycle.DRAFT


@dataclass(frozen=True)
class ProposalPreview:
    """Immutable proposed command shown to an admin before it can mutate state."""

    id: UUID
    flow_spec_id: str
    revision: int
    command_type: str
    canonical_payload: dict[str, Any]
    payload_sha256: str


@dataclass(frozen=True)
class _ActionContext:
    tenant_id: str
    admin_id: str
    session_id: str
    revision: int
    flow_spec_id: str


@dataclass
class _ActionRecord:
    context: _ActionContext
    kind: ActionKind
    command_type: str | None
    proposal_id: UUID | None
    payload_sha256: str | None
    scope: DraftRef | None
    signature: str
    expires_at: int
    consumed: bool = False


class ConfigurationActionLedger:
    """Opaque refs bound to one admin, revision, FlowSpec and transition.

    This in-memory ledger is deliberately deterministic-core infrastructure.
    Production persistence may store the same fields in a table, but must not
    move any of this authority into button labels or client payloads.
    """

    def __init__(
        self,
        signing_key: bytes,
        *,
        now: Callable[[], int] | None = None,
        ttl_seconds: int = 900,
    ) -> None:
        self._signing_key = signing_key
        self._now = now or (lambda: int(time.time()))
        self._ttl_seconds = ttl_seconds
        self._records: dict[str, _ActionRecord] = {}

    def issue(
        self,
        context: _ActionContext,
        kind: ActionKind,
        *,
        command_type: str | None = None,
        proposal_id: UUID | None = None,
        payload_sha256: str | None = None,
        scope: DraftRef | None = None,
    ) -> str:
        nonce = secrets.token_urlsafe(12)
        ref = "cf_" + base64.urlsafe_b64encode(nonce.encode()).decode().rstrip("=")
        record = _ActionRecord(
            context=context,
            kind=kind,
            command_type=command_type,
            proposal_id=proposal_id,
            payload_sha256=payload_sha256,
            scope=scope,
            signature=self._signature(
                context, kind, command_type, proposal_id, payload_sha256, scope, ref
            ),
            expires_at=self._now() + self._ttl_seconds,
        )
        self._records[ref] = record
        return ref

    def consume(self, ref: str, context: _ActionContext) -> _ActionRecord | None:
        record = self._records.get(ref)
        if record is None:
            raise ConfigurationSessionError("unknown or forged configuration action")
        if self._now() > record.expires_at:
            raise ConfigurationSessionError("expired configuration action")
        if not hmac.compare_digest(
            record.signature,
            self._signature(
                record.context,
                record.kind,
                record.command_type,
                record.proposal_id,
                record.payload_sha256,
                record.scope,
                ref,
            ),
        ):
            raise ConfigurationSessionError("configuration action signature mismatch")
        if (
            record.context.tenant_id != context.tenant_id
            or record.context.admin_id != context.admin_id
            or record.context.session_id != context.session_id
        ):
            raise ConfigurationSessionError("stale or cross-session configuration action")
        if record.consumed:
            # At-least-once webhook delivery may arrive after the accepted
            # command advanced the revision/current question.  It is still a
            # safe no-op for the same authenticated session, never a second
            # reduction.
            return None
        if record.context != context:
            raise ConfigurationSessionError("stale configuration action")
        record.consumed = True
        return record

    def _signature(
        self,
        context: _ActionContext,
        kind: ActionKind,
        command_type: str | None,
        proposal_id: UUID | None,
        payload_sha256: str | None,
        scope: DraftRef | None,
        ref: str,
    ) -> str:
        scope_value = "" if scope is None else f"{scope.kind}:{scope.token}"
        material = "|".join(
            (
                context.tenant_id,
                context.admin_id,
                context.session_id,
                str(context.revision),
                context.flow_spec_id,
                kind.value,
                command_type or "",
                str(proposal_id or ""),
                payload_sha256 or "",
                scope_value,
                ref,
            )
        ).encode("utf-8")
        return hmac.new(self._signing_key, material, hashlib.sha256).hexdigest()


_ROOT_COLLECTION_SPECS = frozenset(
    {
        "parties",
        "offerings",
        "customer-profiles",
        "relationships",
        "facts",
        "processes",
        "policies",
        "module-registry",
    }
)
_NESTED_COLLECTION_SPECS = frozenset(
    {
        "customer-profile-fields",
        "process-transitions",
        "policy-conditions",
    }
)

_CORE_COMMANDS: dict[str, type[ConfigurationCommand]] = {
    "organization": SetOrganizationCommand,
    "parties": AddPartyCommand,
    "offerings": AddOfferingCommand,
    "customer-profiles": AddCustomerProfileCommand,
    "customer-profile-fields": AddCustomerFieldCommand,
    "party-profile-bindings": BindPartyProfilesCommand,
    "offering-provider-bindings": BindOfferingProvidersCommand,
    "relationships": RelationshipCommand,
    "facts": AddFactCommand,
    "fact-subject-bindings": BindFactSubjectsCommand,
    "processes": AddProcessCommand,
    "process-transitions": AddProcessTransitionCommand,
    "policies": AddPolicyCommand,
    "policy-conditions": AddPolicyConditionCommand,
    "policy-template-bindings": BindPolicyTemplatesCommand,
    "agent-policy": SetAgentPolicyCommand,
    "module-registry": RegisterModuleCommand,
    "lifecycle-review": SetLifecycleCommand,
}


def typed_reducer_coverage_errors() -> tuple[str, ...]:
    """Check the constructive half of core FlowSpec totality.

    A path owner without a typed reducer is only a questionnaire entry; it
    cannot establish reachability.  This independent registry makes that
    omission fail at session construction time.
    """

    core_ids = {spec.id for spec in CORE_FLOW_SPECS}
    command_ids = set(_CORE_COMMANDS)
    errors: list[str] = []
    if missing := core_ids - command_ids:
        errors.append("missing typed reducers: " + ", ".join(sorted(missing)))
    if unexpected := command_ids - core_ids:
        errors.append("unknown typed reducers: " + ", ".join(sorted(unexpected)))
    return tuple(errors)


class ConfigurationFlowSession:
    """One authoritative, in-memory staged configuration conversation.

    The class is deliberately side-effect free outside its own state.  A
    persistence adapter may serialise its staged graph and action records, but
    it must retain the same ``issue → propose → explicit accept → reduce``
    sequence and revision checks.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        admin_id: str,
        session_id: str,
        signing_key: bytes,
        module_providers: Iterable[ModuleFlowProvider] = (),
        now: Callable[[], int] | None = None,
        action_ttl_seconds: int = 900,
    ) -> None:
        self.tenant_id = tenant_id
        self.admin_id = admin_id
        self.session_id = session_id
        self.revision = 0
        if errors := typed_reducer_coverage_errors():
            raise FlowCompilationError("typed reducer registry is not total: " + "; ".join(errors))
        self._registry = ConfigurationFlowRegistry(module_providers)
        self._ledger = ConfigurationActionLedger(
            signing_key, now=now, ttl_seconds=action_ttl_seconds
        )
        self._stage = StagedCompanyDraft()
        self._refs: dict[DraftKind, set[UUID]] = {kind: set() for kind in DraftKind}
        self._root_scopes: dict[str, ScopeState] = {
            spec_id: ScopeState.OPEN for spec_id in _ROOT_COLLECTION_SPECS
        }
        self._nested_scopes: dict[tuple[str, UUID], ScopeState] = {}
        self._pending: ProposalPreview | None = None
        self._pending_command: ConfigurationCommand | None = None
        self._compiled = self._compile_stage()
        self._specs = self._compiled.specs
        self._index = 0

    @property
    def staged(self) -> StagedCompanyDraft:
        """Read-only-by-convention view for preview/rendering code."""

        return deepcopy(self._stage)

    @property
    def pending_proposal(self) -> ProposalPreview | None:
        return self._pending

    @property
    def current_spec(self) -> FlowSpec | None:
        return self._specs[self._index] if self._index < len(self._specs) else None

    @property
    def is_complete(self) -> bool:
        return self.current_spec is None and self._pending is None

    def current_question_id(self) -> str | None:
        spec = self.current_spec
        return spec.id if spec else None

    def ref_for(self, kind: DraftKind, business_id: str) -> DraftRef:
        """Resolve a server-issued ref for renderer/test candidate lists."""

        if kind == DraftKind.ORGANIZATION:
            if self._stage.organization_ref and self._stage.organization and self._stage.organization.id == business_id:
                return self._stage.organization_ref
        elif kind == DraftKind.PARTY:
            for token, item in self._stage.parties.items():
                if item.id == business_id:
                    return DraftRef(kind=kind, token=token)
        elif kind == DraftKind.OFFERING:
            for token, item in self._stage.offerings.items():
                if item.id == business_id:
                    return DraftRef(kind=kind, token=token)
        elif kind == DraftKind.FACT:
            for token, item in self._stage.facts.items():
                if item.id == business_id:
                    return DraftRef(kind=kind, token=token)
        elif kind == DraftKind.CUSTOMER_PROFILE:
            for token, item in self._stage.customer_profiles.items():
                if item.id == business_id:
                    return DraftRef(kind=kind, token=token)
        elif kind == DraftKind.PROCESS:
            for token, item in self._stage.processes.items():
                if item.id == business_id:
                    return DraftRef(kind=kind, token=token)
        elif kind == DraftKind.POLICY:
            for token, item in self._stage.policies.items():
                if item.id == business_id:
                    return DraftRef(kind=kind, token=token)
        elif kind == DraftKind.MODULE:
            for token, item in self._stage.modules.items():
                if item.id == business_id:
                    return DraftRef(kind=kind, token=token)
        raise ConfigurationSessionError(f"no current {kind.value} ref for {business_id!r}")

    def issue_command(
        self,
        command_type: type[ConfigurationCommand],
        *,
        tenant_id: str,
        admin_id: str,
    ) -> str:
        """Issue a server-bound ref for one typed command class.

        The caller selects a *renderer affordance*; it cannot select a JSON
        path or an arbitrary mutation.  The current ``FlowSpec`` determines
        the sole accepted command model.
        """

        context = self._context(tenant_id, admin_id)
        if self._pending is not None:
            raise ConfigurationSessionError("a proposal must be accepted or rejected first")
        expected = self._command_type_for_current_spec()
        if command_type is not expected:
            raise ConfigurationSessionError(
                f"{command_type.__name__} is not enabled for {context.flow_spec_id!r}"
            )
        return self._ledger.issue(context, ActionKind.PROPOSE, command_type=command_type.__name__)

    def propose(
        self,
        action_ref: str,
        command: ConfigurationCommand,
        *,
        tenant_id: str,
        admin_id: str,
    ) -> ProposalPreview | None:
        """Store a typed preview; importantly, this does not mutate the draft."""

        record = self._consume(action_ref, tenant_id=tenant_id, admin_id=admin_id)
        if record is None:
            return None
        if record.kind != ActionKind.PROPOSE or record.command_type != type(command).__name__:
            raise ConfigurationSessionError("configuration action does not authorize this command")
        if type(command) is not self._command_type_for_current_spec():
            raise ConfigurationSessionError("unexpected command model for current configuration step")
        if self._pending is not None:
            raise ConfigurationSessionError("a proposal is already awaiting review")

        canonical_payload = command.model_dump(mode="json")
        payload_sha256 = hashlib.sha256(
            json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        preview = ProposalPreview(
            id=uuid4(),
            flow_spec_id=self._require_current_spec().id,
            revision=self.revision,
            command_type=type(command).__name__,
            canonical_payload=canonical_payload,
            payload_sha256=payload_sha256,
        )
        self._pending = preview
        self._pending_command = command
        return preview

    def issue_accept(
        self,
        proposal_id: UUID,
        *,
        tenant_id: str,
        admin_id: str,
    ) -> str:
        context = self._context(tenant_id, admin_id)
        if self._pending is None or self._pending.id != proposal_id:
            raise ConfigurationSessionError("proposal is not pending in this session")
        return self._ledger.issue(
            context,
            ActionKind.ACCEPT,
            proposal_id=proposal_id,
            payload_sha256=self._pending.payload_sha256,
        )

    def accept(
        self,
        action_ref: str,
        *,
        tenant_id: str,
        admin_id: str,
    ) -> bool:
        """Apply exactly the previously previewed typed command once."""

        record = self._consume(action_ref, tenant_id=tenant_id, admin_id=admin_id)
        if record is None:
            return False
        if record.kind != ActionKind.ACCEPT or self._pending is None or self._pending_command is None:
            raise ConfigurationSessionError("configuration action does not authorize proposal acceptance")
        if record.proposal_id != self._pending.id:
            raise ConfigurationSessionError("configuration action is bound to another proposal")
        if record.payload_sha256 != self._pending.payload_sha256:
            raise ConfigurationSessionError("configuration action payload binding does not match the pending proposal")

        old_state = self._take_state_snapshot()
        try:
            command = self._pending_command
            self._apply(command)
            self._pending = None
            self._pending_command = None
            self.revision += 1
            if self._require_current_spec().kind not in {
                FlowKind.COLLECTION,
                FlowKind.NESTED_COLLECTION,
                FlowKind.GRAPH_EDGE_COLLECTION,
                FlowKind.EVIDENCE_COLLECTION,
                FlowKind.PROCESS_BUILDER,
                FlowKind.POLICY_BUILDER,
            }:
                self._advance()
        except Exception:
            self._restore_state_snapshot(old_state)
            raise
        return True

    def issue_reject(
        self,
        proposal_id: UUID,
        *,
        tenant_id: str,
        admin_id: str,
    ) -> str:
        context = self._context(tenant_id, admin_id)
        if self._pending is None or self._pending.id != proposal_id:
            raise ConfigurationSessionError("proposal is not pending in this session")
        return self._ledger.issue(context, ActionKind.REJECT, proposal_id=proposal_id)

    def reject(
        self,
        action_ref: str,
        *,
        tenant_id: str,
        admin_id: str,
    ) -> bool:
        record = self._consume(action_ref, tenant_id=tenant_id, admin_id=admin_id)
        if record is None:
            return False
        if record.kind != ActionKind.REJECT or self._pending is None or record.proposal_id != self._pending.id:
            raise ConfigurationSessionError("configuration action does not authorize proposal rejection")
        self._pending = None
        self._pending_command = None
        return True

    def issue_close_collection(
        self,
        *,
        tenant_id: str,
        admin_id: str,
        scope: DraftRef | None = None,
    ) -> str:
        context = self._context(tenant_id, admin_id)
        spec = self._require_current_spec()
        if spec.id not in _ROOT_COLLECTION_SPECS | _NESTED_COLLECTION_SPECS:
            raise ConfigurationSessionError("current flow step is not a collection")
        if self._pending is not None:
            raise ConfigurationSessionError("a proposal must be resolved before closing a collection")
        self._validate_scope(spec.id, scope)
        return self._ledger.issue(context, ActionKind.CLOSE_COLLECTION, scope=scope)

    def close_collection(
        self,
        action_ref: str,
        *,
        tenant_id: str,
        admin_id: str,
    ) -> bool:
        record = self._consume(action_ref, tenant_id=tenant_id, admin_id=admin_id)
        if record is None:
            return False
        if record.kind != ActionKind.CLOSE_COLLECTION:
            raise ConfigurationSessionError("configuration action does not authorize collection closure")
        spec = self._require_current_spec()
        self._validate_scope(spec.id, record.scope)
        self._set_scope(spec.id, record.scope, ScopeState.CLOSED)
        self.revision += 1
        if spec.id in _ROOT_COLLECTION_SPECS or self._all_nested_scopes_closed(spec.id):
            self._advance()
        return True

    def issue_defer(self, *, tenant_id: str, admin_id: str) -> str:
        context = self._context(tenant_id, admin_id)
        spec = self._require_current_spec()
        if not spec.can_defer:
            raise ConfigurationSessionError(f"{spec.id!r} cannot be deferred")
        if self._pending is not None:
            raise ConfigurationSessionError("a proposal must be resolved before deferring")
        if spec.id in _NESTED_COLLECTION_SPECS and self._nested_parent_tokens(spec.id):
            raise ConfigurationSessionError("each existing nested collection must be explicitly closed")
        return self._ledger.issue(context, ActionKind.DEFER)

    def defer(self, action_ref: str, *, tenant_id: str, admin_id: str) -> bool:
        record = self._consume(action_ref, tenant_id=tenant_id, admin_id=admin_id)
        if record is None:
            return False
        spec = self._require_current_spec()
        if record.kind != ActionKind.DEFER or not spec.can_defer:
            raise ConfigurationSessionError("configuration action does not authorize deferral")
        if spec.id in _ROOT_COLLECTION_SPECS:
            self._set_scope(spec.id, None, ScopeState.DEFERRED)
        self.revision += 1
        self._advance()
        return True

    def completion_blockers(
        self,
        *,
        allow_lifecycle_pending: bool = False,
        allow_pending_proposal: bool = False,
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        if self._pending is not None and not allow_pending_proposal:
            blockers.append("a proposal is still awaiting explicit admin acceptance")
        current = self.current_spec
        if current is not None and not (allow_lifecycle_pending and current.id == "lifecycle-review"):
            blockers.append(f"flow step {current.id!r} is not completed")
        for spec_id, state in self._root_scopes.items():
            if state == ScopeState.OPEN:
                blockers.append(f"collection {spec_id!r} has not been explicitly closed or deferred")
        for (spec_id, token), state in self._nested_scopes.items():
            if state == ScopeState.OPEN:
                blockers.append(f"nested collection {spec_id!r} for {token} has not been explicitly closed")
        return tuple(blockers)

    def materialize(self) -> CompanyAgentConfig:
        """Return the only durable shape: a fully validated company config."""

        return self._build_config(self._stage.lifecycle, allow_lifecycle_pending=False)

    # ---- command reduction -------------------------------------------------

    def _apply(self, command: ConfigurationCommand) -> None:
        spec = self._require_current_spec()
        if type(command) is not self._command_type_for_spec(spec):
            raise ConfigurationSessionError("command does not match current FlowSpec")

        if isinstance(command, SetOrganizationCommand):
            if self._stage.organization is not None:
                raise ConfigurationSessionError("organization is already configured in this session")
            self._ensure_unique_entity_id(command.organization.id)
            ref = self._new_ref(DraftKind.ORGANIZATION)
            self._stage.organization = command.organization
            self._stage.organization_ref = ref
        elif isinstance(command, AddPartyCommand):
            self._require_root_open("parties")
            self._ensure_unique_entity_id(command.party.id)
            ref = self._new_ref(DraftKind.PARTY)
            self._stage.parties[ref.token] = command.party
        elif isinstance(command, AddOfferingCommand):
            self._require_root_open("offerings")
            self._ensure_unique_entity_id(command.offering.id)
            ref = self._new_ref(DraftKind.OFFERING)
            self._stage.offerings[ref.token] = command.offering
        elif isinstance(command, AddCustomerProfileCommand):
            self._require_root_open("customer-profiles")
            self._ensure_unique_nonentity_id(DraftKind.CUSTOMER_PROFILE, command.profile.id)
            ref = self._new_ref(DraftKind.CUSTOMER_PROFILE)
            self._stage.customer_profiles[ref.token] = command.profile
            self._stage.customer_fields[ref.token] = []
            self._nested_scopes[("customer-profile-fields", ref.token)] = ScopeState.OPEN
        elif isinstance(command, AddCustomerFieldCommand):
            profile = self._require_ref(command.profile, DraftKind.CUSTOMER_PROFILE)
            self._require_nested_open("customer-profile-fields", profile.token)
            fields = self._stage.customer_fields[profile.token]
            if any(item.id == command.field.id for item in fields):
                raise ConfigurationSessionError(f"customer field {command.field.id!r} already exists")
            fields.append(command.field)
        elif isinstance(command, BindPartyProfilesCommand):
            self._apply_party_profiles(command)
        elif isinstance(command, BindOfferingProvidersCommand):
            self._apply_offering_providers(command)
        elif isinstance(command, RelationshipCommand):
            self._require_root_open("relationships")
            self._require_entity_ref(command.subject)
            self._require_entity_ref(command.object)
            self._stage.relationships.append(
                _StagedRelationship(command.subject, command.predicate, command.object)
            )
        elif isinstance(command, AddFactCommand):
            self._require_root_open("facts")
            self._ensure_unique_nonentity_id(DraftKind.FACT, command.fact.id)
            ref = self._new_ref(DraftKind.FACT)
            self._stage.facts[ref.token] = command.fact
        elif isinstance(command, BindFactSubjectsCommand):
            self._apply_fact_subjects(command)
        elif isinstance(command, AddProcessCommand):
            self._require_root_open("processes")
            self._ensure_unique_nonentity_id(DraftKind.PROCESS, command.process.id)
            ref = self._new_ref(DraftKind.PROCESS)
            self._stage.processes[ref.token] = command.process
            self._stage.process_transitions[ref.token] = []
            self._nested_scopes[("process-transitions", ref.token)] = ScopeState.OPEN
        elif isinstance(command, AddProcessTransitionCommand):
            process = self._require_ref(command.process, DraftKind.PROCESS)
            self._require_nested_open("process-transitions", process.token)
            existing = self._stage.process_transitions[process.token]
            candidate = BusinessProcess.model_validate(
                {
                    **self._stage.processes[process.token].model_dump(mode="json"),
                    "transitions": [item.model_dump(mode="json") for item in existing]
                    + [command.transition.model_dump(mode="json")],
                }
            )
            self._stage.process_transitions[process.token] = list(candidate.transitions)
        elif isinstance(command, AddPolicyCommand):
            self._require_root_open("policies")
            self._ensure_unique_nonentity_id(DraftKind.POLICY, command.policy.id)
            ref = self._new_ref(DraftKind.POLICY)
            self._stage.policies[ref.token] = command.policy
            self._stage.policy_conditions[ref.token] = []
            self._nested_scopes[("policy-conditions", ref.token)] = ScopeState.OPEN
        elif isinstance(command, AddPolicyConditionCommand):
            policy = self._require_ref(command.policy, DraftKind.POLICY)
            self._require_nested_open("policy-conditions", policy.token)
            self._stage.policy_conditions[policy.token].append(command.condition)
        elif isinstance(command, BindPolicyTemplatesCommand):
            self._apply_policy_templates(command)
        elif isinstance(command, SetAgentPolicyCommand):
            if self._stage.agent is not None:
                raise ConfigurationSessionError("agent policy is already configured in this session")
            self._stage.agent = command.agent
        elif isinstance(command, RegisterModuleCommand):
            self._register_module(command)
        elif isinstance(command, ConfigureModuleCommand):
            self._configure_module(command, spec)
        elif isinstance(command, SetLifecycleCommand):
            if command.lifecycle == ConfigurationLifecycle.DRAFT:
                raise ConfigurationSessionError("lifecycle review cannot move a complete session back to draft")
            # This calls all graph/ref/module validators before lifecycle can
            # become reviewable or approved.
            self._build_config(command.lifecycle, allow_lifecycle_pending=True)
            self._stage.lifecycle = command.lifecycle
        else:  # pragma: no cover - registry test prevents an unhandled command class.
            raise ConfigurationSessionError(f"unhandled configuration command {type(command).__name__}")

    def _apply_party_profiles(self, command: BindPartyProfilesCommand) -> None:
        expected = set(self._stage.parties)
        seen: set[UUID] = set()
        bindings: dict[UUID, tuple[UUID, ...]] = {}
        for binding in command.bindings:
            party = self._require_ref(binding.party, DraftKind.PARTY)
            if party.token in seen:
                raise ConfigurationSessionError("party profile bindings must be unique per party")
            seen.add(party.token)
            profile_tokens: list[UUID] = []
            for profile_ref in binding.profiles:
                profile = self._require_ref(profile_ref, DraftKind.CUSTOMER_PROFILE)
                profile_tokens.append(profile.token)
            if len(profile_tokens) != len(set(profile_tokens)):
                raise ConfigurationSessionError("party profile bindings contain a duplicate profile")
            bindings[party.token] = tuple(profile_tokens)
        if seen != expected:
            raise ConfigurationSessionError("profile binding must explicitly cover every staged party")
        self._stage.party_profiles = bindings

    def _apply_offering_providers(self, command: BindOfferingProvidersCommand) -> None:
        expected = set(self._stage.offerings)
        seen: set[UUID] = set()
        bindings: dict[UUID, UUID] = {}
        for binding in command.bindings:
            offering = self._require_ref(binding.offering, DraftKind.OFFERING)
            provider = self._require_entity_ref(binding.provider)
            if offering.token in seen:
                raise ConfigurationSessionError("offering provider bindings must be unique per offering")
            seen.add(offering.token)
            bindings[offering.token] = provider.token
        if seen != expected:
            raise ConfigurationSessionError("provider binding must explicitly cover every staged offering")
        self._stage.offering_providers = bindings

    def _apply_fact_subjects(self, command: BindFactSubjectsCommand) -> None:
        expected = set(self._stage.facts)
        seen: set[UUID] = set()
        bindings: dict[UUID, UUID] = {}
        for binding in command.bindings:
            fact = self._require_ref(binding.fact, DraftKind.FACT)
            subject = self._require_entity_ref(binding.subject)
            if fact.token in seen:
                raise ConfigurationSessionError("fact subject bindings must be unique per fact")
            seen.add(fact.token)
            bindings[fact.token] = subject.token
        if seen != expected:
            raise ConfigurationSessionError("subject binding must explicitly cover every staged fact")
        self._stage.fact_subjects = bindings

    def _apply_policy_templates(self, command: BindPolicyTemplatesCommand) -> None:
        expected = set(self._stage.policies)
        seen: set[UUID] = set()
        bindings: dict[UUID, UUID | None] = {}
        for binding in command.bindings:
            policy = self._require_ref(binding.policy, DraftKind.POLICY)
            if policy.token in seen:
                raise ConfigurationSessionError("policy template bindings must be unique per policy")
            seen.add(policy.token)
            core = self._stage.policies[policy.token]
            if core.effect == PolicyEffect.USE_TEMPLATE:
                if binding.template_fact is None:
                    raise ConfigurationSessionError("use_template policy requires an explicit fact binding")
                fact = self._require_ref(binding.template_fact, DraftKind.FACT)
                bindings[policy.token] = fact.token
            else:
                if binding.template_fact is not None:
                    raise ConfigurationSessionError("only use_template policy may bind a template fact")
                bindings[policy.token] = None
        if seen != expected:
            raise ConfigurationSessionError("template binding must explicitly cover every staged policy")
        self._stage.policy_templates = bindings

    def _register_module(self, command: RegisterModuleCommand) -> None:
        self._require_root_open("module-registry")
        if any(item.id == command.module.id for item in self._stage.modules.values()):
            raise ConfigurationSessionError(f"module {command.module.id!r} already exists")
        module = DomainModule(id=command.module.id, schema_version=command.module.schema_version, config={})
        if self._registry.provider_for(module) is None:
            raise ConfigurationSessionError("unregistered module cannot enter a universal configuration flow")
        ref = self._new_ref(DraftKind.MODULE)
        self._stage.modules[ref.token] = command.module
        self._stage.module_configs[ref.token] = {}
        self._refresh_compiled_flow()

    def _configure_module(self, command: ConfigureModuleCommand, spec: FlowSpec) -> None:
        module_ref = self._require_ref(command.module, DraftKind.MODULE)
        core = self._stage.modules[module_ref.token]
        module = DomainModule(id=core.id, schema_version=core.schema_version, config=command.config)
        if module_config_path(module) not in spec.target_paths:
            raise ConfigurationSessionError("current module flow cannot configure this module instance")
        provider = self._registry.provider_for(module)
        if provider is None:
            raise ConfigurationSessionError("module provider is no longer registered")
        try:
            provider.validate_config(module)
        except Exception as exc:
            raise ConfigurationSessionError(f"module config is invalid: {type(exc).__name__}: {exc}") from exc
        self._stage.module_configs[module_ref.token] = deepcopy(command.config)
        self._refresh_compiled_flow()

    # ---- staging → durable model ------------------------------------------

    def _build_config(
        self,
        lifecycle: ConfigurationLifecycle,
        *,
        allow_lifecycle_pending: bool,
    ) -> CompanyAgentConfig:
        blockers = list(
            self.completion_blockers(
                allow_lifecycle_pending=allow_lifecycle_pending,
                allow_pending_proposal=allow_lifecycle_pending,
            )
        )
        if self._stage.organization is None:
            blockers.append("organization has not been configured")
        if self._stage.agent is None:
            blockers.append("agent policy has not been configured")
        for token in self._stage.parties:
            if token not in self._stage.party_profiles:
                blockers.append(f"party {self._stage.parties[token].id!r} has no explicit profile binding")
        for token in self._stage.offerings:
            if token not in self._stage.offering_providers:
                blockers.append(f"offering {self._stage.offerings[token].id!r} has no explicit provider binding")
        for token in self._stage.facts:
            if token not in self._stage.fact_subjects:
                blockers.append(f"fact {self._stage.facts[token].id!r} has no explicit subject binding")
        for token, policy in self._stage.policies.items():
            if not self._stage.policy_conditions[token]:
                blockers.append(f"policy {policy.id!r} has no condition")
            if token not in self._stage.policy_templates:
                blockers.append(f"policy {policy.id!r} has no explicit template binding")
        for token, core in self._stage.modules.items():
            if token not in self._stage.module_configs:
                blockers.append(f"module {core.id!r} has no explicit configuration")
        if blockers:
            raise MaterializationError(blockers)

        organization = self._stage.organization
        assert organization is not None  # Covered above; keeps type checkers precise.
        parties = [
            {
                **core.model_dump(mode="json"),
                "profile_ids": [self._stage.customer_profiles[item].id for item in self._stage.party_profiles[token]],
            }
            for token, core in self._stage.parties.items()
        ]
        offerings = [
            {
                **core.model_dump(mode="json"),
                "provider_id": self._entity_id_from_token(self._stage.offering_providers[token]),
            }
            for token, core in self._stage.offerings.items()
        ]
        relationships = [
            {
                "subject_id": self._entity_id(item.subject),
                "predicate": item.predicate,
                "object_id": self._entity_id(item.object),
            }
            for item in self._stage.relationships
        ]
        facts = [
            {
                **core.model_dump(mode="json"),
                "subject_id": self._entity_id_from_token(self._stage.fact_subjects[token]),
            }
            for token, core in self._stage.facts.items()
        ]
        profiles = [
            {
                **core.model_dump(mode="json"),
                "fields": [item.model_dump(mode="json") for item in self._stage.customer_fields[token]],
            }
            for token, core in self._stage.customer_profiles.items()
        ]
        processes = [
            {
                **core.model_dump(mode="json"),
                "transitions": [item.model_dump(mode="json") for item in self._stage.process_transitions[token]],
            }
            for token, core in self._stage.processes.items()
        ]
        policies = [
            {
                "id": core.id,
                "when": [item.model_dump(mode="json") for item in self._stage.policy_conditions[token]],
                "effect": core.effect.value,
                "template_fact_id": (
                    self._stage.facts[self._stage.policy_templates[token]].id
                    if self._stage.policy_templates[token] is not None
                    else None
                ),
            }
            for token, core in self._stage.policies.items()
        ]
        modules = [
            {
                "id": core.id,
                "schema_version": core.schema_version,
                "config": self._stage.module_configs[token],
            }
            for token, core in self._stage.modules.items()
        ]
        data = {
            "schema_version": "company-agent-config/1.0",
            "lifecycle": lifecycle.value,
            "organization": organization.model_dump(mode="json"),
            "parties": parties,
            "offerings": offerings,
            "relationships": relationships,
            "facts": facts,
            "customer_profiles": profiles,
            "processes": processes,
            "policies": policies,
            "agent": self._stage.agent.model_dump(mode="json") if self._stage.agent else None,
            "modules": modules,
        }
        try:
            config = CompanyAgentConfig.model_validate(data)
        except ValueError as exc:
            raise MaterializationError((f"canonical company schema validation failed: {exc}",)) from exc

        # The module registry has a broader contract than Pydantic's opaque
        # JSON field. A result with an unknown/invalid extension is not a
        # usable company flow even when its JSON happens to be syntactically
        # valid.
        compiled = self._registry.compile(config)
        try:
            compiled.require_structural_completeness()
        except FlowCompilationError as exc:
            raise MaterializationError((str(exc),)) from exc
        return config

    # ---- flow/session mechanics -------------------------------------------

    def _compile_stage(self):
        """Compile only the registered module envelope; core may stay partial."""

        envelope = CompanyAgentConfig.model_validate(
            {
                "schema_version": "company-agent-config/1.0",
                "lifecycle": ConfigurationLifecycle.DRAFT.value,
                "modules": [
                    {
                        "id": core.id,
                        "schema_version": core.schema_version,
                        "config": self._stage.module_configs[token],
                    }
                    for token, core in self._stage.modules.items()
                ],
            }
        )
        return self._registry.compile(envelope)

    def _refresh_compiled_flow(self) -> None:
        current_id = self.current_spec.id if hasattr(self, "_specs") and self.current_spec else None
        self._compiled = self._compile_stage()
        self._specs = self._compiled.specs
        if current_id is not None:
            try:
                self._index = next(index for index, spec in enumerate(self._specs) if spec.id == current_id)
            except StopIteration as exc:  # A provider may not remove a current spec mid-session.
                raise ConfigurationSessionError(
                    f"current FlowSpec {current_id!r} disappeared after a staged update"
                ) from exc

    def _context(self, tenant_id: str, admin_id: str) -> _ActionContext:
        if tenant_id != self.tenant_id or admin_id != self.admin_id:
            raise ConfigurationSessionError("tenant or admin is not authorized for this configuration session")
        # A consumed final action must still be a no-op on duplicate webhook
        # delivery.  Use a terminal marker here; the ledger checks identity
        # and consumed state before it compares the stale revision/spec.
        spec = self.current_spec
        return _ActionContext(
            tenant_id=tenant_id,
            admin_id=admin_id,
            session_id=self.session_id,
            revision=self.revision,
            flow_spec_id=spec.id if spec else "__complete__",
        )

    def _consume(self, action_ref: str, *, tenant_id: str, admin_id: str) -> _ActionRecord | None:
        # Constructing the context first rejects a request after a flow has
        # completed and binds the ledger to the exact current spec/revision.
        return self._ledger.consume(action_ref, self._context(tenant_id, admin_id))

    def _require_current_spec(self) -> FlowSpec:
        spec = self.current_spec
        if spec is None:
            raise ConfigurationSessionError("configuration flow is already complete")
        return spec

    def _command_type_for_current_spec(self) -> type[ConfigurationCommand]:
        return self._command_type_for_spec(self._require_current_spec())

    def _command_type_for_spec(self, spec: FlowSpec) -> type[ConfigurationCommand]:
        if spec.kind == FlowKind.MODULE_CONFIG:
            return ConfigureModuleCommand
        try:
            return _CORE_COMMANDS[spec.id]
        except KeyError as exc:
            raise ConfigurationSessionError(f"no typed reducer is registered for {spec.id!r}") from exc

    def _advance(self) -> None:
        if self.current_spec is None:
            raise ConfigurationSessionError("configuration flow is already complete")
        self._index += 1

    def _take_state_snapshot(self) -> tuple[Any, ...]:
        return (
            deepcopy(self._stage),
            deepcopy(self._refs),
            deepcopy(self._root_scopes),
            deepcopy(self._nested_scopes),
            self._compiled,
            self._specs,
            self._index,
            self._pending,
            self._pending_command,
            self.revision,
        )

    def _restore_state_snapshot(self, snapshot: tuple[Any, ...]) -> None:
        (
            self._stage,
            self._refs,
            self._root_scopes,
            self._nested_scopes,
            self._compiled,
            self._specs,
            self._index,
            self._pending,
            self._pending_command,
            self.revision,
        ) = snapshot

    # ---- scopes ------------------------------------------------------------

    def _validate_scope(self, spec_id: str, scope: DraftRef | None) -> None:
        if spec_id in _ROOT_COLLECTION_SPECS:
            if scope is not None:
                raise ConfigurationSessionError("root collection close cannot name a child scope")
            if self._root_scopes[spec_id] != ScopeState.OPEN:
                raise ConfigurationSessionError("collection is no longer open")
            return
        if spec_id == "customer-profile-fields":
            ref = self._require_ref_value(scope, DraftKind.CUSTOMER_PROFILE)
        elif spec_id == "process-transitions":
            ref = self._require_ref_value(scope, DraftKind.PROCESS)
        elif spec_id == "policy-conditions":
            ref = self._require_ref_value(scope, DraftKind.POLICY)
        else:
            raise ConfigurationSessionError(f"unknown collection scope {spec_id!r}")
        if self._nested_scopes.get((spec_id, ref.token)) != ScopeState.OPEN:
            raise ConfigurationSessionError("nested collection is no longer open")

    def _set_scope(self, spec_id: str, scope: DraftRef | None, state: ScopeState) -> None:
        if spec_id in _ROOT_COLLECTION_SPECS:
            self._root_scopes[spec_id] = state
        else:
            assert scope is not None
            self._nested_scopes[(spec_id, scope.token)] = state

    def _nested_parent_tokens(self, spec_id: str) -> tuple[UUID, ...]:
        if spec_id == "customer-profile-fields":
            return tuple(self._stage.customer_profiles)
        if spec_id == "process-transitions":
            return tuple(self._stage.processes)
        if spec_id == "policy-conditions":
            return tuple(self._stage.policies)
        raise ConfigurationSessionError(f"unknown nested collection {spec_id!r}")

    def _all_nested_scopes_closed(self, spec_id: str) -> bool:
        return all(
            self._nested_scopes.get((spec_id, token)) == ScopeState.CLOSED
            for token in self._nested_parent_tokens(spec_id)
        )

    def _require_root_open(self, spec_id: str) -> None:
        if self._root_scopes.get(spec_id) != ScopeState.OPEN:
            raise ConfigurationSessionError(f"collection {spec_id!r} is closed or deferred")

    def _require_nested_open(self, spec_id: str, token: UUID) -> None:
        if self._nested_scopes.get((spec_id, token)) != ScopeState.OPEN:
            raise ConfigurationSessionError(f"nested collection {spec_id!r} is closed")

    # ---- staged reference helpers -----------------------------------------

    def _new_ref(self, kind: DraftKind) -> DraftRef:
        token = uuid4()
        self._refs[kind].add(token)
        return DraftRef(kind=kind, token=token)

    def _require_ref(self, ref: DraftRef, expected_kind: DraftKind) -> DraftRef:
        if ref.kind != expected_kind:
            raise ConfigurationSessionError(
                f"expected a {expected_kind.value} reference, got {ref.kind.value}"
            )
        if ref.token not in self._refs[expected_kind]:
            raise ConfigurationSessionError("unknown, stale, or cross-session draft reference")
        return ref

    def _require_ref_value(self, ref: DraftRef | None, expected_kind: DraftKind) -> DraftRef:
        if ref is None:
            raise ConfigurationSessionError("nested collection closure requires a server-issued parent reference")
        return self._require_ref(ref, expected_kind)

    def _require_entity_ref(self, ref: DraftRef) -> DraftRef:
        if ref.kind not in {DraftKind.ORGANIZATION, DraftKind.PARTY, DraftKind.OFFERING}:
            raise ConfigurationSessionError("reference must point to a staged organization, party, or offering")
        return self._require_ref(ref, ref.kind)

    def _entity_id(self, ref: DraftRef) -> str:
        self._require_entity_ref(ref)
        return self._entity_id_from_token(ref.token)

    def _entity_id_from_token(self, token: UUID) -> str:
        if self._stage.organization_ref and token == self._stage.organization_ref.token:
            assert self._stage.organization is not None
            return self._stage.organization.id
        if token in self._stage.parties:
            return self._stage.parties[token].id
        if token in self._stage.offerings:
            return self._stage.offerings[token].id
        raise ConfigurationSessionError("reference does not resolve to an entity in this session")

    def _ensure_unique_entity_id(self, business_id: str) -> None:
        current = []
        if self._stage.organization is not None:
            current.append(self._stage.organization.id)
        current.extend(item.id for item in self._stage.parties.values())
        current.extend(item.id for item in self._stage.offerings.values())
        if business_id in current:
            raise ConfigurationSessionError(f"entity id {business_id!r} already exists")

    def _ensure_unique_nonentity_id(self, kind: DraftKind, business_id: str) -> None:
        groups: dict[DraftKind, dict[UUID, Any]] = {
            DraftKind.FACT: self._stage.facts,
            DraftKind.CUSTOMER_PROFILE: self._stage.customer_profiles,
            DraftKind.PROCESS: self._stage.processes,
            DraftKind.POLICY: self._stage.policies,
            DraftKind.MODULE: self._stage.modules,
        }
        if any(item.id == business_id for item in groups[kind].values()):
            raise ConfigurationSessionError(f"{kind.value} id {business_id!r} already exists")
