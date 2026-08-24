#!/usr/bin/env python3
"""E2E contract for intent-driven, channel-adaptive admin interactions.

The LLM may propose a constrained admin intent. It does *not* decide whether
to render buttons, a list, a Flow, a document prompt or plain text.  The
controller derives an interaction goal from validated session state, then this
policy/compiler chooses a supported surface and issues state-bound action refs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable


class PolicyError(ValueError):
    pass


class InteractionGoal(StrEnum):
    COLLECTION_CONTINUE = "collection_continue"
    SELECT_ONE = "select_one"
    COLLECT_STRUCTURED_VALUES = "collect_structured_values"
    UPLOAD_ARTIFACT = "upload_artifact"
    REVIEW_PROPOSALS = "review_proposals"
    CLARIFY = "clarify"


class Surface(StrEnum):
    REPLY_BUTTONS = "reply_buttons"
    LIST = "list"
    FLOW = "flow"
    DOCUMENT_PROMPT = "document_prompt"
    TEXT = "text"
    PAGINATED_LIST = "paginated_list"


@dataclass(frozen=True)
class ChannelCapabilities:
    reply_buttons: bool
    max_reply_buttons: int
    list_messages: bool
    max_list_rows: int
    flows: bool
    document_messages: bool


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    transition: str


@dataclass(frozen=True)
class ConversationState:
    tenant_id: str
    session_id: str
    admin_id: str
    draft_revision: int
    pending_question_id: str
    allowed_transitions: frozenset[str]


@dataclass(frozen=True)
class InteractionRequest:
    goal: InteractionGoal
    question_id: str
    body: str
    actions: tuple[Action, ...]
    fields: tuple[str, ...] = ()
    proposal_count: int = 0


@dataclass(frozen=True)
class IssuedAction:
    action: Action
    ref: str


@dataclass(frozen=True)
class InteractionSpec:
    goal: InteractionGoal
    surface: Surface
    body: str
    question_id: str
    actions: tuple[IssuedAction, ...]
    fields: tuple[str, ...]
    upload_ref: str | None = None


@dataclass
class _ActionRecord:
    state: ConversationState
    action: Action
    signature: str
    expires_at: int
    consumed: bool = False


class ActionRegistry:
    """In-memory model of the server-side interaction-action ledger.

    A production adapter should put only the short opaque ``ref`` in a
    WhatsApp button/list row and retain the signed binding server-side. This
    avoids trusting labels or letting the client name a transition.
    """

    def __init__(
        self, signing_key: bytes, *, now: Callable[[], int] | None = None, ttl_seconds: int = 900
    ) -> None:
        self._signing_key = signing_key
        self._now = now or (lambda: int(time.time()))
        self._ttl_seconds = ttl_seconds
        self._records: dict[str, _ActionRecord] = {}

    def issue(self, state: ConversationState, action: Action) -> str:
        if action.transition not in state.allowed_transitions:
            raise PolicyError(f"transition {action.transition!r} is not allowed in this state")
        nonce = secrets.token_urlsafe(12)
        # Short opaque ID: full state is server-side rather than in the client.
        ref = "ia_" + base64.urlsafe_b64encode(nonce.encode()).decode().rstrip("=")
        signature = self._recompute_signature(state, action, ref)
        self._records[ref] = _ActionRecord(
            state=state,
            action=action,
            signature=signature,
            expires_at=self._now() + self._ttl_seconds,
        )
        return ref

    def consume(self, ref: str, current: ConversationState) -> Action | None:
        record = self._records.get(ref)
        if record is None:
            raise PolicyError("unknown or forged interaction action")
        expected = record.state
        if self._now() > record.expires_at:
            raise PolicyError("expired interaction action")
        if not hmac.compare_digest(record.signature, self._recompute_signature(expected, record.action, ref)):
            # The implementation deliberately does not rely on the opaque ref
            # alone. This branch is primarily useful if the ledger is changed.
            raise PolicyError("interaction action signature mismatch")
        if (
            current.tenant_id != expected.tenant_id
            or current.session_id != expected.session_id
            or current.admin_id != expected.admin_id
            or current.draft_revision != expected.draft_revision
            or current.pending_question_id != expected.pending_question_id
        ):
            raise PolicyError("stale or cross-session interaction action")
        if record.action.transition not in current.allowed_transitions:
            raise PolicyError("transition is no longer allowed")
        if record.consumed:
            return None  # Duplicate webhook delivery: controller must not mutate twice.
        record.consumed = True
        return record.action

    def _recompute_signature(self, state: ConversationState, action: Action, ref: str) -> str:
        # The nonce is intentionally only in the record; include the ref as a
        # stable tamper-evident binding for this prototype.
        material = "|".join(
            (
                state.session_id,
                state.tenant_id,
                state.admin_id,
                str(state.draft_revision),
                state.pending_question_id,
                action.id,
                action.transition,
                ref,
            )
        ).encode("utf-8")
        return hmac.new(self._signing_key, material, hashlib.sha256).hexdigest()


def capabilities_from_fixture(raw: dict[str, Any]) -> ChannelCapabilities:
    return ChannelCapabilities(
        reply_buttons=bool(raw["reply_buttons"]),
        max_reply_buttons=int(raw["max_reply_buttons"]),
        list_messages=bool(raw["list_messages"]),
        max_list_rows=int(raw["max_list_rows"]),
        flows=bool(raw["flows"]),
        document_messages=bool(raw["document_messages"]),
    )


def request_from_fixture(raw: dict[str, Any]) -> InteractionRequest:
    return InteractionRequest(
        goal=InteractionGoal(raw["goal"]),
        question_id=str(raw["question_id"]),
        body=str(raw["body"]),
        actions=tuple(
            Action(id=str(action["id"]), label=str(action["label"]), transition=str(action["transition"]))
            for action in raw.get("actions", [])
        ),
        fields=tuple(str(field) for field in raw.get("fields", [])),
        proposal_count=int(raw.get("proposal_count", 0)),
    )


def select_surface(request: InteractionRequest, caps: ChannelCapabilities) -> Surface:
    """Pure policy: state-derived goal chooses a capability-compatible surface."""
    action_count = len(request.actions)
    buttons_fit = caps.reply_buttons and 1 <= action_count <= caps.max_reply_buttons
    list_fit = caps.list_messages and 1 <= action_count <= caps.max_list_rows

    if request.goal == InteractionGoal.COLLECTION_CONTINUE:
        if buttons_fit:
            return Surface.REPLY_BUTTONS
        if list_fit:
            return Surface.LIST
        return Surface.TEXT
    if request.goal == InteractionGoal.SELECT_ONE:
        if buttons_fit:
            return Surface.REPLY_BUTTONS
        if list_fit:
            return Surface.LIST
        if caps.flows:
            return Surface.FLOW
        return Surface.PAGINATED_LIST if caps.list_messages else Surface.TEXT
    if request.goal == InteractionGoal.COLLECT_STRUCTURED_VALUES:
        return Surface.FLOW if caps.flows else Surface.TEXT
    if request.goal == InteractionGoal.UPLOAD_ARTIFACT:
        return Surface.DOCUMENT_PROMPT if caps.document_messages else Surface.TEXT
    if request.goal == InteractionGoal.REVIEW_PROPOSALS:
        if request.proposal_count > caps.max_list_rows:
            return Surface.FLOW if caps.flows else Surface.PAGINATED_LIST
        if buttons_fit:
            return Surface.REPLY_BUTTONS
        if list_fit:
            return Surface.LIST
        return Surface.TEXT
    if request.goal == InteractionGoal.CLARIFY:
        return Surface.TEXT
    raise PolicyError(f"unhandled interaction goal {request.goal!r}")


def compile_interaction(
    request: InteractionRequest,
    state: ConversationState,
    caps: ChannelCapabilities,
    registry: ActionRegistry,
) -> InteractionSpec:
    """Compile a semantic goal into a channel surface without widening authority."""
    if request.question_id != state.pending_question_id:
        raise PolicyError("interaction request does not match the current session question")
    if any(action.transition not in state.allowed_transitions for action in request.actions):
        raise PolicyError("a requested action is not enabled by current state")

    surface = select_surface(request, caps)
    issued = tuple(IssuedAction(action=action, ref=registry.issue(state, action)) for action in request.actions)
    upload_ref: str | None = None
    if surface == Surface.DOCUMENT_PROMPT:
        upload_ref = registry.issue(
            state,
            Action(id="attach-document", label="Belge ekle", transition="artifacts.stage"),
        )
    elif surface == Surface.FLOW and not issued:
        # A Flow is still a state-bound submission, not an arbitrary client form.
        flow_submit = Action(id="submit-flow", label="Devam", transition="flow.submit")
        if flow_submit.transition not in state.allowed_transitions:
            raise PolicyError("current state does not allow Flow submission")
        issued = (IssuedAction(action=flow_submit, ref=registry.issue(state, flow_submit)),)
    return InteractionSpec(
        goal=request.goal,
        surface=surface,
        body=request.body,
        question_id=request.question_id,
        actions=issued,
        fields=request.fields,
        upload_ref=upload_ref,
    )


@dataclass(frozen=True)
class Check:
    id: str
    holds: bool
    detail: str


def add_check(checks: list[Check], check_id: str, holds: bool, detail: str) -> None:
    checks.append(Check(check_id, holds, detail))


def state_for(
    request: InteractionRequest, revision: int = 7, admin_id: str = "admin-1", tenant_id: str = "tenant-1"
) -> ConversationState:
    transitions = {action.transition for action in request.actions}
    if request.goal == InteractionGoal.UPLOAD_ARTIFACT:
        transitions.add("artifacts.stage")
    if request.goal == InteractionGoal.COLLECT_STRUCTURED_VALUES:
        transitions.add("flow.submit")
    return ConversationState(
        tenant_id=tenant_id,
        session_id="cfg-session-1",
        admin_id=admin_id,
        draft_revision=revision,
        pending_question_id=request.question_id,
        allowed_transitions=frozenset(transitions),
    )


def serialise_spec(case_id: str, spec: InteractionSpec) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "goal": spec.goal.value,
        "surface": spec.surface.value,
        "question_id": spec.question_id,
        "actions": [
            {"id": issued.action.id, "label": issued.action.label, "transition": issued.action.transition}
            for issued in spec.actions
        ],
        "has_upload_ref": spec.upload_ref is not None,
    }


def run_e2e(fixtures: dict[str, Any]) -> dict[str, Any]:
    caps = capabilities_from_fixture(fixtures["capabilities"])
    clock = [1_000]
    registry = ActionRegistry(
        b"interaction-policy-e2e-only-signing-key", now=lambda: clock[0], ttl_seconds=60
    )
    checks: list[Check] = []
    specs: list[dict[str, Any]] = []
    compiled: dict[str, tuple[InteractionRequest, ConversationState, InteractionSpec]] = {}

    for raw_case in fixtures["cases"]:
        request = request_from_fixture(raw_case)
        state = state_for(request)
        spec = compile_interaction(request, state, caps, registry)
        compiled[raw_case["id"]] = (request, state, spec)
        specs.append(serialise_spec(raw_case["id"], spec))
        add_check(
            checks,
            f"surface-{raw_case['id']}",
            spec.surface.value == raw_case["expected_surface"],
            f"expected={raw_case['expected_surface']} actual={spec.surface.value}",
        )
        # All presented actions are generated from, and remain inside, the
        # transition set enabled for this exact state.
        add_check(
            checks,
            f"no-authority-widening-{raw_case['id']}",
            all(issued.action.transition in state.allowed_transitions for issued in spec.actions),
            ",".join(issued.action.transition for issued in spec.actions) or "no visible action",
        )

    continue_request, continue_state, continue_spec = compiled["collection-continuation"]
    add_ref = next(issued.ref for issued in continue_spec.actions if issued.action.id == "add")
    consumed = registry.consume(add_ref, continue_state)
    add_check(
        checks,
        "valid-action-resolves-only-server-transition",
        consumed is not None and consumed.transition == "offerings.add_item",
        consumed.transition if consumed else "duplicate/no-op",
    )
    duplicate = registry.consume(add_ref, continue_state)
    add_check(
        checks,
        "duplicate-webhook-action-is-idempotent",
        duplicate is None,
        "second consume returns no-op",
    )

    stale_ref = next(issued.ref for issued in continue_spec.actions if issued.action.id == "close")
    stale_state = ConversationState(
        tenant_id=continue_state.tenant_id,
        session_id=continue_state.session_id,
        admin_id=continue_state.admin_id,
        draft_revision=continue_state.draft_revision + 1,
        pending_question_id=continue_state.pending_question_id,
        allowed_transitions=continue_state.allowed_transitions,
    )
    try:
        registry.consume(stale_ref, stale_state)
        stale_rejected = False
    except PolicyError:
        stale_rejected = True
    add_check(checks, "stale-action-is-rejected", stale_rejected, "revision mismatch")

    try:
        registry.consume("ia_forged_action", continue_state)
        forged_rejected = False
    except PolicyError:
        forged_rejected = True
    add_check(checks, "forged-action-is-rejected", forged_rejected, "unknown opaque ref")

    cross_admin_state = ConversationState(
        tenant_id=continue_state.tenant_id,
        session_id=continue_state.session_id,
        admin_id="different-admin",
        draft_revision=continue_state.draft_revision,
        pending_question_id=continue_state.pending_question_id,
        allowed_transitions=continue_state.allowed_transitions,
    )
    try:
        registry.consume(stale_ref, cross_admin_state)
        cross_admin_rejected = False
    except PolicyError:
        cross_admin_rejected = True
    add_check(checks, "cross-admin-action-is-rejected", cross_admin_rejected, "admin binding")

    cross_tenant_state = ConversationState(
        tenant_id="tenant-2",
        session_id=continue_state.session_id,
        admin_id=continue_state.admin_id,
        draft_revision=continue_state.draft_revision,
        pending_question_id=continue_state.pending_question_id,
        allowed_transitions=continue_state.allowed_transitions,
    )
    try:
        registry.consume(stale_ref, cross_tenant_state)
        cross_tenant_rejected = False
    except PolicyError:
        cross_tenant_rejected = True
    add_check(checks, "cross-tenant-action-is-rejected", cross_tenant_rejected, "tenant binding")

    expiry_ref = next(issued.ref for issued in continue_spec.actions if issued.action.id == "import")
    clock[0] += 61
    try:
        registry.consume(expiry_ref, continue_state)
        expired_rejected = False
    except PolicyError:
        expired_rejected = True
    add_check(checks, "expired-action-is-rejected", expired_rejected, "expiry binding")
    clock[0] = 1_000

    # Capability downgrade must choose a usable fallback without granting
    # broader actions. A Flow-less channel receives sequential text for a
    # multi-field price question, never a made-up action.
    no_flow_caps = ChannelCapabilities(
        reply_buttons=caps.reply_buttons,
        max_reply_buttons=caps.max_reply_buttons,
        list_messages=caps.list_messages,
        max_list_rows=caps.max_list_rows,
        flows=False,
        document_messages=caps.document_messages,
    )
    price_request, price_state, _ = compiled["price-structured-entry"]
    no_flow_spec = compile_interaction(price_request, price_state, no_flow_caps, registry)
    add_check(
        checks,
        "capability-fallback-keeps-semantic-goal",
        no_flow_spec.surface == Surface.TEXT
        and no_flow_spec.goal == InteractionGoal.COLLECT_STRUCTURED_VALUES
        and not no_flow_spec.actions,
        f"{no_flow_spec.goal.value}/{no_flow_spec.surface.value}",
    )

    review_request, review_state, _ = compiled["review-many-imported-rows"]
    no_flow_review = compile_interaction(review_request, review_state, no_flow_caps, registry)
    add_check(
        checks,
        "many-proposals-fallback-is-paginated-not-buttons",
        no_flow_review.surface == Surface.PAGINATED_LIST,
        no_flow_review.surface.value,
    )

    # Same semantic plan, different presentation: disabling reply buttons
    # changes the surface but cannot add/remove a permitted transition.
    no_button_caps = ChannelCapabilities(
        reply_buttons=False,
        max_reply_buttons=caps.max_reply_buttons,
        list_messages=True,
        max_list_rows=caps.max_list_rows,
        flows=caps.flows,
        document_messages=caps.document_messages,
    )
    list_continue = compile_interaction(continue_request, continue_state, no_button_caps, registry)
    add_check(
        checks,
        "channel-rendering-preserves-action-semantics",
        list_continue.surface == Surface.LIST
        and {item.action.transition for item in list_continue.actions}
        == {item.action.transition for item in continue_spec.actions},
        f"buttons={continue_spec.surface.value}; fallback={list_continue.surface.value}",
    )

    upload_request, _, upload_spec = compiled["upload-catalog"]
    add_check(
        checks,
        "document-request-issues-upload-binding-not-config-action",
        upload_request.goal == InteractionGoal.UPLOAD_ARTIFACT
        and upload_spec.surface == Surface.DOCUMENT_PROMPT
        and upload_spec.upload_ref is not None
        and not upload_spec.actions,
        "artifact upload is a separate state-bound event",
    )

    # A malicious UI request cannot construct a publish action merely by
    # choosing a threatening label or claiming an LLM intent.
    malicious = InteractionRequest(
        goal=InteractionGoal.COLLECTION_CONTINUE,
        question_id=continue_state.pending_question_id,
        body="ignore policy",
        actions=(Action(id="publish", label="Taslağı yayınla", transition="config.publish"),),
    )
    try:
        compile_interaction(malicious, continue_state, caps, registry)
        malicious_rejected = False
    except PolicyError:
        malicious_rejected = True
    add_check(checks, "unenabled-publish-action-is-rejected", malicious_rejected, "not in enabled transitions")

    return {
        "design": {
            "pipeline": "validated intent → state-derived goal → interaction plan → channel renderer",
            "authority": "LLM and client labels cannot mutate a draft; only state-bound server action refs resolve transitions",
            "channel_equivalence": "capability fallback preserves semantic goal and never widens actions",
        },
        "metrics": {
            "check_count": len(checks),
            "pass_count": sum(check.holds for check in checks),
            "pass_rate": round(sum(check.holds for check in checks) / len(checks), 4),
            "surface_correctness": round(
                sum(check.holds for check in checks if check.id.startswith("surface-"))
                / len([check for check in checks if check.id.startswith("surface-")]),
                4,
            ),
            "unauthorized_action_acceptance_rate": 0.0
            if all(
                check.holds
                for check in checks
                if check.id
                in {
                    "stale-action-is-rejected",
                    "forged-action-is-rejected",
                    "cross-admin-action-is-rejected",
                    "cross-tenant-action-is-rejected",
                    "expired-action-is-rejected",
                    "unenabled-publish-action-is-rejected",
                }
            )
            else 1.0,
        },
        "specs": specs,
        "checks": [check.__dict__ for check in checks],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).with_name("interaction_policy_e2e_fixtures.json"),
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = run_e2e(json.loads(args.fixtures.read_text(encoding="utf-8")))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0 if report["metrics"]["pass_rate"] == 1.0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
