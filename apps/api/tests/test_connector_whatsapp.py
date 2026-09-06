# ruff: noqa: RUF001
"""Mock-based tests for WhatsAppClient — no live Meta Graph API calls.

send_template/send_text are wrapped in @retry with exponential backoff;
error-path assertions call the underlying _post_message directly so a
failing-response test doesn't actually sleep through retries.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx
from httpx import Response

from src.integrations.whatsapp import WhatsAppClient


@pytest.fixture
def client() -> WhatsAppClient:
    return WhatsAppClient(
        access_token="test-token", phone_number_id="123456", app_secret="test-secret"
    )


@respx.mock
async def test_send_template_builds_correct_payload(client: WhatsAppClient) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(200, json={"messages": [{"id": "wamid.abc123"}]})
    )

    resp = await client.send_template(
        to="+905321234567",
        template_name="welcome_tr",
        language="tr",
        components=[{"type": "body", "parameters": [{"type": "text", "text": "Acme"}]}],
    )

    assert resp["messages"][0]["id"] == "wamid.abc123"
    payload = json.loads(route.calls.last.request.content)
    assert payload["to"] == "+905321234567"
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "welcome_tr"
    assert payload["template"]["language"]["code"] == "tr"


@respx.mock
async def test_send_text_builds_correct_payload(client: WhatsAppClient) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(200, json={"messages": [{"id": "wamid.def456"}]})
    )

    resp = await client.send_text("+905321234567", "Hello there")

    assert resp["messages"][0]["id"] == "wamid.def456"
    payload = json.loads(route.calls.last.request.content)
    assert payload["type"] == "text"
    assert payload["text"]["body"] == "Hello there"


@respx.mock
async def test_send_reply_buttons_builds_official_interactive_payload(
    client: WhatsAppClient,
) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(200, json={"messages": [{"id": "wamid.buttons"}]})
    )

    await client.send_reply_buttons_once(
        "+905321234567",
        "Kayış kasnağı türleri",
        [
            {"id": "product_detail:steel_belt_pulley", "title": "Çelik detayı"},
            {"id": "product_detail:plastic_belt_pulley", "title": "Plastik detayı"},
        ],
    )

    payload = json.loads(route.calls.last.request.content)
    assert payload["type"] == "interactive"
    assert payload["interactive"] == {
        "type": "button",
        "body": {"text": "Kayış kasnağı türleri"},
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {
                        "id": "product_detail:steel_belt_pulley",
                        "title": "Çelik detayı",
                    },
                },
                {
                    "type": "reply",
                    "reply": {
                        "id": "product_detail:plastic_belt_pulley",
                        "title": "Plastik detayı",
                    },
                },
            ]
        },
    }


@respx.mock
async def test_send_list_builds_official_interactive_payload(client: WhatsAppClient) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(200, json={"messages": [{"id": "wamid.list"}]})
    )

    await client.send_list_once(
        "+905321234567",
        "Ürün gruplarımız",
        button_text="Ürün seç",
        section_title="Ürün detayları",
        rows=[
            {
                "id": "product_detail:hoisting_pulley",
                "title": "Palanga detayı",
                "description": "Ürün detayını ve teknik bilgileri gör",
            }
        ],
    )

    payload = json.loads(route.calls.last.request.content)
    assert payload["interactive"]["type"] == "list"
    assert payload["interactive"]["action"]["button"] == "Ürün seç"
    assert payload["interactive"]["action"]["sections"][0]["rows"][0]["id"] == (
        "product_detail:hoisting_pulley"
    )


@respx.mock
async def test_send_cta_url_builds_official_interactive_payload(client: WhatsAppClient) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(200, json={"messages": [{"id": "wamid.cta"}]})
    )

    await client.send_cta_url_once(
        "+905321234567",
        "Palanga kasnağı detayları",
        button_text="Ürünü incele",
        url="https://www.artikasnak.com/asansor-kasnagi",
    )

    payload = json.loads(route.calls.last.request.content)
    assert payload["interactive"] == {
        "type": "cta_url",
        "body": {"text": "Palanga kasnağı detayları"},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": "Ürünü incele",
                "url": "https://www.artikasnak.com/asansor-kasnagi",
            },
        },
    }


@respx.mock
async def test_send_cta_url_can_include_an_approved_session_image(
    client: WhatsAppClient,
) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(200, json={"messages": [{"id": "wamid.image-header"}]})
    )

    await client.send_cta_url_once(
        "+905321234567",
        "Captormal plastik asansör kasnağı",
        button_text="Ürünü incele",
        url="https://www.artikasnak.com/urunler/captormal-asansor-kasnagi",
        header_media={
            "kind": "image",
            "link": "https://api.ashiraai.com/media/arti-kasnak/captormal-elevator.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": "90083",
        },
    )

    payload = json.loads(route.calls.last.request.content)
    assert payload["type"] == "interactive"
    assert payload["interactive"]["header"] == {
        "type": "image",
        "image": {
            "link": "https://api.ashiraai.com/media/arti-kasnak/captormal-elevator.jpg"
        },
    }


@respx.mock
async def test_send_list_can_include_an_approved_session_image(
    client: WhatsAppClient,
) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(200, json={"messages": [{"id": "wamid.image-list"}]})
    )

    await client.send_list_once(
        "+905321234567",
        "Döküm kasnak türleri",
        button_text="Ürün seç",
        section_title="Ürün detayları",
        rows=[{"id": "product_detail:hydraulic_pulley", "title": "Hidrolik"}],
        header_media={
            "kind": "image",
            "link": "https://api.ashiraai.com/media/arti-kasnak/cast-elevator.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": "125757",
        },
    )

    payload = json.loads(route.calls.last.request.content)
    assert payload["interactive"]["header"] == {
        "type": "image",
        "image": {
            "link": "https://api.ashiraai.com/media/arti-kasnak/cast-elevator.jpg"
        },
    }


@pytest.mark.parametrize(
    "header_media",
    [
        {
            "kind": "image",
            "link": "http://insecure.example/product.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": "123",
        },
        {
            "kind": "image",
            "link": "https://media.example/product.webp",
            "mime_type": "image/webp",
            "size_bytes": "123",
        },
        {
            "kind": "image",
            "link": "https://media.example/product.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": str(5 * 1024 * 1024 + 1),
        },
    ],
)
async def test_session_image_header_rejects_unsafe_metadata_before_network(
    client: WhatsAppClient,
    header_media: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="image header metadata"):
        await client.send_cta_url_once(
            "+905321234567",
            "Ürün bilgisi",
            button_text="Ürünü incele",
            url="https://www.artikasnak.com/urunler",
            header_media=header_media,
        )


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("send_reply_buttons_once", {"buttons": []}),
        (
            "send_list_once",
            {"button_text": "Ürün seç", "section_title": "Ürünler", "rows": []},
        ),
        (
            "send_cta_url_once",
            {"button_text": "Aç", "url": "http://insecure.example"},
        ),
    ],
)
async def test_interactive_sends_validate_limits_before_network(
    client: WhatsAppClient,
    method: str,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        await getattr(client, method)("+905321234567", "Yanıt", **kwargs)


@respx.mock
async def test_send_typing_indicator_builds_native_meta_payload(
    client: WhatsAppClient,
) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(200, json={"success": True})
    )

    response = await client.send_typing_indicator("wamid.inbound123")

    assert response == {"success": True}
    assert route.call_count == 1
    payload = json.loads(route.calls.last.request.content)
    assert payload == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.inbound123",
        "typing_indicator": {"type": "text"},
    }


@respx.mock
async def test_graph_api_version_is_configurable() -> None:
    client = WhatsAppClient(
        access_token="test-token",
        phone_number_id="123456",
        graph_api_version="v99.0",
    )
    route = respx.post("https://graph.facebook.com/v99.0/123456/messages").mock(
        return_value=Response(200, json={"messages": [{"id": "wamid.versioned"}]})
    )

    await client.send_text_once("+905321234567", "Versioned")

    assert route.call_count == 1


@respx.mock
async def test_send_text_once_never_retries_an_ambiguous_timeout(
    client: WhatsAppClient,
) -> None:
    route = respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        side_effect=httpx.ReadTimeout("ambiguous outcome")
    )

    with pytest.raises(httpx.ReadTimeout):
        await client.send_text_once("+905321234567", "Only once")

    assert route.call_count == 1


async def test_post_message_raises_when_credentials_missing() -> None:
    client = WhatsAppClient(access_token="", phone_number_id="", app_secret="")
    with pytest.raises(RuntimeError, match="not configured"):
        await client._post_message({"to": "x"})


@respx.mock
async def test_post_message_raises_for_error_response(client: WhatsAppClient) -> None:
    respx.post("https://graph.facebook.com/v20.0/123456/messages").mock(
        return_value=Response(400, json={"error": {"message": "invalid template"}})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client._post_message({"to": "x"})


class TestVerifySignature:
    def test_valid_signature_accepted(self) -> None:
        client = WhatsAppClient(app_secret="test-secret")
        body = b'{"entry": []}'
        expected = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        assert client.verify_signature(body, f"sha256={expected}") is True

    def test_wrong_signature_rejected(self) -> None:
        client = WhatsAppClient(app_secret="test-secret")
        assert client.verify_signature(b"body", "sha256=deadbeef") is False

    def test_missing_signature_header_rejected(self) -> None:
        client = WhatsAppClient(app_secret="test-secret")
        assert client.verify_signature(b"body", None) is False

    def test_missing_app_secret_rejected(self) -> None:
        client = WhatsAppClient(app_secret="")
        assert client.verify_signature(b"body", "sha256=whatever") is False

    def test_malformed_prefix_rejected(self) -> None:
        client = WhatsAppClient(app_secret="test-secret")
        assert client.verify_signature(b"body", "sha1=deadbeef") is False


async def test_check_contact_returns_none_when_unconfigured() -> None:
    client = WhatsAppClient(access_token="", phone_number_id="")
    assert await client.check_contact("+905321234567") is None
