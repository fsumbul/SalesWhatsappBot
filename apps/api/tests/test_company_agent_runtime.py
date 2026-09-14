# ruff: noqa: RUF001
"""Pure tests for the JSON-config-to-local-LLM runtime boundary."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from src.integrations.llm import (
    ChatCompletionsLLMClient,
    LLMCompletionError,
    LLMMessage,
    LLMNotConfiguredError,
    OllamaLLMClient,
)
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import (
    CompanyAgentRuntime,
    CustomerReplyAction,
    CustomerReplyParseError,
    RuntimeInteractionKind,
    _suggest_interaction,
    build_customer_decision_schema,
    build_customer_system_prompt,
    parse_customer_reply,
)


def _config() -> CompanyAgentConfig:
    return CompanyAgentConfig.model_validate(
        {
            "lifecycle": "approved",
            "organization": {"display_names": {"tr-TR": "Örnek Şirket"}},
            "offerings": [
                {
                    "id": "premium-plan",
                    "kind": "subscription",
                    "display_names": {"tr-TR": "Premium Plan"},
                }
            ],
            "facts": [
                {
                    "id": "premium-price",
                    "subject_id": "premium-plan",
                    "category": "commercial_rule",
                    "value": {"internal_price_cents": 99900},
                    "source": "internal-price-sheet-2026",
                    "customer_visible": True,
                    "customer_text": {"tr-TR": "Premium Plan aylık 999 TL'dir."},
                    "search_terms": ["fiyat"],
                },
                {
                    "id": "internal-margin",
                    "subject_id": "premium-plan",
                    "category": "other",
                    "value": "do not disclose",
                    "source": "finance",
                },
            ],
            "agent": {
                "purposes": ["information", "sales"],
                "supported_locales": ["tr-TR"],
                "default_locale": "tr-TR",
                "max_characters": 450,
                "unknown_fact_action": "handoff",
            },
        }
    )


def _config_with_three_visible_facts() -> CompanyAgentConfig:
    config_data = _config().model_dump(mode="json")
    config_data["facts"].extend(
        [
            {
                "id": "premium-delivery",
                "subject_id": "premium-plan",
                "category": "delivery",
                "value": "three days",
                "source": "delivery-policy",
                "customer_visible": True,
                "customer_text": {"tr-TR": "Teslimat üç iş günüdür."},
            },
            {
                "id": "premium-support",
                "subject_id": "premium-plan",
                "category": "support",
                "value": "phone",
                "source": "support-policy",
                "customer_visible": True,
                "customer_text": {"tr-TR": "Telefon desteği sunulur."},
            },
        ]
    )
    return CompanyAgentConfig.model_validate(config_data)


def _config_with_handoff_contact() -> CompanyAgentConfig:
    config_data = _config().model_dump(mode="json")
    config_data["facts"].append(
        {
            "id": "support-contact",
            "subject_id": "company",
            "category": "support",
            "value": "support@example.test",
            "source": "approved-contact-record",
            "customer_visible": True,
            "customer_text": {"tr-TR": "İletişim: support@example.test."},
        }
    )
    config_data["agent"]["handoff_fact_id"] = "support-contact"
    return CompanyAgentConfig.model_validate(config_data)


def _arti_kasnak_production_config(*, presentation: bool = False) -> CompanyAgentConfig:
    path = Path(__file__).resolve().parents[1] / "config" / "arti_kasnak.production.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not presentation:
        data["whatsapp_presentation"] = None
    data["agent"]["semantic_dialogue"] = None
    return CompanyAgentConfig.model_validate(data)


def test_prompt_projects_only_customer_visible_facts() -> None:
    prompt = build_customer_system_prompt(_config())

    assert "Premium Plan aylık 999 TL'dir." in prompt
    assert "premium-price" in prompt
    assert "internal_price_cents" not in prompt
    assert "internal-margin" not in prompt
    assert "do not disclose" not in prompt
    assert "internal-price-sheet-2026" not in prompt


def test_prompt_rejects_product_list_repetition_and_cross_product_claims() -> None:
    prompt = build_customer_system_prompt(_arti_kasnak_production_config())

    assert "A product list does not answer a technical-detail query" in prompt
    assert "Never transfer a claim" in prompt
    assert "from another product" in prompt


def test_arti_kasnak_detail_facts_render_approved_product_specific_answers() -> None:
    config = _arti_kasnak_production_config()

    palanga = parse_customer_reply(
        json.dumps(
            {
                "action": "reply",
                "fact_ids": ["cast_pulley_role_and_types", "cast_pulley_performance"],
            }
        ),
        config,
    )
    motor = parse_customer_reply(
        json.dumps(
            {
                "action": "reply",
                "fact_ids": [
                    "motor_pulley_custom_production",
                    "pulley_technical_information_required",
                ],
            }
        ),
        config,
    )

    assert "Palanga, saptırma ve hidrolik kasnaklar" in palanga.reply
    assert "2,5 m/sn" in palanga.reply
    assert "Artı Kasnak motor kasnağı üretir" in motor.reply
    assert "kasnak çapı, genişliği, halat adedi" in motor.reply
    assert "GG-25" not in motor.reply


def test_product_name_limits_prompt_and_schema_to_matching_subject() -> None:
    config = _arti_kasnak_production_config()
    question = "Motor kasnakları hakkında detaylı bilgi verir misin?"

    prompt = build_customer_system_prompt(config, customer_message=question)
    schema = build_customer_decision_schema(config, customer_message=question)
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "motor_pulley_custom_production" in fact_ids
    assert "cast_pulley_materials" not in fact_ids
    assert "cast_pulley_role_and_types" not in fact_ids
    assert "palanga_pulley_details" not in fact_ids
    assert "Artı Kasnak motor kasnağı üretir" in prompt
    assert "Palanga kasnağı" not in prompt


def test_named_product_inherits_facts_from_company_space_parent_groups() -> None:
    config = _arti_kasnak_production_config()

    deflection_schema = build_customer_decision_schema(
        config,
        customer_message="Saptırma kasnağı hakkında detay verir misin?",
    )
    deflection_fact_ids = deflection_schema["properties"]["fact_ids"]["items"]["enum"]

    assert "cast_pulley_role_and_types" in deflection_fact_ids
    assert "cast_pulley_performance" in deflection_fact_ids
    assert "palanga_pulley_details" not in deflection_fact_ids
    assert "motor_pulley_custom_production" not in deflection_fact_ids


def test_general_product_question_exposes_catalog_fact_not_random_dimensions() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Hangi ürünleri üretiyorsunuz?",
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert fact_ids == ["all_product_groups"]


def test_specific_product_detail_prefers_exact_fact_over_redundant_parent() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Palanga hakkında detay verir misin?",
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "palanga_pulley_details" in fact_ids
    assert not any(fact_id.startswith("conversation_") for fact_id in fact_ids)
    assert "cast_pulley_role_and_types" not in fact_ids


def test_specific_belt_variant_excludes_sibling_product_facts() -> None:
    config = _arti_kasnak_production_config()
    question = "Plastik kayış kasnağı hakkında bilgi verir misin?"

    schema = build_customer_decision_schema(config, customer_message=question)
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "plastic_belt_pulley_details" in fact_ids
    assert "belt_pulley_types" not in fact_ids
    assert "steel_belt_pulley_details" not in fact_ids
    assert "plastic_pulley_performance" not in fact_ids


def test_reply_rejects_unknown_or_internal_fact_citation() -> None:
    with pytest.raises(CustomerReplyParseError, match="non-customer-visible"):
        parse_customer_reply(
            json.dumps({"action": "reply", "fact_ids": ["internal-margin"]}),
            _config(),
        )


def test_reply_enforces_configured_output_limit() -> None:
    config = _config().model_copy(
        update={"agent": _config().agent.model_copy(update={"max_characters": 10})}
    )
    with pytest.raises(CustomerReplyParseError, match="max_characters"):
        parse_customer_reply(json.dumps({"action": "reply", "fact_ids": ["premium-price"]}), config)


def test_model_cannot_supply_customer_visible_text() -> None:
    with pytest.raises(CustomerReplyParseError, match="valid customer reply JSON"):
        parse_customer_reply(
            json.dumps(
                {
                    "action": "reply",
                    "reply": "Model tarafından uydurulan metin",
                    "fact_ids": ["premium-price"],
                }
            ),
            _config(),
        )


def test_reply_is_rendered_from_literal_approved_customer_text() -> None:
    turn = parse_customer_reply(
        json.dumps({"action": "reply", "fact_ids": ["premium-price"]}), _config()
    )

    assert turn.reply == "Premium Plan aylık 999 TL'dir."


def test_decision_schema_allows_only_reply_or_configured_unknown_action() -> None:
    schema = build_customer_decision_schema(_config_with_three_visible_facts())

    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["action"]["enum"] == ["reply", "handoff"]
    assert properties["fact_ids"]["items"]["enum"] == [
        "premium-price",
        "premium-delivery",
        "premium-support",
    ]
    assert properties["fact_ids"]["maxItems"] == 2


def test_reply_rejects_more_than_two_facts_even_without_schema_enforcement() -> None:
    with pytest.raises(CustomerReplyParseError, match="valid customer reply JSON"):
        parse_customer_reply(
            json.dumps(
                {
                    "action": "reply",
                    "fact_ids": ["premium-price", "premium-delivery", "premium-support"],
                }
            ),
            _config_with_three_visible_facts(),
        )


class _FactSelectingLLM:
    """Scripted language model for transport/UI tests; live semantics have separate acceptance."""
    def __init__(self, *fact_ids):
        self.fact_ids = fact_ids
        self.response_schema = None

    async def complete(self, messages, *, system="", max_tokens=1024, response_schema=None):
        self.response_schema = response_schema
        title = response_schema["title"]
        if title == "LanguageCheck":
            return json.dumps({"unsupported_claims": [], "supported": True})
        if title == "RequestPlan":
            candidates = [*_arti_kasnak_production_config().facts, *_config_with_three_visible_facts().facts]
            fact = next((f for f in candidates if f.id in self.fact_ids), None)
            social = fact and fact.category.value == "social"
            subject = fact.subject_id if fact else "company"
            return json.dumps({"requests": [{"subject_id": subject,
                "topic": "social" if social else "price" if messages[-1].content == "Fiyat nedir?" else "details",
                "question": messages[-1].content[:180]}]})
        assert title == "LanguageReply"
        data = json.loads(messages[0].content)
        if any(f.startswith("conversation_") or f == "welcome" for f in self.fact_ids):
            return json.dumps({"text": "Modelin bu konuşma için ürettiği yanıt."})
        texts = {e["id"]: e.get("text", "") for e in data["evidence"]}
        return json.dumps({"text": "\n".join(texts.get(f, "Unsupported") for f in self.fact_ids),
                           "evidence_ids": self.fact_ids})


class _ReplyingLocalLLM(_FactSelectingLLM):
    def __init__(self):
        super().__init__("premium-price")


class _UnavailableLocalLLM:
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        raise LLMNotConfiguredError("offline")


class _NetworkErrorLocalLLM:
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        raise httpx.ConnectError("offline")


class _UnexpectedModelErrorLLM:
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        raise RuntimeError("model process crashed")


class _MalformedLocalLLM:
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        return '{"action":"reply","reply":"injected","fact_ids":["premium-price"]}'


@pytest.mark.asyncio
async def test_runtime_returns_only_validated_local_llm_output() -> None:
    llm = _ReplyingLocalLLM()
    turn = await CompanyAgentRuntime(_config(), llm).reply("Fiyat nedir?")

    assert turn.action == CustomerReplyAction.REPLY
    assert turn.reply == "Premium Plan aylık 999 TL'dir."
    assert turn.fact_ids == ("premium-price",)
    assert turn.used_fallback is False
    assert llm.response_schema["title"] == "LanguageCheck"
    assert turn.answer_verified and turn.answer_origin == "model_generated"



@pytest.mark.asyncio
async def test_product_catalog_reply_offers_progressive_product_buttons() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _FactSelectingLLM("all_product_groups")).reply(
        "Hangi ürünleri üretiyorsunuz?"
    )

    assert turn.interaction is not None
    assert turn.interaction.kind == RuntimeInteractionKind.REPLY_BUTTONS
    assert [option.id for option in turn.interaction.options] == [
        "product_detail:elevator_pulley",
        "product_detail:belt_pulley",
        "product_detail:pulley_components",
    ]
    assert [option.title for option in turn.interaction.options] == [
        "Asansör türleri",
        "Kayış türleri",
        "Mil ve bileşenler",
    ]


@pytest.mark.asyncio
async def test_welcome_reply_is_model_generated() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _FactSelectingLLM("welcome")).reply("Merhaba")

    assert turn.answer_verified
    assert turn.answer_origin == "model_generated"



def test_broad_conversation_gets_semantic_candidates_without_random_technical_facts() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Bugün içimde tarif edemediğim bir sıkıntı var.",
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "conversation_empathy" in fact_ids
    assert "conversation_confusion" in fact_ids
    assert "conversation_disinterest" in fact_ids
    assert "conversation_indecision" in fact_ids
    assert "conversation_waiting" in fact_ids
    assert not any(fact_id.startswith("quote_") for fact_id in fact_ids)
    assert "contact_information" not in fact_ids
    assert "cast_pulley_materials" not in fact_ids


@pytest.mark.parametrize(
    ("message", "fact_id"),
    [
        ("Ne demeye çalıştığınızı çözemedim.", "conversation_confusion"),
        ("Hayır, kastım o değildi.", "conversation_correction"),
        ("Şimdilik pas geçeceğim.", "conversation_disinterest"),
        ("İki arada kaldım, nereden başlayacağımı bilmiyorum.", "conversation_indecision"),
        ("Sesiniz çıkmadı, hâlâ buradayım.", "conversation_waiting"),
    ],
)
@pytest.mark.asyncio
async def test_social_conversation_is_model_generated_without_canned_facts(
    message: str,
    fact_id: str,
) -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _FactSelectingLLM(fact_id)).reply(message)
    fact = next(item for item in config.facts if item.id == fact_id)

    assert turn.action == CustomerReplyAction.REPLY
    assert turn.fact_ids == ()
    assert turn.reply != fact.customer_text["tr"]
    assert turn.answer_verified
    assert turn.used_fallback is False


@pytest.mark.asyncio
async def test_sensitive_social_replies_do_not_show_pushy_sales_buttons() -> None:
    config = _arti_kasnak_production_config()

    empathy = await CompanyAgentRuntime(
        config,
        _FactSelectingLLM("conversation_empathy"),
    ).reply("Bugün moralim çok bozuk.")
    farewell = await CompanyAgentRuntime(
        config,
        _FactSelectingLLM("conversation_farewell"),
    ).reply("Ben kaçayım artık.")

    assert empathy.interaction is None
    assert farewell.interaction is None


def test_supported_business_intent_wins_over_social_language() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Nasılsın, palanga kasnağı hakkında detay verir misin?",
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "palanga_pulley_details" in fact_ids
    assert not any(fact_id.startswith("conversation_") for fact_id in fact_ids)


def test_product_praise_can_choose_a_social_behavior_without_mixing_claims() -> None:
    config = _arti_kasnak_production_config()
    message = "Palanga kasnağınız gerçekten çok iyi, tebrik ederim."

    schema = build_customer_decision_schema(config, customer_message=message)
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]
    turn = parse_customer_reply(
        json.dumps(
            {
                "action": "reply",
                "fact_ids": ["conversation_gratitude"],
            }
        ),
        config,
        customer_message=message,
    )

    assert "palanga_pulley_details" in fact_ids
    assert "conversation_gratitude" in fact_ids
    assert turn.fact_ids == ("conversation_gratitude",)


def test_confident_social_feedback_cannot_fall_through_to_handoff() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Bu cevap hiç iyi değildi.",
    )

    assert schema["properties"]["action"]["enum"] == ["reply"]
    assert schema["properties"]["fact_ids"]["minItems"] == 1


@pytest.mark.parametrize(
    "fact_ids",
    [
        ["conversation_gratitude", "conversation_casual_chat"],
        ["conversation_gratitude", "palanga_pulley_details"],
    ],
)
def test_parser_rejects_multiple_or_mixed_social_facts(fact_ids: list[str]) -> None:
    config = _arti_kasnak_production_config()

    with pytest.raises(CustomerReplyParseError, match="exactly one social"):
        parse_customer_reply(
            json.dumps({"action": "reply", "fact_ids": fact_ids}),
            config,
            customer_message="Palanga kasnağınız gerçekten çok iyi, tebrik ederim.",
        )


def test_vague_follow_up_recovers_the_last_grounded_product_subject() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Bunu biraz daha açar mısın?",
        context_fact_ids=("palanga_pulley_details",),
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "palanga_pulley_details" in fact_ids
    assert "cast_pulley_role_and_types" in fact_ids
    assert "motor_pulley_custom_production" not in fact_ids


def test_contextual_material_question_cannot_leak_sibling_product_facts() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Bunun malzemesi nedir?",
        context_fact_ids=("palanga_pulley_details",),
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "palanga_pulley_details" in fact_ids
    assert "cast_pulley_materials" in fact_ids
    assert "plastic_pulley_material" not in fact_ids
    assert "steel_belt_pulley_details" not in fact_ids


def test_context_lineage_disambiguates_an_ambiguous_product_name() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Plastik olanı biraz anlatır mısın?",
        context_fact_ids=("belt_pulley_types",),
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "plastic_belt_pulley_details" in fact_ids
    assert "plastic_pulley_material" not in fact_ids
    assert "plastic_pulley_performance" not in fact_ids


def test_company_question_and_product_context_are_both_available_for_disambiguation() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Şirket hangi malzemelerle üretim yapıyor?",
        context_fact_ids=("palanga_pulley_details",),
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "in_house_manufacturing" in fact_ids
    assert "cast_pulley_materials" in fact_ids
    assert fact_ids.index("in_house_manufacturing") < fact_ids.index("cast_pulley_materials")
    assert "plastic_pulley_material" not in fact_ids


def test_contextual_unknown_policy_does_not_borrow_a_sibling_warranty() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Bunun garantisi ne kadar?",
        context_fact_ids=("palanga_pulley_details",),
    )
    fact_id_schema = schema["properties"]["fact_ids"]

    assert fact_id_schema["maxItems"] == 0
    assert "enum" not in fact_id_schema["items"]


def test_current_company_question_overrides_old_product_context() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Adresiniz nerede?",
        context_fact_ids=("palanga_pulley_details",),
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "contact_information" in fact_ids
    assert "palanga_pulley_details" not in fact_ids


@pytest.mark.parametrize(
    "message",
    [
        "Motor kasnağı için fiyat teklifi almak istiyorum.",
        "Palanga için fiyat teklifi hazırlar mısınız?",
    ],
)
def test_explicit_quote_request_keeps_only_the_approved_quote_intake(
    message: str,
) -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(config, customer_message=message)
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert fact_ids == ["quote_product_question"]


@pytest.mark.parametrize(
    "message",
    [
        "Palanga kaç para?",
        "Maliyeti nedir?",
        "Bedeli ne?",
    ],
)
def test_price_question_cannot_be_recast_as_quote_intake(message: str) -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(config, customer_message=message)
    fact_id_schema = schema["properties"]["fact_ids"]

    assert fact_id_schema["maxItems"] == 0
    assert "enum" not in fact_id_schema["items"]


def test_competitor_comparison_stays_fail_closed() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Rakibinizden daha kaliteli misiniz?",
    )
    fact_id_schema = schema["properties"]["fact_ids"]

    assert fact_id_schema["maxItems"] == 0
    assert "enum" not in fact_id_schema["items"]


@pytest.mark.parametrize(
    "message",
    [
        "Stokta var mı?",
        "Elde var mı?",
        "Mevcut mu?",
        "Kaç günde gelir?",
        "Ne zaman teslim edilir?",
        "Termin süresi?",
        "Teslimat süresi nedir?",
    ],
)
def test_unknown_stock_and_delivery_time_stay_fail_closed(message: str) -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(config, customer_message=message)
    fact_id_schema = schema["properties"]["fact_ids"]

    assert fact_id_schema["maxItems"] == 0
    assert "enum" not in fact_id_schema["items"]


@pytest.mark.parametrize(
    "message",
    [
        "Bu sistemime uyar mı?",
        "Buna olur mu?",
        "Uyumlu mu?",
    ],
)
def test_suitability_question_can_only_use_the_technical_review_boundary(
    message: str,
) -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(config, customer_message=message)
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert fact_ids == ["technical_selection_handoff"]


def test_drawing_submission_is_not_misclassified_as_a_certificate_request() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Teknik çizimi belge olarak gönderebilir miyim?",
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert fact_ids == ["quote_drawing_question"]
    assert "company_quality_standards" not in fact_ids


@pytest.mark.parametrize("message", ["Stockholm", "Bir belgesel önerir misiniz?"])
def test_protected_stems_do_not_match_arbitrary_word_prefixes(message: str) -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(config, customer_message=message)
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "conversation_off_topic" in fact_ids


def test_quote_cta_precedes_contact_and_product_links() -> None:
    config = _arti_kasnak_production_config()
    turn = parse_customer_reply(
        json.dumps(
            {
                "action": "reply",
                "fact_ids": ["contact_information", "quote_product_question"],
            }
        ),
        config,
    )

    interaction = _suggest_interaction(config, turn, "Motor kasnağı")

    assert interaction is not None
    assert interaction.kind == RuntimeInteractionKind.CTA_URL
    assert interaction.url == "https://www.artikasnak.com/talep-formu"


def test_explicit_new_product_overrides_old_product_context() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Motor kasnağını soruyorum, biraz anlatır mısın?",
        context_fact_ids=("palanga_pulley_details",),
    )
    fact_ids = schema["properties"]["fact_ids"]["items"]["enum"]

    assert "motor_pulley_custom_production" in fact_ids
    assert "palanga_pulley_details" not in fact_ids


@pytest.mark.asyncio
async def test_runtime_rejects_a_globally_visible_fact_outside_query_candidates() -> None:
    turn = await CompanyAgentRuntime(
        _config_with_three_visible_facts(),
        _FactSelectingLLM("premium-support"),
    ).reply("Fiyat nedir?")

    assert turn.action == CustomerReplyAction.REPLY
    assert turn.fact_ids == ()
    assert turn.used_fallback is True


@pytest.mark.asyncio
async def test_plain_product_menu_never_bypasses_unavailable_model() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _UnavailableLocalLLM()).reply(
        "ürünleri göster",
        history=[LLMMessage(role="assistant", content="eski ilgisiz konuşma")] * 12,
    )

    assert turn.reply == "" and turn.used_fallback
    assert not turn.answer_verified


@pytest.mark.asyncio
async def test_marketing_info_requires_model_generated_reply() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _UnavailableLocalLLM()).reply("Bilgi Al")

    assert turn.reply == "" and turn.used_fallback
    assert not turn.answer_verified


@pytest.mark.asyncio
async def test_own_fact_button_resolves_data_but_does_not_fabricate_outage_reply() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _UnavailableLocalLLM()).reply(
        "Ürünleri göster [fact_request:all_product_groups]"
    )

    assert turn.fact_ids == ("all_product_groups",)
    assert turn.interaction is not None


def test_guided_fact_button_exposes_only_its_approved_fact() -> None:
    config = _arti_kasnak_production_config()

    schema = build_customer_decision_schema(
        config,
        customer_message="Ürünleri göster [fact_request:all_product_groups]",
    )

    assert schema["properties"]["fact_ids"]["items"]["enum"] == ["all_product_groups"]


@pytest.mark.asyncio
async def test_elevator_product_family_uses_a_four_item_list() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _FactSelectingLLM("product_families")).reply(
        "Asansör türleri [product_detail:elevator_pulley]",
        history=[LLMMessage(role="assistant", content="eski ilgisiz konuşma")] * 12,
    )

    assert turn.fact_ids == ("product_families",)
    assert "Captormal (MC Nylon 6)" in turn.reply
    assert "çekiş kasnaklarından" in turn.reply
    assert turn.interaction is not None
    assert turn.interaction.kind == RuntimeInteractionKind.LIST
    assert [option.id for option in turn.interaction.options] == [
        "product_detail:plastic_elevator_pulley",
        "product_detail:cast_elevator_pulley",
        "product_detail:motor_pulley",
        "product_detail:traction_sheave",
    ]


@pytest.mark.asyncio
async def test_product_detail_button_returns_an_approved_product_link_cta() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _FactSelectingLLM("palanga_pulley_details")).reply(
        "Palanga detayı [product_detail:hoisting_pulley]"
    )

    assert turn.interaction is not None
    assert turn.interaction.kind == RuntimeInteractionKind.CTA_URL
    assert turn.interaction.button_text == "Ürünü incele"
    assert turn.interaction.url == "https://www.artikasnak.com/urunler/dokum-kasnaklar"


@pytest.mark.asyncio
async def test_product_detail_attaches_its_approved_session_image() -> None:
    config = _arti_kasnak_production_config(presentation=True)
    turn = await CompanyAgentRuntime(config, _UnavailableLocalLLM()).reply(
        "Captormal detayı [product_detail:plastic_elevator_pulley]"
    )

    assert turn.interaction is not None
    assert turn.interaction.kind == RuntimeInteractionKind.CTA_URL
    assert turn.interaction.url == (
        "https://www.artikasnak.com/urunler/captormal-asansor-kasnagi"
    )
    assert turn.interaction.header_media is not None
    assert turn.interaction.header_media.id == "captormal-elevator-image"
    assert turn.interaction.header_media.url == (
        "https://api.ashiraai.com/media/arti-kasnak/captormal-elevator.jpg"
    )


@pytest.mark.asyncio
async def test_product_family_menu_attaches_its_approved_session_image() -> None:
    config = _arti_kasnak_production_config(presentation=True)
    turn = await CompanyAgentRuntime(config, _UnavailableLocalLLM()).reply(
        "Döküm detayı [product_detail:cast_elevator_pulley]"
    )

    assert turn.interaction is not None
    assert turn.interaction.kind == RuntimeInteractionKind.REPLY_BUTTONS
    assert turn.interaction.header_media is not None
    assert turn.interaction.header_media.id == "cast-elevator-image"


@pytest.mark.asyncio
async def test_small_product_family_uses_inline_reply_buttons() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(config, _FactSelectingLLM("belt_pulley_types")).reply(
        "Kayış kasnağı türleri nelerdir?"
    )

    assert turn.interaction is not None
    assert turn.interaction.kind == RuntimeInteractionKind.REPLY_BUTTONS
    assert [option.id for option in turn.interaction.options] == [
        "product_detail:steel_belt_pulley",
        "product_detail:plastic_belt_pulley",
    ]


@pytest.mark.asyncio
async def test_company_profile_reply_offers_the_approved_website_cta() -> None:
    config = _arti_kasnak_production_config()
    turn = await CompanyAgentRuntime(
        config,
        _FactSelectingLLM("company_history_and_reach"),
    ).reply("Şirket hakkında bilgi verir misin?")

    assert turn.interaction is not None
    assert turn.interaction.kind == RuntimeInteractionKind.CTA_URL
    assert turn.interaction.url == "https://www.artikasnak.com/"


@pytest.mark.asyncio
async def test_runtime_fails_closed_when_local_llm_is_unavailable() -> None:
    turn = await CompanyAgentRuntime(_config(), _UnavailableLocalLLM()).reply("Fiyat nedir?")

    assert turn.action == CustomerReplyAction.REPLY
    assert turn.fact_ids == ()
    assert turn.used_fallback is True


@pytest.mark.asyncio
async def test_handoff_uses_only_the_configured_customer_visible_contact() -> None:
    turn = await CompanyAgentRuntime(_config_with_handoff_contact(), _UnavailableLocalLLM()).reply(
        "Bilinmeyen bir soru"
    )

    assert turn.action == CustomerReplyAction.REPLY
    assert turn.reply == ""
    assert turn.fact_ids == ()
    assert turn.used_fallback is True


@pytest.mark.parametrize(
    "llm",
    [_NetworkErrorLocalLLM(), _UnexpectedModelErrorLLM(), _MalformedLocalLLM()],
)
@pytest.mark.asyncio
async def test_runtime_fails_closed_for_any_model_boundary_error(llm: object) -> None:
    turn = await CompanyAgentRuntime(_config(), llm).reply("Fiyat nedir?")

    assert turn.action == CustomerReplyAction.REPLY
    assert turn.reply == ""
    assert turn.fact_ids == ()
    assert turn.used_fallback is True


@respx.mock
@pytest.mark.asyncio
async def test_ollama_client_sends_strict_structured_output_request() -> None:
    route = respx.post("http://ollama:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": '{"action":"handoff","fact_ids":[]}'}},
        )
    )
    schema = build_customer_decision_schema(_config())

    raw = await OllamaLLMClient(base_url="http://ollama:11434/v1", model="qwen3:8b").complete(
        [LLMMessage(role="user", content="Bilinmeyen bir soru")],
        system="system",
        response_schema=schema,
    )

    assert raw == '{"action":"handoff","fact_ids":[]}'
    request_payload = json.loads(route.calls.last.request.content)
    assert request_payload["stream"] is False
    assert request_payload["think"] is False
    assert request_payload["keep_alive"] == "10m"
    assert request_payload["options"] == {
        "temperature": 0,
        "num_predict": 1024,
        "num_ctx": 4096,
    }
    assert request_payload["format"] == schema


@respx.mock
@pytest.mark.asyncio
async def test_ollama_client_hides_http_error_body() -> None:
    respx.post("http://ollama:11434/api/chat").mock(
        return_value=httpx.Response(503, text="sensitive echoed customer content")
    )

    with pytest.raises(LLMCompletionError) as caught:
        await OllamaLLMClient(base_url="http://ollama:11434/v1", model="qwen3:8b").complete(
            [LLMMessage(role="user", content="hello")]
        )

    assert "sensitive" not in str(caught.value)


@respx.mock
@pytest.mark.asyncio
async def test_ollama_client_keeps_existing_unstructured_calls_working() -> None:
    route = respx.post("http://ollama:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "plain reply"}})
    )

    raw = await OllamaLLMClient(base_url="http://ollama:11434/v1/", model="qwen3:8b").complete(
        [LLMMessage(role="user", content="hello")]
    )

    assert raw == "plain reply"
    request_payload = json.loads(route.calls.last.request.content)
    assert "format" not in request_payload


@respx.mock
@pytest.mark.asyncio
async def test_chat_completions_client_sends_schema_and_bearer_key() -> None:
    route = respx.post("https://llm.example.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"action":"handoff","fact_ids":[]}'}}]},
        )
    )
    schema = build_customer_decision_schema(_config())

    raw = await ChatCompletionsLLMClient(
        base_url="https://llm.example.test/v1",
        model="instruct-model",
        api_key="private-key",
    ).complete(
        [LLMMessage(role="user", content="Bilinmeyen bir soru")],
        system="system",
        response_schema=schema,
    )

    assert raw == '{"action":"handoff","fact_ids":[]}'
    assert route.calls.last.request.headers["Authorization"] == "Bearer private-key"
    request_payload = json.loads(route.calls.last.request.content)
    assert request_payload["model"] == "instruct-model"
    assert request_payload["response_format"]["json_schema"]["schema"] == schema
