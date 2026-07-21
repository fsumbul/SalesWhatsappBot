"""WhatsApp Business Cloud API client (Meta Graph v20+).

Handles: template messages, session (free-form) messages, contact existence
checks, webhook signature verification.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, cast

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.config import get_settings

logger = structlog.get_logger(__name__)

_BASE = "https://graph.facebook.com/v20.0"


class WhatsAppClient:
    def __init__(
        self,
        *,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        app_secret: str | None = None,
    ) -> None:
        s = get_settings()
        self.access_token = access_token or s.whatsapp_access_token
        self.phone_number_id = phone_number_id or s.whatsapp_phone_number_id
        self.app_secret = app_secret or s.whatsapp_app_secret

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
        body = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body_text, "preview_url": preview_url},
        }
        return await self._post_message(body)

    async def _post_message(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.access_token or not self.phone_number_id:
            raise RuntimeError("WhatsApp credentials not configured")
        url = f"{_BASE}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
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
