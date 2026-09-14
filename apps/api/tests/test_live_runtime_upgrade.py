"""Regression boundaries for the production semantic and company-wide rollout."""

import json
from pathlib import Path
from uuid import uuid4

from src.integrations.llm import LLMCompletionError
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import CompanyAgentRuntime
from src.modules.selection.service import enabled


def production_config():
    return CompanyAgentConfig.model_validate_json(
        (Path(__file__).parents[1] / "config/arti_kasnak.production.json").read_text()
    )


class Responses:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, *args, **kwargs):
        self.calls += 1
        return json.dumps(self.responses.pop(0))


async def test_semantic_requests_answer_known_specification_and_report_unknown_price():
    llm = Responses(
        {
            "requests": [
                {
                    "subject_id": "plastic_elevator_pulley",
                    "topic": "details",
                    "question": "Captormal özellikleri",
                },
                {
                    "subject_id": "plastic_elevator_pulley",
                    "topic": "price",
                    "question": "Captormal fiyatı",
                },
            ]
        },
        {
            "resolutions": [
                {"request_index": 0, "status": "answered", "fact_ids": ["plastic_pulley_benefits"]},
                {"request_index": 1, "status": "unavailable", "fact_ids": []},
            ]
        },
    )
    config = production_config()
    # Use the actual current fact ID rather than a stale catalog fixture.
    fact = next(
        f
        for f in config.facts
        if f.subject_id == "plastic_elevator_pulley"
        and f.category.value == "specification"
        and f.customer_visible
    )
    llm.responses[1]["resolutions"][0]["fact_ids"] = [fact.id]
    turn = await CompanyAgentRuntime(config, llm).reply("Captromal özellikleri ve fiyatı nedir?")
    assert llm.calls == 2
    assert turn.response_source == "model", turn
    assert not turn.used_fallback
    assert fact.customer_text["tr"] in turn.reply
    assert len(turn.request_resolutions) == 2
    assert turn.request_resolutions[1]["status"] == "unavailable"


async def test_stale_menu_uses_current_published_menu_without_model():
    llm = Responses()
    turn = await CompanyAgentRuntime(production_config(), llm).reply(
        "Eski menü [product_detail:deleted_product]"
    )
    assert llm.calls == 0
    assert turn.response_source == "guided"
    assert turn.fact_ids == ("all_product_groups",)
    assert turn.interaction is not None


async def test_semantic_invalid_evidence_is_visible_fallback():
    llm = Responses(
        {"requests": [{"subject_id": "company", "topic": "social", "question": "Merhaba"}]},
        {
            "resolutions": [
                {"request_index": 0, "status": "answered", "fact_ids": ["another_tenants_fact"]}
            ]
        },
    )
    turn = await CompanyAgentRuntime(production_config(), llm).reply("Selamlar size")
    assert turn.response_source == "fallback"
    assert turn.used_fallback
    assert turn.fallback_reason == "ValueError"
    assert "another_tenants_fact" not in turn.reply


async def test_model_outage_is_not_reported_as_model_success():
    class Unavailable:
        async def complete(self, *args, **kwargs):
            raise LLMCompletionError("test outage")

    turn = await CompanyAgentRuntime(production_config(), Unavailable()).reply(
        "Şirketiniz neler üretiyor?"
    )
    assert turn.response_source == "fallback"
    assert turn.fallback_reason == "LLMCompletionError"


def test_published_selection_applies_to_every_customer(monkeypatch):
    monkeypatch.setenv("SELECTION_ROLLOUT", "pilot")
    config = production_config()
    assert config.selection_flow is not None
    assert enabled(config, uuid4()) and enabled(config, uuid4())
    config.selection_flow = None
    assert not enabled(config, uuid4())


def test_preview_uses_live_selection_reducer_and_preserves_current_step():
    from src.modules.selection.service import preview_selection

    config = production_config()
    turn, state, resume = preview_selection(config, None, config.selection_flow.start_phrases[0])
    assert turn.response_source == "guided" and state["step_index"] == 0
    turn, state, resume = preview_selection(config, state, "Ayşe Demir")
    assert state["answers"]["contact_name"]["value"] == "Ayşe Demir"
    assert turn.response_source == "guided"
    old = dict(state)
    turn, state, resume = preview_selection(config, state, "Fiyatı nedir?")
    assert turn is None and resume
    assert state == old


async def test_semantic_runtime_respects_custom_company_graph_identity():
    config = CompanyAgentConfig.model_validate({
        "lifecycle":"approved", "organization":{"id":"training_org","display_names":{"tr":"Örnek Eğitim"}},
        "agent":{"purposes":["information"],"supported_locales":["tr"],"default_locale":"tr",
                 "semantic_dialogue":{"unavailable_messages":{"other":{"tr":"Bu bilgi doğrulanmadı."}},"clarification_text":{"tr":"Sorunuzu açar mısınız?"}}},
        "facts":[{"id":"training","subject_id":"training_org","category":"capability","value":"Kurumsal eğitim","source":"owner","customer_visible":True,"customer_text":{"tr":"Kurumsal eğitim sunuyoruz."}}]})
    llm=Responses({"requests":[{"subject_id":"training_org","topic":"details","question":"Hizmetleriniz?"}]},
                  {"resolutions":[{"request_index":0,"status":"answered","fact_ids":["training"]}]})
    turn=await CompanyAgentRuntime(config,llm).reply("Hangi hizmetleri sunuyorsunuz?")
    assert turn.response_source == "model"
    assert turn.reply == "Kurumsal eğitim sunuyoruz."
