"""Manual test: list approved WhatsApp templates, or send one to a number.

Usage (from apps/api, with .env populated):
    poetry run python scripts/send_test_template.py --list
    poetry run python scripts/send_test_template.py --to +905464867775 \
        --template hello_world --lang tr [--params "Bilal" "Acme"]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx

from src.core.config import get_settings
from src.integrations.whatsapp import WhatsAppClient


async def list_templates() -> None:
    s = get_settings()
    if not s.whatsapp_access_token or not s.whatsapp_business_account_id:
        raise SystemExit("WHATSAPP_ACCESS_TOKEN / WHATSAPP_BUSINESS_ACCOUNT_ID not set in .env")
    graph_base = f"https://graph.facebook.com/{s.whatsapp_graph_api_version}"
    url = f"{graph_base}/{s.whatsapp_business_account_id}/message_templates"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {s.whatsapp_access_token}"},
            params={"limit": 50},
        )
        resp.raise_for_status()
        for t in resp.json().get("data", []):
            print(f"{t['status']:10} {t['name']:35} {t['language']:6} {t.get('category', '')}")


async def send(to: str, template: str, lang: str, params: list[str]) -> None:
    components: list[dict[str, Any]] = []
    if params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in params],
            }
        )
    result = await WhatsAppClient().send_template(
        to=to.lstrip("+"), template_name=template, language=lang, components=components
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list templates and exit")
    ap.add_argument("--to", help="recipient in E.164, e.g. +905464867775")
    ap.add_argument("--template", help="approved template name")
    ap.add_argument("--lang", default="tr", help="template language code (default: tr)")
    ap.add_argument("--params", nargs="*", default=[], help="body variable values, in order")
    args = ap.parse_args()

    if args.list:
        asyncio.run(list_templates())
        return
    if not (args.to and args.template):
        ap.error("--to and --template are required unless --list is used")
    asyncio.run(send(args.to, args.template, args.lang, args.params))


if __name__ == "__main__":
    main()
