"""Map a guard verdict to the approved fallback turn (or to nothing)."""

from __future__ import annotations

from dataclasses import replace

from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import (
    RuntimeTurn,
    safe_decline_turn,
    safe_unknown_fact_turn,
)

from .ports import GuardDecision, GuardVerdict


def guardrail_blocked_turn(config: CompanyAgentConfig, verdict: GuardVerdict) -> RuntimeTurn | None:
    """``None`` means "proceed to the model"; otherwise the only reply the customer gets.

    * jailbreak / unsafe content → approved DECLINE text; the conversation is
      not paused, so an abusive message cannot switch the bot off;
    * off-topic (block mode) → the tenant's configured unknown-fact action;
    * classifiers unavailable in closed mode → same as a model outage.
    """

    if verdict.decision == GuardDecision.BLOCK:
        reason = f"guardrail:{verdict.reason or 'blocked'}"
        if (verdict.reason or "").startswith("topic_control"):
            turn = safe_unknown_fact_turn(config, reason=reason)
        else:
            turn = safe_decline_turn(config, reason=reason)
        return replace(turn, response_source="guardrail")
    if verdict.decision == GuardDecision.UNAVAILABLE:
        turn = safe_unknown_fact_turn(config, reason="guardrail:unavailable")
        return replace(turn, response_source="guardrail")
    return None
