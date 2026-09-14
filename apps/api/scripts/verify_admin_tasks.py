"""Local API + real configured model + disposable PostgreSQL acceptance, never sends.

Set DATABASE_URL to the migrated local test database and run from apps/api with PYTHONPATH=.
The generated account can also be used by apps/web/e2e/admin_tasks.py.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from src import models_registry  # noqa: F401
from src.core.config import get_settings
from src.core.db import dispose_engine, session_scope
from src.modules.admin_chat.models import AdminChatTurn
from src.modules.auth.schemas import TenantRegisterIn
from src.modules.auth.service import AuthService
from src.modules.discovery.models import Lead, LeadContact
from src.modules.outreach.models import Conversation, Message


async def main(args):
    if urlparse(args.api).hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Only a local test API is allowed")
    database = make_url(str(get_settings().database_url))
    if database.host not in {"localhost", "127.0.0.1"} or database.database != "leadpulse_test":
        raise SystemExit("Only the disposable local leadpulse_test database is allowed")
    password = "Synthetic-task-" + uuid4().hex
    account = {
        "slug": "task-e2e-" + uuid4().hex[:12],
        "email": "owner@example.com",
        "password": password,
    }
    async with session_scope() as db:
        tenant, user = await AuthService(db).register_tenant(
            TenantRegisterIn(
                tenant_name="Task acceptance",
                tenant_slug=account["slug"],
                admin_email=account["email"],
                admin_password=password,
            )
        )
        tid, uid = tenant.id, user.id
        await db.commit()
    async with session_scope(tid) as db:
        lead = Lead(
            tenant_id=tid,
            person_name="Deniz",
            company_name="Synthetic",
            normalized_name="synthetic",
            source="test",
            discovered_at=datetime.now(UTC),
        )
        db.add(lead)
        await db.flush()
        contact = LeadContact(
            tenant_id=tid,
            lead_id=lead.id,
            type="phone",
            raw_value="+15550102030",
            normalized_value="+15550102030",
        )
        db.add(contact)
        await db.flush()
        conv = Conversation(tenant_id=tid, lead_id=lead.id, contact_id=contact.id)
        db.add(conv)
        await db.flush()
        db.add_all(
            [
                Message(
                    tenant_id=tid,
                    conversation_id=conv.id,
                    direction="inbound",
                    body="Döküm kasnak kataloğunu rica ediyorum.",
                ),
                Message(
                    tenant_id=tid,
                    conversation_id=conv.id,
                    direction="inbound",
                    body="Ölçü çizimini yarın paylaşacağım.",
                ),
                Message(
                    tenant_id=tid,
                    conversation_id=conv.id,
                    direction="outbound",
                    body="Çiziminizi bekliyoruz.",
                    raw={"delivery_status": "read"},
                ),
            ]
        )
        await db.commit()
    args.account_file.write_text(json.dumps(account), encoding="utf-8")
    args.account_file.chmod(0o600)
    if args.seed_only:
        print(json.dumps({"seeded": True, "account_file": str(args.account_file)}))
        await dispose_engine()
        return
    cases = [
        ("+1 (555) 010-2030 yazdıklarını getir. ne konuşmuş", 1),
        ("Deniz ne konuşmuş, son mesajımız okunmuş mu? Ayrıca toplam kaç teknik talep var?", 3),
        ("Deniz ne yazmış? Ayrıca Ece adlı müşteriyi ece@example.com adresiyle ekle.", 2),
        ("Güncel stok miktarımız kaç?", 1),
    ]
    if args.case is not None:
        cases = [cases[args.case]]
    async with httpx.AsyncClient(base_url=args.api + "/api/v1/", timeout=300) as client:
        login = await client.post(
            "auth/login",
            json={"tenant_slug": account["slug"], "email": account["email"], "password": password},
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        for question, expected in cases:
            sid = (await client.post("admin-chat/sessions", json={})).json()["id"]
            mid = str(uuid4())
            response = await client.post(
                f"admin-chat/sessions/{sid}/turns",
                json={"text": question, "client_message_id": mid},
            )
            response.raise_for_status()
            body = response.json()
            async with session_scope(tid) as db:
                await db.execute(
                    text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(uid)}
                )
                turn = await db.scalar(
                    select(AdminChatTurn).where(AdminChatTurn.session_id == UUID(sid))
                )
                print(
                    json.dumps(
                        {"question": question, "reply": body["reply"], "audit": turn.audit},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                assert turn.audit.get("verified"), turn.audit.get("stop_reason")
            outcomes = next(c for c in body["cards"] if c["type"] == "task_result")["outcomes"]
            assert len(outcomes) == expected
            if question == "Güncel stok miktarımız kaç?":
                assert outcomes[0]["status"] == "unsupported"
            else:
                assert all(o["status"] in {"completed", "awaiting_review"} for o in outcomes)
                summary = outcomes[0]["text"].casefold()
                assert "katalo" in summary and "çizim" in summary, "Both customer topics must be covered"
            retry = await client.post(
                f"admin-chat/sessions/{sid}/turns",
                json={"text": question, "client_message_id": mid},
            )
            assert retry.json() == body
    async with session_scope(tid) as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 3
        assert await db.scalar(select(func.count()).select_from(Lead)) == 1  # Ece is only a draft.
    print(
        json.dumps(
            {
                "passed": True,
                "cases": len(cases),
                "model": "actual configured provider",
                "external_sends": 0,
            }
        ),
        flush=True,
    )
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:58010")
    parser.add_argument(
        "--account-file",
        type=Path,
        default=Path("/tmp/sales-admin-task-account.json"),  # noqa: S108 -- synthetic local acceptance account
    )
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--case", type=int, choices=range(4))
    asyncio.run(main(parser.parse_args()))
