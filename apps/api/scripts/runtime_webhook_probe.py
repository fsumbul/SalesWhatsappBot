"""Probe the public signed WhatsApp webhook without creating a customer message."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import get_settings


def _payload(waba_id: str, phone_number_id: str) -> bytes:
    body: dict[str, Any] = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            # A nonexistent status id exercises authentication,
                            # sender binding, RLS and parsing without sending or
                            # creating a customer-visible message.
                            "statuses": [
                                {
                                    "id": "wamid.runtime-preflight-probe",
                                    "status": "sent",
                                    "timestamp": str(int(time.time())),
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(body, separators=(",", ":")).encode()


async def _probe(base_url: str, tenant_slug: str) -> dict[str, object]:
    settings = get_settings()
    raw = _payload(
        settings.whatsapp_business_account_id,
        settings.whatsapp_phone_number_id,
    )
    signature = hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    endpoint = f"{base_url.rstrip('/')}/webhooks/whatsapp/{tenant_slug}"
    last_error_type = "unknown"
    last_status: int | None = None
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=8, read=15, write=8, pool=5),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    endpoint,
                    content=raw,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature-256": f"sha256={signature}",
                    },
                )
            last_status = response.status_code
            if response.status_code == 200 and response.json() == {"ok": True}:
                return {"status": "ok", "http_status": 200, "attempt": attempt}
            if response.status_code < 500:
                break
            last_error_type = "server-error"
        except (httpx.HTTPError, ValueError) as exc:
            last_error_type = type(exc).__name__
        if attempt < 3:
            await asyncio.sleep(1)
    return {
        "status": "failed",
        "http_status": last_status,
        "error_type": last_error_type,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://api.ashiraai.com")
    parser.add_argument("--tenant-slug", default="kasnak")
    args = parser.parse_args()
    result = asyncio.run(_probe(args.base_url, args.tenant_slug))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result.get("status") == "ok" else 1)


if __name__ == "__main__":
    main()
