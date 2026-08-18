"""Customer Bot Runtime Engine — Deterministic FSM & Guardrails Engine.

Executes end-customer (B2B lead) interactions over WhatsApp using a strict
Finite State Machine (FSM) + Guardrails + Structured Tool Evaluation.

Guarantees:
  1. No hallucinations: Fact answers are constrained by product_knowledge.
  2. Escalation safety: Forbidden topics and escalation triggers immediately
     transition the conversation to ESCALATED_HUMAN and trigger human handover.
  3. Structured state progression: Step-by-step qualification question tracking.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Literal
from dataclasses import dataclass, field
from pydantic import BaseModel, Field


class CustomerBotState(str, Enum):
    GREETING = "GREETING"
    QUALIFYING = "QUALIFYING"
    KNOWLEDGE_FAQ = "KNOWLEDGE_FAQ"
    OFFER_REQUESTED = "OFFER_REQUESTED"
    ESCALATED_HUMAN = "ESCALATED_HUMAN"


class CustomerBotEvaluationResult(BaseModel):
    state: CustomerBotState
    action_type: Literal["ASK_QUALIFICATION", "ANSWER_KNOWLEDGE", "ESCALATE_TO_HUMAN", "THANK_AND_OFFER"]
    reply_text: str
    extracted_lead_data: dict[str, Any] = Field(default_factory=dict)
    escalated: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class AgentRuntimeConfig:
    persona: str
    tone: str
    languages: list[str]
    product_knowledge: str
    qualification_questions: list[str]
    forbidden_topics: list[str]
    escalation_triggers: list[str]


class CustomerBotRuntimeEngine:
    """Evaluates inbound WhatsApp customer messages deterministically against
    the agent's version configuration and current conversation state."""

    def __init__(self, config: AgentRuntimeConfig) -> None:
        self.config = config

    def check_guardrails(self, message_text: str) -> tuple[bool, str | None]:
        """Checks inbound text against forbidden topics and escalation triggers.
        Returns (should_escalate, reason)."""
        text_lower = message_text.lower()

        for trigger in self.config.escalation_triggers:
            if trigger.strip() and trigger.lower() in text_lower:
                return True, f"Matched escalation trigger: '{trigger}'"

        for topic in self.config.forbidden_topics:
            if topic.strip() and topic.lower() in text_lower:
                return True, f"Matched forbidden topic: '{topic}'"

        return False, None

    def evaluate_turn(
        self,
        user_message: str,
        current_state: CustomerBotState = CustomerBotState.GREETING,
        answered_questions: dict[str, str] | None = None,
    ) -> CustomerBotEvaluationResult:
        answered = answered_questions or {}

        # 1. Guardrail Inspection (Pre-eval)
        should_escalate, reason = self.check_guardrails(user_message)
        if should_escalate:
            return CustomerBotEvaluationResult(
                state=CustomerBotState.ESCALATED_HUMAN,
                action_type="ESCALATE_TO_HUMAN",
                reply_text=(
                    "Talebinizi yetkili satış temsilcimize aktarıyorum. "
                    "En kısa sürede sizinle iletişime geçeceğiz."
                ),
                extracted_lead_data=answered,
                escalated=True,
                reason=reason,
            )

        # 2. State Machine Transition Logic
        if current_state == CustomerBotState.GREETING:
            # Check if there are qualification questions to ask
            if self.config.qualification_questions:
                first_q = self.config.qualification_questions[0]
                return CustomerBotEvaluationResult(
                    state=CustomerBotState.QUALIFYING,
                    action_type="ASK_QUALIFICATION",
                    reply_text=f"Merhaba! {self.config.persona or 'Satış Temsilciniz olarak size yardımcı olmaktan mutluluk duyarım.'}\n\nSize daha iyi hizmet verebilmemiz için: {first_q}",
                    extracted_lead_data=answered,
                )
            else:
                return CustomerBotEvaluationResult(
                    state=CustomerBotState.KNOWLEDGE_FAQ,
                    action_type="ANSWER_KNOWLEDGE",
                    reply_text=f"Merhaba! Size nasıl yardımcı olabilirim?\n\n{self.config.product_knowledge[:200]}...",
                    extracted_lead_data=answered,
                )

        elif current_state == CustomerBotState.QUALIFYING:
            # Save answer to current pending question
            questions = self.config.qualification_questions
            unanswered_idx = len(answered)

            if unanswered_idx < len(questions):
                current_q = questions[unanswered_idx]
                updated_answers = {**answered, current_q: user_message.strip()}

                # Check if there's a next qualification question
                if len(updated_answers) < len(questions):
                    next_q = questions[len(updated_answers)]
                    return CustomerBotEvaluationResult(
                        state=CustomerBotState.QUALIFYING,
                        action_type="ASK_QUALIFICATION",
                        reply_text=f"Teşekkürler! Bir sonraki sorumuz: {next_q}",
                        extracted_lead_data=updated_answers,
                    )
                else:
                    # All qualification questions answered!
                    return CustomerBotEvaluationResult(
                        state=CustomerBotState.OFFER_REQUESTED,
                        action_type="THANK_AND_OFFER",
                        reply_text=(
                            "Verdiğiniz bilgiler için teşekkür ederiz! "
                            "Talebiniz kaydedildi, satış ekibimiz size özel teklif hazırlayıp dönüş yapacaktır. "
                            "Başka bir sorunuz var mıydı?"
                        ),
                        extracted_lead_data=updated_answers,
                    )

        # Default / Fallback Knowledge FAQ state
        return CustomerBotEvaluationResult(
            state=CustomerBotState.KNOWLEDGE_FAQ,
            action_type="ANSWER_KNOWLEDGE",
            reply_text=f"Ürünlerimiz hakkında bilgi: {self.config.product_knowledge[:300]}",
            extracted_lead_data=answered,
        )
