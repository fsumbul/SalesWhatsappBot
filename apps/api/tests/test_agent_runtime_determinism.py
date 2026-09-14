"""Unit & determinism tests for CustomerBotRuntimeEngine FSM & Guardrails."""

import pytest

from src.modules.agents.runtime_fsm import (
    AgentRuntimeConfig,
    CustomerBotRuntimeEngine,
    CustomerBotState,
)


@pytest.fixture
def sample_config() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        persona="Asansör Kasnağı Satış Uzmanı",
        tone="samimi ve profesyonel",
        languages=["tr", "en"],
        product_knowledge="Firmamız 100mm ile 600mm arasında döküm asansör kasnakları üretmektedir. Minimum sipariş miktarımız 10 adettir.",
        qualification_questions=[
            "Hangi çapta kasnak arıyorsunuz?",
            "Kaç adet ihtiyacınız var?",
            "Teslimat hangi şehre yapılacak?",
        ],
        forbidden_topics=["fiyat pazarlığı", "rakip firmalar"],
        escalation_triggers=["şikayet", "yasal konu", "patronla görüşmek"],
    )


def test_guardrail_forbidden_topic_triggers_escalation(sample_config: AgentRuntimeConfig) -> None:
    engine = CustomerBotRuntimeEngine(sample_config)
    res = engine.evaluate_turn("Bana fiyat pazarlığı yapabilir misiniz?")

    assert res.escalated is True
    assert res.state == CustomerBotState.ESCALATED_HUMAN
    assert res.action_type == "ESCALATE_TO_HUMAN"
    assert "fiyat pazarlığı" in (res.reason or "")


def test_guardrail_escalation_trigger_word(sample_config: AgentRuntimeConfig) -> None:
    engine = CustomerBotRuntimeEngine(sample_config)
    res = engine.evaluate_turn("Gelen kasnaklarda çatlak var, şikayet etmek istiyorum.")

    assert res.escalated is True
    assert res.state == CustomerBotState.ESCALATED_HUMAN
    assert res.action_type == "ESCALATE_TO_HUMAN"
    assert "şikayet" in (res.reason or "")


def test_qualification_flow_step_by_step(sample_config: AgentRuntimeConfig) -> None:
    engine = CustomerBotRuntimeEngine(sample_config)

    # Turn 1: Greeting -> Asks Q1
    res1 = engine.evaluate_turn("Merhaba", current_state=CustomerBotState.GREETING)
    assert res1.state == CustomerBotState.QUALIFYING
    assert res1.action_type == "ASK_QUALIFICATION"
    assert "Hangi çapta kasnak arıyorsunuz?" in res1.reply_text

    # Turn 2: User answers Q1 -> Asks Q2
    res2 = engine.evaluate_turn(
        "300mm kasnak arıyorum",
        current_state=CustomerBotState.QUALIFYING,
        answered_questions=res1.extracted_lead_data,
    )
    assert res2.state == CustomerBotState.QUALIFYING
    assert "300mm kasnak arıyorum" in res2.extracted_lead_data["Hangi çapta kasnak arıyorsunuz?"]
    assert "Kaç adet ihtiyacınız var?" in res2.reply_text

    # Turn 3: User answers Q2 -> Asks Q3
    res3 = engine.evaluate_turn(
        "50 adet alacağız",
        current_state=CustomerBotState.QUALIFYING,
        answered_questions=res2.extracted_lead_data,
    )
    assert res3.state == CustomerBotState.QUALIFYING
    assert "50 adet alacağız" in res3.extracted_lead_data["Kaç adet ihtiyacınız var?"]
    assert "Teslimat hangi şehre yapılacak?" in res3.reply_text

    # Turn 4: User answers Q3 -> Qualification complete -> OFFER_REQUESTED
    res4 = engine.evaluate_turn(
        "İstanbul",
        current_state=CustomerBotState.QUALIFYING,
        answered_questions=res3.extracted_lead_data,
    )
    assert res4.state == CustomerBotState.OFFER_REQUESTED
    assert res4.action_type == "THANK_AND_OFFER"
    assert len(res4.extracted_lead_data) == 3
    assert res4.extracted_lead_data["Teslimat hangi şehre yapılacak?"] == "İstanbul"
