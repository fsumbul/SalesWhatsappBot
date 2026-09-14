"""Natural customer replies grounded in the published company graph."""

import asyncio
from dataclasses import replace
from typing import Any

from src.integrations.llm import LLMMessage
from src.modules.conversation_language import respond

from .company_runtime import (
    CustomerReplyAction,
    RuntimeTurn,
    _fact_projection,
    _localized_text,
    _suggest_interaction,
)
from .semantic_dialogue import interpret_requests, request_candidates


async def reply_naturally(
    config: Any, llm: Any, message: str, *, history: list[LLMMessage],
    context_fact_ids: tuple[str, ...], capabilities: Any = None,
    intake: dict[str, Any] | None = None,
) -> RuntimeTurn:
    try:
        plan = await asyncio.wait_for(
            interpret_requests(config, llm, message, history, context_fact_ids), timeout=55,
        )
        evidence: list[dict[str, Any]] = [{
            "id": "company_identity",
            "name": _localized_text(config.organization.display_names, config.agent.default_locale)
                    if config.organization else "Şirket asistanı",
        }]
        selected = {}
        requests = []
        for request in plan.requests:
            candidates = [] if request.topic in {"general", "social"} else request_candidates(config, request)
            candidates = [f for f in candidates if f.category.value != "social"]
            for fact in candidates:
                selected[fact.id] = fact
            requests.append({**request.model_dump(), "available_fact_ids": [f.id for f in candidates]})
        evidence.extend({"id": f.id, "subject_id": f.subject_id, "category": f.category.value,
                         "text": _fact_projection(config, f)["text"]} for f in selected.values())
        if intake:
            evidence.insert(1, {"id": "intake", **intake})
        generated = await respond(
            llm, message, history=history, evidence=evidence,
            context={"channel": "whatsapp_customer", "requests": requests,
                     "unknown_business_fact_behavior": "Explain what cannot be verified; continue conversation.",
                     "intake": bool(intake)},
            max_characters=min(config.agent.max_characters, 1024),
        )
        fact_ids = tuple(f for f in generated.evidence_ids if f in selected)
        turn = RuntimeTurn(
            action=CustomerReplyAction.HANDOFF if generated.handoff_requested else CustomerReplyAction.REPLY,
            reply=generated.text, fact_ids=fact_ids, used_fallback=not generated.verified,
            response_source="model" if generated.verified else "fallback",
            fallback_reason=generated.reason, answer_origin=generated.source,
            answer_verified=generated.verified,
            answer_evidence_ids=generated.evidence_ids,
            intake_requested=not intake and generated.verified and any(r.topic == "quote" for r in plan.requests),
            request_resolutions=tuple({"subject_id": r.subject_id, "topic": r.topic,
                                      "status": "verified" if generated.verified else "unverified",
                                      "fact_ids": [f for f in fact_ids if selected[f].subject_id == r.subject_id]}
                                     for r in plan.requests),
        )
        return replace(turn, interaction=_suggest_interaction(config, turn, message, capabilities))
    except Exception as exc:
        # Availability is not an instruction to pause the customer's conversation.
        return RuntimeTurn(
            CustomerReplyAction.REPLY, "", (), used_fallback=True,
            response_source="fallback", fallback_reason=type(exc).__name__, answer_origin="model_unavailable",
        )


async def describe_navigation(config: Any, llm: Any, message: str, turn: RuntimeTurn,
                              history: list[LLMMessage]) -> RuntimeTurn:
    """Navigation selects data/UI; the language model still writes every reply."""
    facts = {f.id: f for f in config.facts if f.customer_visible and f.customer_text}
    evidence = [{"id": f, **_fact_projection(config, facts[f])} for f in turn.fact_ids if f in facts]
    evidence.append({"id": "navigation", "result": turn.reply,
                     "interaction": turn.interaction.kind.value if turn.interaction else None})
    generated = await respond(llm, message, history=history, evidence=evidence,
                              context={"channel": "whatsapp_customer", "navigation_selected": True},
                              max_characters=min(config.agent.max_characters, 1024))
    return replace(turn, reply=generated.text, action=CustomerReplyAction.REPLY,
                   used_fallback=not generated.verified, response_source="model" if generated.verified else "fallback",
                   answer_origin=generated.source, answer_verified=generated.verified,
                   answer_evidence_ids=generated.evidence_ids,
                   fallback_reason=generated.reason)
