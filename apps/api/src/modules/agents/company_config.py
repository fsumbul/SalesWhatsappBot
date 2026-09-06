"""Universal, versioned company configuration for customer-facing agents.

This module deliberately models a company as a small typed graph instead of
as a sector-specific form.  A company instance contains parties, offerings,
relationships, facts, business processes and policies.  Sector-specific data
belongs at an explicitly namespaced module boundary; it never leaks into the
top-level schema as an unvalidated ``dict[str, Any]``.

The configuration is a *blueprint*, not a customer record.  Individual
customers and per-conversation state will be separate runtime aggregates.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

LEGACY_COMPANY_CONFIG_SCHEMA_VERSION = "company-agent-config/1.0"
COMPANY_CONFIG_SCHEMA_VERSION = "company-agent-config/1.2"

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3}|-[A-Z]{2})?$")

Identifier = Annotated[str, Field(pattern=_IDENTIFIER_RE.pattern, min_length=1, max_length=80)]
Locale = Annotated[str, Field(pattern=_LOCALE_RE.pattern, min_length=2, max_length=16)]
LocalizedText = dict[Locale, Annotated[str, Field(min_length=1, max_length=4000)]]
LocalizedInteractionLabel = dict[
    Locale,
    Annotated[str, Field(min_length=1, max_length=24)],
]
StarterInteractionLabel = dict[
    Locale,
    Annotated[str, Field(min_length=1, max_length=20)],
]
HttpsUrl = Annotated[
    str,
    Field(pattern=r"^https://[^\s]+$", min_length=9, max_length=1000),
]


class StrictModel(BaseModel):
    """Every known object is closed; extensibility has one explicit boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConfigurationLifecycle(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"


class PartyKind(StrEnum):
    ORGANIZATION = "organization"
    PERSON = "person"
    HOUSEHOLD = "household"
    TEAM = "team"
    ANONYMOUS_CONTACT = "anonymous_contact"


class OfferingKind(StrEnum):
    PHYSICAL_PRODUCT = "physical_product"
    DIGITAL_PRODUCT = "digital_product"
    PROFESSIONAL_SERVICE = "professional_service"
    FIELD_SERVICE = "field_service"
    SUBSCRIPTION = "subscription"
    PROJECT = "project"
    MARKETPLACE_LISTING = "marketplace_listing"
    OTHER = "other"


class CustomerLinkKind(StrEnum):
    WEBSITE = "website"
    PRODUCT_PAGE = "product_page"
    CATALOG = "catalog"
    QUOTE_FORM = "quote_form"
    CONTACT = "contact"
    SOCIAL = "social"


class MediaKind(StrEnum):
    IMAGE = "image"


class FactCategory(StrEnum):
    CAPABILITY = "capability"
    SPECIFICATION = "specification"
    COMMERCIAL_RULE = "commercial_rule"
    AVAILABILITY = "availability"
    ELIGIBILITY = "eligibility"
    DELIVERY = "delivery"
    SUPPORT = "support"
    SOCIAL = "social"
    OTHER = "other"


class ResponseMode(StrEnum):
    STRICT = "strict"
    GROUNDED = "grounded"


class UnknownFactAction(StrEnum):
    HANDOFF = "handoff"
    ASK_CLARIFICATION = "ask_clarification"
    DECLINE = "decline"


class ConversationPurpose(StrEnum):
    """The explicit job the agent is allowed to perform for this company."""

    INFORMATION = "information"
    LEAD_CAPTURE = "lead_capture"
    SALES = "sales"
    SUPPORT = "support"
    APPOINTMENT = "appointment"
    ORDER_STATUS = "order_status"
    ACCOUNT = "account"


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    HANDOFF = "handoff"
    ASK_CLARIFICATION = "ask_clarification"
    USE_TEMPLATE = "use_template"


class CustomerFieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    ENUM = "enum"


class CustomerLink(StrictModel):
    """An explicitly approved customer-facing HTTPS destination."""

    id: Identifier
    kind: CustomerLinkKind
    display_names: LocalizedText
    url: HttpsUrl


class MediaAsset(StrictModel):
    """An approved, publicly retrievable customer-facing media asset."""

    id: Identifier
    kind: MediaKind
    url: HttpsUrl
    mime_type: str = Field(min_length=3, max_length=127)
    size_bytes: int = Field(gt=0, le=5 * 1024 * 1024)
    captions: LocalizedText | None = None
    provenance: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def _is_supported_whatsapp_image(self) -> MediaAsset:
        if self.mime_type not in {"image/jpeg", "image/png"}:
            raise ValueError("unsupported WhatsApp image MIME type")
        return self


class WhatsAppPresentation(StrictModel):
    """Approved session-message assets, separate from tenant credentials."""

    assets: list[MediaAsset] = Field(default_factory=list, max_length=500)
    offering_media: dict[Identifier, Identifier] = Field(
        default_factory=dict,
        max_length=10_000,
    )

    @model_validator(mode="after")
    def _media_references_are_valid(self) -> WhatsAppPresentation:
        _assert_unique((asset.id for asset in self.assets), "media asset ids")
        asset_ids = {asset.id for asset in self.assets}
        if any(asset_id not in asset_ids for asset_id in self.offering_media.values()):
            raise ValueError("offering media must refer to a known media asset")
        return self


class Organization(StrictModel):
    """The configured company itself; ``id`` is a graph node like every other entity."""

    id: Identifier = "company"
    display_names: LocalizedText
    legal_name: str | None = Field(default=None, min_length=1, max_length=240)
    markets: list[str] = Field(default_factory=list, max_length=100)
    customer_links: list[CustomerLink] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _link_ids_are_unique(self) -> Organization:
        _assert_unique((link.id for link in self.customer_links), "organization link ids")
        return self


class Party(StrictModel):
    """A potential customer, partner, supplier or another participant."""

    id: Identifier
    kind: PartyKind
    roles: list[Identifier] = Field(default_factory=list, max_length=30)
    display_names: LocalizedText | None = None
    profile_ids: list[Identifier] = Field(default_factory=list, max_length=20)


class Offering(StrictModel):
    id: Identifier
    kind: OfferingKind
    display_names: LocalizedText
    interaction_labels: LocalizedInteractionLabel | None = None
    overview_fact_id: Identifier | None = None
    provider_id: Identifier = "company"
    active: bool = True
    customer_links: list[CustomerLink] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _link_ids_are_unique(self) -> Offering:
        _assert_unique((link.id for link in self.customer_links), "offering link ids")
        return self


class Relationship(StrictModel):
    """A typed graph edge.  The predicate is data, not executable behavior."""

    subject_id: Identifier
    predicate: Identifier
    object_id: Identifier


class Fact(StrictModel):
    """A source-controlled piece of knowledge available to the controller."""

    id: Identifier
    subject_id: Identifier
    category: FactCategory
    value: JsonValue
    customer_visible: bool = False
    customer_text: LocalizedText | None = None
    selection_guidance: LocalizedText | None = None
    search_terms: list[Annotated[str, Field(min_length=2, max_length=120)]] = Field(
        default_factory=list,
        max_length=100,
    )
    source: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def _visible_facts_need_customer_text(self) -> Fact:
        if self.customer_visible and not self.customer_text:
            raise ValueError("customer_visible facts require customer_text")
        return self


class GuidedFactAction(StrictModel):
    """A customer-visible fact exposed as a deterministic quick action."""

    fact_id: Identifier
    display_names: StarterInteractionLabel


class CustomerField(StrictModel):
    """Declares a customer-data coordinate without storing a customer value."""

    id: Identifier
    type: CustomerFieldType
    required: bool = False
    allowed_values: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _enum_fields_need_values(self) -> CustomerField:
        if self.type == CustomerFieldType.ENUM and not self.allowed_values:
            raise ValueError("enum customer fields require allowed_values")
        if self.type != CustomerFieldType.ENUM and self.allowed_values:
            raise ValueError("allowed_values is only valid for enum customer fields")
        return self


class CustomerProfileDefinition(StrictModel):
    """A reusable shape for any type of customer, not an individual customer."""

    id: Identifier
    applies_to: list[PartyKind] = Field(min_length=1, max_length=10)
    fields: list[CustomerField] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _field_ids_are_unique(self) -> CustomerProfileDefinition:
        _assert_unique((field.id for field in self.fields), "customer profile field ids")
        return self


class ProcessTransition(StrictModel):
    from_state: Identifier
    event: Identifier
    to_state: Identifier
    action: Identifier


class BusinessProcess(StrictModel):
    """A deterministic state machine whose actions are later bound to runtime code."""

    id: Identifier
    states: list[Identifier] = Field(min_length=1, max_length=100)
    initial_state: Identifier
    allowed_actions: list[Identifier] = Field(min_length=1, max_length=100)
    transitions: list[ProcessTransition] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _validate_state_machine(self) -> BusinessProcess:
        _assert_unique(self.states, "process states")
        _assert_unique(self.allowed_actions, "process allowed_actions")
        if self.initial_state not in self.states:
            raise ValueError("initial_state must be one of states")
        for transition in self.transitions:
            if transition.from_state not in self.states or transition.to_state not in self.states:
                raise ValueError("transition states must exist in states")
            if transition.action not in self.allowed_actions:
                raise ValueError("transition action must exist in allowed_actions")
        return self


class PolicyCondition(StrictModel):
    field: Identifier
    operator: Literal["equals", "contains", "present", "in"]
    value: JsonValue | None = None


class PolicyRule(StrictModel):
    id: Identifier
    when: list[PolicyCondition] = Field(min_length=1, max_length=20)
    effect: PolicyEffect
    template_fact_id: Identifier | None = None

    @model_validator(mode="after")
    def _template_effect_needs_fact(self) -> PolicyRule:
        if self.effect == PolicyEffect.USE_TEMPLATE and self.template_fact_id is None:
            raise ValueError("use_template policies require template_fact_id")
        if self.effect != PolicyEffect.USE_TEMPLATE and self.template_fact_id is not None:
            raise ValueError("template_fact_id is only valid for use_template policies")
        return self


class AgentReplyPolicy(StrictModel):
    response_mode: ResponseMode = ResponseMode.GROUNDED
    purposes: list[ConversationPurpose] = Field(min_length=1, max_length=7)
    supported_locales: list[Locale] = Field(min_length=1, max_length=20)
    default_locale: Locale
    max_characters: int = Field(default=450, ge=1, le=4000)
    require_fact_ids_for_claims: bool = True
    unknown_fact_action: UnknownFactAction = UnknownFactAction.HANDOFF
    handoff_fact_id: Identifier | None = None
    semantic_fallback_fact_ids: list[Identifier] = Field(
        default_factory=list,
        max_length=24,
    )
    starter_trigger_fact_ids: list[Identifier] = Field(
        default_factory=list,
        max_length=100,
    )
    starter_actions: list[GuidedFactAction] = Field(
        default_factory=list,
        max_length=3,
    )

    @model_validator(mode="after")
    def _default_locale_is_supported(self) -> AgentReplyPolicy:
        _assert_unique(self.purposes, "agent purposes")
        _assert_unique(self.supported_locales, "supported_locales")
        _assert_unique(
            self.semantic_fallback_fact_ids,
            "semantic_fallback_fact_ids",
        )
        _assert_unique(
            self.starter_trigger_fact_ids,
            "starter_trigger_fact_ids",
        )
        _assert_unique(
            (action.fact_id for action in self.starter_actions),
            "starter action fact ids",
        )
        if self.default_locale not in self.supported_locales:
            raise ValueError("default_locale must be one of supported_locales")
        if bool(self.starter_trigger_fact_ids) != bool(self.starter_actions):
            raise ValueError("starter triggers and actions must be configured together")
        return self


class DomainModule(StrictModel):
    """Explicit extensibility boundary for future registered domain packs.

    ``config`` is intentionally quarantined from core behavior: it may not be
    used for customer replies until a matching module validator/runtime adapter
    is registered.  This preserves a closed core while leaving room for future
    sectors without changing the universal model.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,119}$", max_length=120)
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=32)
    config: dict[str, JsonValue] = Field(default_factory=dict)


class CompanyAgentConfig(StrictModel):
    """The universal company blueprint persisted with one AgentVersion."""

    schema_version: Literal[
        "company-agent-config/1.0",
        "company-agent-config/1.1",
        "company-agent-config/1.2",
    ] = COMPANY_CONFIG_SCHEMA_VERSION
    lifecycle: ConfigurationLifecycle = ConfigurationLifecycle.DRAFT
    organization: Organization | None = None
    parties: list[Party] = Field(default_factory=list, max_length=10_000)
    offerings: list[Offering] = Field(default_factory=list, max_length=10_000)
    relationships: list[Relationship] = Field(default_factory=list, max_length=50_000)
    facts: list[Fact] = Field(default_factory=list, max_length=50_000)
    customer_profiles: list[CustomerProfileDefinition] = Field(default_factory=list, max_length=100)
    processes: list[BusinessProcess] = Field(default_factory=list, max_length=100)
    policies: list[PolicyRule] = Field(default_factory=list, max_length=1_000)
    agent: AgentReplyPolicy | None = None
    whatsapp_presentation: WhatsAppPresentation | None = None
    modules: list[DomainModule] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _validate_company_graph(self) -> CompanyAgentConfig:
        if (
            self.whatsapp_presentation is not None
            and self.schema_version != COMPANY_CONFIG_SCHEMA_VERSION
        ):
            raise ValueError(
                "WhatsApp presentation requires company-agent-config/1.2"
            )
        if self.schema_version == LEGACY_COMPANY_CONFIG_SCHEMA_VERSION:
            uses_conversation_facts = any(
                fact.category == FactCategory.SOCIAL or fact.selection_guidance is not None
                for fact in self.facts
            )
            uses_conversation_policy = self.agent is not None and bool(
                self.agent.semantic_fallback_fact_ids
                or self.agent.starter_trigger_fact_ids
                or self.agent.starter_actions
            )
            if uses_conversation_facts or uses_conversation_policy:
                raise ValueError(
                    "company-agent-config/1.0 does not support conversation fields; "
                    "use company-agent-config/1.1"
                )

        if self.lifecycle != ConfigurationLifecycle.DRAFT:
            if self.organization is None:
                raise ValueError("organization is required outside draft lifecycle")
            if self.agent is None:
                raise ValueError("agent policy is required outside draft lifecycle")

        entity_ids = [party.id for party in self.parties] + [
            offering.id for offering in self.offerings
        ]
        if self.organization is not None:
            entity_ids.insert(0, self.organization.id)
        _assert_unique(entity_ids, "entity ids")
        entity_id_set = set(entity_ids)

        fact_ids = [fact.id for fact in self.facts]
        _assert_unique(fact_ids, "fact ids")
        customer_profile_ids = [profile.id for profile in self.customer_profiles]
        _assert_unique(customer_profile_ids, "customer profile ids")
        _assert_unique((process.id for process in self.processes), "process ids")
        _assert_unique((policy.id for policy in self.policies), "policy ids")
        _assert_unique((module.id for module in self.modules), "module ids")

        # A builder may collect fields in any order, so a DRAFT may have a
        # deliberately incomplete graph.  Ready-for-review and approved
        # configs are closed world documents: every reference must resolve.
        if self.lifecycle != ConfigurationLifecycle.DRAFT:
            for offering in self.offerings:
                if offering.provider_id not in entity_id_set:
                    raise ValueError(f"offering '{offering.id}' has unknown provider_id")
                if offering.overview_fact_id is not None:
                    overview_fact = next(
                        (fact for fact in self.facts if fact.id == offering.overview_fact_id),
                        None,
                    )
                    if overview_fact is None:
                        raise ValueError(
                            f"offering '{offering.id}' refers to an unknown overview_fact_id"
                        )
                    if overview_fact.subject_id != offering.id:
                        raise ValueError(
                            f"offering '{offering.id}' overview fact must have the same subject"
                        )
                    if not overview_fact.customer_visible or not overview_fact.customer_text:
                        raise ValueError(
                            f"offering '{offering.id}' overview fact must be customer-visible"
                        )
            if self.whatsapp_presentation is not None:
                offering_ids = {offering.id for offering in self.offerings}
                if any(
                    offering_id not in offering_ids
                    for offering_id in self.whatsapp_presentation.offering_media
                ):
                    raise ValueError("offering media must refer to a known offering")
            for relationship in self.relationships:
                if (
                    relationship.subject_id not in entity_id_set
                    or relationship.object_id not in entity_id_set
                ):
                    raise ValueError("relationships must refer to known entity ids")
            for fact in self.facts:
                if fact.subject_id not in entity_id_set:
                    raise ValueError(f"fact '{fact.id}' has unknown subject_id")
            if self.agent is not None:
                visible_fact_by_id = {
                    fact.id: fact
                    for fact in self.facts
                    if fact.customer_visible and fact.customer_text
                }
                for fact_id in self.agent.semantic_fallback_fact_ids:
                    fallback_fact = visible_fact_by_id.get(fact_id)
                    if fallback_fact is None:
                        raise ValueError(
                            "semantic fallback fact ids must refer to customer-visible facts"
                        )
                    if (
                        fallback_fact.category != FactCategory.SOCIAL
                        or not fallback_fact.selection_guidance
                    ):
                        raise ValueError(
                            "semantic fallback facts must be social and define selection_guidance"
                        )
                for fact_id in self.agent.starter_trigger_fact_ids:
                    if fact_id not in visible_fact_by_id:
                        raise ValueError(
                            "starter trigger fact ids must refer to customer-visible facts"
                        )
                for action in self.agent.starter_actions:
                    if action.fact_id not in visible_fact_by_id:
                        raise ValueError("starter actions must refer to customer-visible facts")
            for policy in self.policies:
                if policy.template_fact_id is not None and policy.template_fact_id not in fact_ids:
                    raise ValueError(f"policy '{policy.id}' refers to an unknown template_fact_id")

            customer_profile_id_set = set(customer_profile_ids)
            for party in self.parties:
                if any(
                    profile_id not in customer_profile_id_set for profile_id in party.profile_ids
                ):
                    raise ValueError(f"party '{party.id}' refers to an unknown customer profile")

        if self.lifecycle == ConfigurationLifecycle.APPROVED:
            errors = self.publishability_errors()
            if errors:
                raise ValueError("approved configuration is not publishable: " + "; ".join(errors))
        return self

    @classmethod
    def empty(cls) -> CompanyAgentConfig:
        """A valid persisted envelope for a newly created agent draft."""

        return cls()

    def publishability_errors(self) -> list[str]:
        """Return only the minimum requirements for a customer-facing runtime.

        Offerings, prices, customer profiles, processes, and policies are not
        universal requirements.  They become required only when a future
        enabled capability uses them.  This method deliberately checks the
        smaller, invariant bot contract instead.
        """

        errors: list[str] = []
        if self.lifecycle != ConfigurationLifecycle.APPROVED:
            errors.append("lifecycle must be approved")
        if self.organization is None:
            errors.append("organization is required")
        if self.agent is None:
            errors.append("agent policy is required")
            return errors

        if not self.agent.require_fact_ids_for_claims:
            errors.append("agent must require fact ids for customer claims")
        if self.agent.handoff_fact_id is not None:
            handoff_fact = next(
                (fact for fact in self.facts if fact.id == self.agent.handoff_fact_id),
                None,
            )
            if handoff_fact is None:
                errors.append("agent handoff_fact_id must refer to a known fact")
            elif not handoff_fact.customer_visible or not handoff_fact.customer_text:
                errors.append("agent handoff_fact_id must be customer-visible text")
        if self.organization and self.agent.default_locale not in self.organization.display_names:
            errors.append("organization needs a display name in the default locale")
        return errors

    def is_publishable(self) -> bool:
        return not self.publishability_errors()


def empty_company_agent_config() -> dict[str, JsonValue]:
    """JSONB-safe ORM default; returns a fresh object on every invocation."""

    return CompanyAgentConfig.empty().model_dump(mode="json")


def _assert_unique(values: Iterable[str], label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{label} must be unique")
