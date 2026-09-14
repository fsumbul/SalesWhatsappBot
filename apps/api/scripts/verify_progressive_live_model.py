"""Real local API + model acceptance, using synthetic data and never messaging Meta."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx


async def verify(base: str, account_file: Path) -> None:
    if urlparse(base).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit(
            "This acceptance creates synthetic records and is restricted to a local API"
        )
    account = json.loads(account_file.read_text())
    async with httpx.AsyncClient(base_url=base.rstrip("/") + "/api/v1/", timeout=180) as client:

        async def call(path, body=None, method=None):
            response = await client.request(
                method or ("POST" if body is not None else "GET"), path, json=body
            )
            response.raise_for_status()
            return response.json()

        login = await call(
            "auth/login",
            {
                "tenant_slug": account["slug"],
                "email": account["email"],
                "password": account["password"],
            },
        )
        client.headers["Authorization"] = "Bearer " + login["access_token"]
        sid = (await call("admin-chat/sessions", {}))["id"]
        path = f"admin-chat/sessions/{sid}"

        async def start(kind, fields):
            return await call(
                path + "/workflows",
                {"kind": kind, "fields": fields, "client_operation_id": str(uuid4())},
            )

        async def act(row, action):
            return await call(
                path + f"/workflows/{row['id']}/actions",
                {
                    "action": action,
                    "fields": {},
                    "expected_revision": row["revision"],
                    "client_operation_id": str(uuid4()),
                },
            )

        row = await start(
            "create_agent",
            {"name": "Model Kabul Metal", "slug": "model-acceptance-" + uuid4().hex[:10]},
        )
        row = await act(await act(row, "continue"), "complete")
        aid = row["result"]["agent_id"]
        instruction = "Şirketimizin adı Model Kabul Metal. Endüstriyel metal parça üretimi yapıyoruz. Bu bilgileri asistana ekle."
        turn = await call(path + "/turns", {"text": instruction, "client_message_id": str(uuid4())})
        assert turn["response_source"] == "model", turn["response_source"]
        row = turn["workflows"][-1]
        assert row["kind"] == "configure", row["kind"]
        print(json.dumps({"step": "natural configuration intent", "passed": True}), flush=True)
        row = await act(row, "continue")
        assert row["status"] == "ready", {"status": row["status"], "errors": row["errors"]}
        versions = await call(f"agents/{aid}/versions")
        assert not versions[0]["company_config"].get(
            "facts"
        ), "Builder wrote facts before acceptance"
        row = await act(row, "complete")
        assert row["result"]["outcome"] == "draft_saved"
        versions = await call(f"agents/{aid}/versions")
        draft = versions[0]
        assert draft["status"] == "draft"
        approved_text = {
            text
            for fact in draft["company_config"]["facts"]
            for text in fact.get("customer_text", {}).values()
        }
        assert any("metal parça" in text for text in approved_text), approved_text
        print(
            json.dumps({"step": "real builder preview and draft acceptance", "passed": True}),
            flush=True,
        )
        test_ids = []
        for _ in range(2):
            row = await start(
                "test", {"version": draft["id"], "content": "Hangi hizmetleri sunuyorsunuz?"}
            )
            row = await act(await act(row, "continue"), "complete")
            assert row["status"] == "completed", row["output"]
            assert row["output"]["response_source"] == "model" and not row["output"].get(
                "used_fallback"
            )
            assert row["output"]["reply"] in approved_text, row["output"]["reply"]
            test_ids.append(row["result"]["test_id"])
        assert test_ids[0] == test_ids[1]
        history = await call(f"agents/{aid}/test-sessions")
        assert len(history) == 1 and len(history[0]["messages"]) == 4
        print(
            json.dumps({"step": "real customer model and persistent test history", "passed": True}),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "passed": True,
                    "session_id": sid,
                    "agent_id": aid,
                    "Meta": "not called",
                    "publication": "not performed",
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:58010")
    parser.add_argument(
        "--account-file", type=Path, required=True
    )
    args = parser.parse_args()
    asyncio.run(verify(args.api_url, args.account_file))
