"""Pure tests for the JSON-config-to-local-LLM runtime boundary."""

import json

import httpx
import pytest
import respx

from src.integrations.llm import (
    LLMCompletionError,
    LLMMessage,
    LLMNotConfiguredError,
    OllamaLLMClient,
    ChatCompletionsLLMClient,
)
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import (
    CompanyAgentRuntime,
    CustomerReplyAction,
    CustomerReplyParseError,
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
                    "customer_text": {"tr-TR": "Premium Plan aylık 999 TL'dir."},  # noqa: RUF001
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


def test_prompt_projects_only_customer_visible_facts() -> None:
    prompt = build_customer_system_prompt(_config())

    assert "Premium Plan aylık 999 TL'dir." in prompt  # noqa: RUF001
    assert "premium-price" in prompt
    assert "internal_price_cents" not in prompt
    assert "internal-margin" not in prompt
    assert "do not disclose" not in prompt
    assert "internal-price-sheet-2026" not in prompt


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
                    "reply": "Model tarafından uydurulan metin",  # noqa: RUF001
                    "fact_ids": ["premium-price"],
                }
            ),
            _config(),
        )


def test_reply_is_rendered_from_literal_approved_customer_text() -> None:
    turn = parse_customer_reply(
        json.dumps({"action": "reply", "fact_ids": ["premium-price"]}), _config()
    )

    assert turn.reply == "Premium Plan aylık 999 TL'dir."  # noqa: RUF001


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


class _ReplyingLocalLLM:
    response_schema: dict[str, object] | None = None

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str = "",
        max_tokens: int = 1024,
        response_schema: dict[str, object] | None = None,
    ) -> str:
        assert messages[-1].content == "Fiyat nedir?"
        assert "customer_visible_facts" in system
        self.response_schema = response_schema
        return '{"action":"reply","fact_ids":["premium-price"]}'


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
    assert turn.reply == "Premium Plan aylık 999 TL'dir."  # noqa: RUF001
    assert turn.fact_ids == ("premium-price",)
    assert turn.used_fallback is False
    assert llm.response_schema == build_customer_decision_schema(_config())


@pytest.mark.asyncio
async def test_runtime_fails_closed_when_local_llm_is_unavailable() -> None:
    turn = await CompanyAgentRuntime(_config(), _UnavailableLocalLLM()).reply("Fiyat nedir?")

    assert turn.action == CustomerReplyAction.HANDOFF
    assert turn.fact_ids == ()
    assert turn.used_fallback is True


@pytest.mark.asyncio
async def test_handoff_uses_only_the_configured_customer_visible_contact() -> None:
    turn = await CompanyAgentRuntime(_config_with_handoff_contact(), _UnavailableLocalLLM()).reply(
        "Bilinmeyen bir soru"
    )

    assert turn.action == CustomerReplyAction.HANDOFF
    assert (
        turn.reply
        == (
            "Bu bilgiyi otomatik olarak yanıtlayamıyorum. "  # noqa: RUF001
            "İletişim: support@example.test."
        )
    )
    assert turn.fact_ids == ("support-contact",)
    assert turn.used_fallback is True


@pytest.mark.parametrize(
    "llm",
    [_NetworkErrorLocalLLM(), _UnexpectedModelErrorLLM(), _MalformedLocalLLM()],
)
@pytest.mark.asyncio
async def test_runtime_fails_closed_for_any_model_boundary_error(llm: object) -> None:
    turn = await CompanyAgentRuntime(_config(), llm).reply("Fiyat nedir?")

    assert turn.action == CustomerReplyAction.HANDOFF
    assert (
        turn.reply
        == "Bu bilgiyi otomatik olarak yanıtlayamıyorum. Yetkili ekip incelemesi gerekiyor."  # noqa: RUF001
    )
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
