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
