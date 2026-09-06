"""WhatsApp Business Cloud API client (Meta Graph v20+).

Handles: template messages, session (free-form) messages, contact existence
checks, webhook signature verification.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, cast
from urllib.parse import urlparse

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.config import get_settings

logger = structlog.get_logger(__name__)

_GRAPH_ROOT = "https://graph.facebook.com"


class WhatsAppClient:
    def __init__(
        self,
        *,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        app_secret: str | None = None,
        graph_api_version: str | None = None,
    ) -> None:
        s = get_settings()
        self.access_token = access_token or s.whatsapp_access_token
        self.phone_number_id = phone_number_id or s.whatsapp_phone_number_id
        self.app_secret = app_secret or s.whatsapp_app_secret
        self.graph_api_version = graph_api_version or s.whatsapp_graph_api_version

    # --- Messages ---

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str,
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        body = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components or [],
            },
        }
        return await self._post_message(body)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def send_text(self, to: str, body_text: str, preview_url: bool = False) -> dict[str, Any]:
        return await self.send_text_once(to, body_text, preview_url)

    async def send_text_once(
        self, to: str, body_text: str, preview_url: bool = False
    ) -> dict[str, Any]:
        """Send one POST with no transport retry.

        Customer-agent jobs use this at-most-once boundary. A timeout after
        Meta accepted a POST is ambiguous, so retrying it could duplicate a
        customer-visible message.
        """

        body = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body_text, "preview_url": preview_url},
        }
        return await self._post_message(body)

    async def send_reply_buttons_once(
        self,
        to: str,
        body_text: str,
        buttons: list[dict[str, str]],
        header_media: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send one session message with one to three inline reply buttons."""

        self._validate_interactive_body(body_text)
        if not 1 <= len(buttons) <= 3:
            raise ValueError("WhatsApp reply messages require one to three buttons")
        for button in buttons:
            if not button.get("id") or len(button["id"]) > 256:
                raise ValueError("WhatsApp reply button ids must contain 1-256 characters")
            if not button.get("title") or len(button["title"]) > 20:
                raise ValueError("WhatsApp reply button titles must contain 1-20 characters")
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                **self._interactive_image_header(header_media),
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": button["id"], "title": button["title"]},
                        }
                        for button in buttons
                    ]
                },
            },
        }
        return await self._post_message(body)

    async def send_list_once(
        self,
        to: str,
        body_text: str,
        *,
        button_text: str,
        section_title: str,
        rows: list[dict[str, str]],
        header_media: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send one session list containing up to ten deterministic choices."""

        self._validate_interactive_body(body_text)
        if not button_text or len(button_text) > 20:
            raise ValueError("WhatsApp list button text must contain 1-20 characters")
        if not section_title or len(section_title) > 24:
            raise ValueError("WhatsApp list section titles must contain 1-24 characters")
        if not 1 <= len(rows) <= 10:
            raise ValueError("WhatsApp list messages require one to ten rows")
        for row in rows:
            if not row.get("id") or len(row["id"]) > 200:
                raise ValueError("WhatsApp list row ids must contain 1-200 characters")
            if not row.get("title") or len(row["title"]) > 24:
                raise ValueError("WhatsApp list row titles must contain 1-24 characters")
            if len(row.get("description", "")) > 72:
                raise ValueError("WhatsApp list descriptions may contain at most 72 characters")
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                **self._interactive_image_header(header_media),
                "body": {"text": body_text},
                "action": {
                    "button": button_text,
                    "sections": [
                        {
                            "title": section_title,
                            "rows": [
                                {
                                    "id": row["id"],
                                    "title": row["title"],
                                    **(
                                        {"description": row["description"]}
                                        if row.get("description")
                                        else {}
                                    ),
                                }
                                for row in rows
                            ],
                        }
                    ],
                },
            },
        }
        return await self._post_message(body)

    async def send_cta_url_once(
        self,
        to: str,
        body_text: str,
        *,
        button_text: str,
        url: str,
        header_media: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send one session call-to-action button to an approved HTTPS URL."""

        self._validate_interactive_body(body_text)
        if not button_text or len(button_text) > 20:
            raise ValueError("WhatsApp CTA text must contain 1-20 characters")
        if not self._is_https_url(url) or len(url) > 2000:
            raise ValueError("WhatsApp CTA URLs must be HTTPS and at most 2000 characters")
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "cta_url",
                **self._interactive_image_header(header_media),
                "body": {"text": body_text},
                "action": {
                    "name": "cta_url",
                    "parameters": {
                        "display_text": button_text,
                        "url": url,
                    },
                },
            },
        }
        return await self._post_message(body)

    @staticmethod
    def _is_https_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username

    @classmethod
    def _interactive_image_header(
        cls,
        media: dict[str, str] | None,
    ) -> dict[str, Any]:
        if media is None:
            return {}
        link = media.get("link", "")
        mime_type = media.get("mime_type", "")
        try:
            size_bytes = int(media.get("size_bytes", "0"))
        except ValueError as exc:
            raise ValueError("WhatsApp image size must be an integer") from exc
        if (
            media.get("kind") != "image"
            or not cls._is_https_url(link)
            or mime_type not in {"image/jpeg", "image/png"}
            or not 0 < size_bytes <= 5 * 1024 * 1024
        ):
            raise ValueError("WhatsApp image header metadata is invalid")
        return {"header": {"type": "image", "image": {"link": link}}}

    @staticmethod
    def _validate_interactive_body(body_text: str) -> None:
        if not body_text or len(body_text) > 1024:
            raise ValueError("WhatsApp interactive bodies must contain 1-1024 characters")

    async def send_typing_indicator(self, message_id: str) -> dict[str, Any]:
        """Mark an inbound message read and show WhatsApp's native typing UI.

        Each call is deliberately short and best-effort. The runtime may
        refresh the transient indicator while a reply is being prepared, but
        indicator failures must never delay or suppress the customer reply.
        """

        body = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        return await self._post_message(body, timeout=5.0)

    async def _post_message(self, body: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
        if not self.access_token or not self.phone_number_id:
            raise RuntimeError("WhatsApp credentials not configured")
        url = f"{_GRAPH_ROOT}/{self.graph_api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                logger.warning("wa_send_error", status=resp.status_code, body=resp.text[:500])
                resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    # --- Contacts ---

    async def check_contact(self, phone_e164: str) -> bool | None:
        """Best-effort WhatsApp existence check. Some Meta accounts require
        a Solutions Partner; this stub uses a lightweight message-dry-run style call.
        For MVP we simply return None when unknown."""
        if not self.access_token or not self.phone_number_id:
            return None
        # Real implementation would use /contacts endpoint on on-prem API.
        # Cloud API doesn't expose it publicly; leave as unknown here.
        return None

    # --- Webhook signature ---

    def verify_signature(self, raw_body: bytes, x_hub_signature_256: str | None) -> bool:
        if not self.app_secret or not x_hub_signature_256:
            return False
        if not x_hub_signature_256.startswith("sha256="):
            return False
        expected = hmac.new(self.app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, x_hub_signature_256.split("=", 1)[1])
