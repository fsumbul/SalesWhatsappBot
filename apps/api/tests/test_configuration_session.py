"""Tests for the constructive, staged universal configuration flow."""

from __future__ import annotations

import pytest

from src.modules.agents.company_config import AgentReplyPolicy, ConfigurationLifecycle, Organization
from src.modules.agents.configuration_session import (
    AddFactCommand,
    AddOfferingCommand,
    AddPartyCommand,
    BindFactSubjectsCommand,
    BindOfferingProvidersCommand,
    BindPartyProfilesCommand,
    BindPolicyTemplatesCommand,
    ConfigurationFlowSession,
    ConfigurationSessionError,
    DraftKind,
    FactCore,
    FactSubjectBinding,
    MaterializationError,
    OfferingCore,
    OfferingProviderBinding,
    PartyCore,
    PartyProfileBinding,
    SetAgentPolicyCommand,
    SetLifecycleCommand,
    SetOrganizationCommand,
    typed_reducer_coverage_errors,
)


TENANT = "tenant-a"
ADMIN = "admin-a"


def _session() -> ConfigurationFlowSession:
    return ConfigurationFlowSession(
        tenant_id=TENANT,
        admin_id=ADMIN,
        session_id="configuration-session-a",
        signing_key=b"configuration-session-test-key",
    )


def _accept(session: ConfigurationFlowSession, command: object) -> None:
    action = session.issue_command(type(command), tenant_id=TENANT, admin_id=ADMIN)  # type: ignore[arg-type]
    preview = session.propose(action, command, tenant_id=TENANT, admin_id=ADMIN)  # type: ignore[arg-type]
    assert preview is not None
    accept = session.issue_accept(preview.id, tenant_id=TENANT, admin_id=ADMIN)
    assert session.accept(accept, tenant_id=TENANT, admin_id=ADMIN)


def _close(session: ConfigurationFlowSession) -> None:
    action = session.issue_close_collection(tenant_id=TENANT, admin_id=ADMIN)
    assert session.close_collection(action, tenant_id=TENANT, admin_id=ADMIN)


def _defer(session: ConfigurationFlowSession) -> None:
    action = session.issue_defer(tenant_id=TENANT, admin_id=ADMIN)
    assert session.defer(action, tenant_id=TENANT, admin_id=ADMIN)


def test_every_core_flow_spec_has_a_typed_reducer() -> None:
    assert typed_reducer_coverage_errors() == ()


def test_preview_is_not_a_mutation_and_duplicate_accept_is_idempotent() -> None:
    session = _session()
    command = SetOrganizationCommand(
        organization=Organization(id="market", display_names={"en-GB": "Mosaic Market"})
    )
    action = session.issue_command(SetOrganizationCommand, tenant_id=TENANT, admin_id=ADMIN)
    preview = session.propose(action, command, tenant_id=TENANT, admin_id=ADMIN)

    assert preview is not None
    assert session.staged.organization is None

    accept = session.issue_accept(preview.id, tenant_id=TENANT, admin_id=ADMIN)
    assert session.accept(accept, tenant_id=TENANT, admin_id=ADMIN)
    assert session.staged.organization is not None
    # An at-least-once webhook delivery cannot reapply the accepted change.
    assert not session.accept(accept, tenant_id=TENANT, admin_id=ADMIN)


def test_expired_action_cannot_create_a_proposal() -> None:
    clock = [1_000]
    session = ConfigurationFlowSession(
        tenant_id=TENANT,
        admin_id=ADMIN,
        session_id="expiry-session",
        signing_key=b"configuration-session-test-key",
        now=lambda: clock[0],
        action_ttl_seconds=10,
    )
    action = session.issue_command(SetOrganizationCommand, tenant_id=TENANT, admin_id=ADMIN)
    clock[0] += 11

    with pytest.raises(ConfigurationSessionError, match="expired"):
        session.propose(
            action,
            SetOrganizationCommand(
                organization=Organization(id="market", display_names={"en-GB": "Mosaic Market"})
            ),
            tenant_id=TENANT,
            admin_id=ADMIN,
        )


def test_open_collection_blocks_a_distant_flow_step_and_stale_action() -> None:
    session = _session()
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

    assert session.current_question_id() == "parties"
    with pytest.raises(ConfigurationSessionError, match="not enabled"):
        session.issue_command(AddOfferingCommand, tenant_id=TENANT, admin_id=ADMIN)

    close = session.issue_close_collection(tenant_id=TENANT, admin_id=ADMIN)
    # A mismatched admin cannot consume an otherwise valid opaque action ref.
    with pytest.raises(ConfigurationSessionError):
        session.close_collection(close, tenant_id=TENANT, admin_id="another-admin")
    assert session.close_collection(close, tenant_id=TENANT, admin_id=ADMIN)


def test_unbound_reference_cannot_materialize_and_wrong_ref_kind_is_rejected() -> None:
    session = _session()
    _accept(
        session,
        SetOrganizationCommand(
            organization=Organization(id="market", display_names={"en-GB": "Mosaic Market"})
        ),
    )
    _close(session)  # no parties
    _accept(
        session,
        AddOfferingCommand(
            offering=OfferingCore(
                id="linen-set", kind="marketplace_listing", display_names={"en-GB": "Linen set"}
            )
        ),
    )
    _close(session)
    _close(session)  # no profiles
    _defer(session)  # no nested profile fields
    _accept(session, BindPartyProfilesCommand(bindings=[]))

    # A fact ref is not an entity ref and cannot be smuggled into provider binding.
    _accept(
        session,
        BindOfferingProvidersCommand(
            bindings=[
                OfferingProviderBinding(
                    offering=session.ref_for(DraftKind.OFFERING, "linen-set"),
                    provider=session.ref_for(DraftKind.ORGANIZATION, "market"),
                )
            ]
        ),
    )
    _close(session)  # relationships
    _accept(
        session,
        # The fact is deliberately incomplete until the later binding flow.
        # Staging accepts it, durable materialization does not.
        AddFactCommand(
            fact=FactCore(id="availability", category="availability", value={"in_stock": True}, source="admin")
        ),
    )
    _close(session)

    with pytest.raises(ConfigurationSessionError, match="reference must point"):
        _accept(
            session,
            BindFactSubjectsCommand(
                bindings=[
                    FactSubjectBinding(
                        fact=session.ref_for(DraftKind.FACT, "availability"),
                        subject=session.ref_for(DraftKind.FACT, "availability"),
                    )
                ]
            ),
        )

    with pytest.raises(MaterializationError, match="flow step"):
        session.materialize()


def test_marketplace_trace_constructs_a_publishable_company_graph() -> None:
    session = _session()
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
    _close(session)
    _defer(session)
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
    _close(session)
    _accept(
        session,
        AddFactCommand(
            fact=FactCore(
                id="availability",
                category="availability",
                value={"in_stock": True},
                source="admin",
                customer_visible=True,
                customer_text={"en-GB": "The linen set is in stock."},
            )
        ),
    )
    _close(session)
    _accept(
        session,
        BindFactSubjectsCommand(
            bindings=[
                FactSubjectBinding(
                    fact=session.ref_for(DraftKind.FACT, "availability"),
                    subject=session.ref_for(DraftKind.OFFERING, "linen-set"),
                )
            ]
        ),
    )
    _close(session)
    _defer(session)
    _close(session)
    _defer(session)
    _accept(session, BindPolicyTemplatesCommand(bindings=[]))
    _accept(
        session,
        SetAgentPolicyCommand(
            agent=AgentReplyPolicy(
                purposes=["sales", "support"], supported_locales=["en-GB"], default_locale="en-GB"
            )
        ),
    )
    _close(session)
    _accept(session, SetLifecycleCommand(lifecycle=ConfigurationLifecycle.APPROVED))

    config = session.materialize()
    assert session.is_complete
    assert config.is_publishable()
    assert config.offerings[0].provider_id == "seller-ada"
    assert config.facts[0].subject_id == "linen-set"
