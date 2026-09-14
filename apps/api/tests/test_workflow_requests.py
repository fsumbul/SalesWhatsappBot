"""Shared request records retain canonical search, immutable snapshots and tenant scope."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.core.db import session_scope
from src.modules.selection.models import SelectionRequest
from tests.test_company_workspace import client as client_fixture
from tests.test_progressive_inbox import seeded, views
from tests.test_progressive_workflows import act, start

client = client_fixture


async def test_request_pages_filters_snapshot_and_conversation(client, monkeypatch):
    headers, tid, _, sid, cid, _ = await seeded(client, monkeypatch)
    definition = {"steps": [{"id": "contact_name", "label": "Müşteri"}]}
    async with session_scope(tid) as db:
        for index in range(25):
            db.add(
                SelectionRequest(
                    tenant_id=tid,
                    conversation_id=cid,
                    definition=definition,
                    answers={"contact_name": {"value": "Deniz"}},
                    status="waiting_review",
                    confirmed_snapshot={
                        "answers": {"contact_name": {"value": "Onaylı Deniz"}},
                        "fields": definition["steps"],
                    }
                    if index == 0
                    else None,
                    created_at=datetime.now(UTC) - timedelta(days=2 if index else 0),
                )
            )
        await db.commit()
    response = await start(client, headers, sid, "records", {"category": "requests", "q": "Deniz"})
    assert response.status_code == 200, response.text
    row = response.json()
    assert len(row["records"]) == 20 and row["has_more"]
    second = (await act(client, headers, row, "continue", {"page": "2"})).json()
    assert len(second["records"]) == 5 and not second["has_more"]
    assert not ({r["id"] for r in row["records"]} & {r["id"] for r in second["records"]})
    today = (await act(client, headers, second, "continue", {"today": "true"})).json()
    assert len(today["records"]) == 1
    assert today["records"][0]["title"] == "Onaylı Deniz"
    quote = (await start(client, headers, sid, "records", {"category": "quotes"})).json()
    assert len(quote["records"]) == 1
    assert "fiyat veya uygunluk onayı değildir" in quote["records"][0]["details"]["Kapsam"]
    assert (await act(client, headers, quote, "continue", {"status": "invalid"})).status_code == 422
    missing = (await act(client, headers, quote, "continue", {"status": "completed"})).json()
    assert not missing["records"]
    quote = (await act(client, headers, missing, "continue", {"status": ""})).json()
    launched = await act(
        client,
        headers,
        quote,
        "launch",
        {"operation": "conversation", "record": quote["records"][0]["id"]},
    )
    assert launched.status_code == 200, launched.text
    assert (await views(client, headers, sid))[-1]["fields"]["conversation"] == str(cid)
    # Arbitrary identifiers never become conversation targets.
    quote = next(r for r in await views(client, headers, sid) if r["id"] == quote["id"])
    assert (
        await act(
            client, headers, quote, "launch", {"operation": "conversation", "record": str(uuid4())}
        )
    ).status_code == 404


async def test_natural_request_list_routes_to_shared_card(client, monkeypatch):
    from tests.test_chat_workspace import model, turn

    headers, _, _, sid, _, _ = await seeded(client, monkeypatch)
    model(monkeypatch, {"tool": "search", "status": "waiting_review", "today": True})
    response = await turn(client, headers, sid, "Bugün gelen bekleyen talepleri göster")
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    row = response.json()["workflows"][-1]
    assert row["fields"]["category"] == "requests"
    assert row["fields"]["today"] == "true"
    assert row["fields"]["status"] == "waiting_review"
    model(monkeypatch, {"tool": "quotes"})
    response = await turn(client, headers, sid, "Teklif taleplerini göster")
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    assert response.json()["workflows"][-1]["fields"]["category"] == "quotes"


async def test_request_records_do_not_cross_tenants_and_allow_full_search_length(
    client, monkeypatch
):
    from tests.test_chat_workspace import chat
    from tests.test_company_workspace import account

    headers, tid, _, sid, cid, _ = await seeded(client, monkeypatch)
    async with session_scope(tid) as db:
        request = SelectionRequest(
            tenant_id=tid,
            conversation_id=cid,
            definition={"steps": []},
            answers={},
            status="waiting_review",
        )
        db.add(request)
        await db.commit()
    response = await start(
        client, headers, sid, "records", {"category": "requests", "q": "x" * 160}
    )
    assert response.status_code == 200, response.text
    assert not response.json()["records"]
    other, _ = await account(client)
    other_sid = await chat(client, other)
    response = await start(client, other, other_sid, "records", {"category": "requests"})
    assert response.status_code == 200, response.text
    assert not response.json()["records"]
    response = await act(
        client,
        other,
        response.json(),
        "launch",
        {"operation": "conversation", "record": str(request.id)},
    )
    assert response.status_code == 404, response.text


async def test_request_review_actions_receipt_revision_and_no_customer_mutation(
    client, monkeypatch
):
    from uuid import UUID

    headers, tid, uid, sid, cid, _ = await seeded(client, monkeypatch)
    snapshot = {"answers": {"contact_name": {"value": "Deniz"}}}
    async with session_scope(tid) as db:
        request = SelectionRequest(
            tenant_id=tid,
            conversation_id=cid,
            definition={"steps": []},
            answers={},
            confirmed_snapshot=snapshot,
            status="waiting_review",
        )
        db.add(request)
        await db.commit()
        rid = request.id

    async def review(operation, fields):
        response = await start(
            client, headers, sid, "request_update", {"request": str(rid), "operation": operation}
        )
        assert response.status_code == 200, response.text
        response = await act(client, headers, response.json(), "continue", fields)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ready", response.text
        return response.json()

    row = await review("status", {"status": "in_review"})
    operation = uuid4()
    saved = await act(client, headers, row, "complete", mid=operation)
    assert saved.status_code == 200, saved.text
    assert (await act(client, headers, row, "complete", mid=operation)).json() == saved.json()
    same = await review("status", {"status": "in_review"})
    unchanged = await act(client, headers, same, "complete")
    assert unchanged.json()["result"]["outcome"] == "no_change"
    row = await review("assign", {"assignee": str(uid)})
    assert (await act(client, headers, row, "complete")).status_code == 200
    row = await review("note", {"note": "Teknik ekip inceleyecek."})
    async with session_scope(tid) as db:
        current = await db.get(SelectionRequest, rid)
        current.revision += 1
        await db.commit()
    assert (await act(client, headers, row, "complete")).status_code == 409
    row = (await act(client, headers, row, "back")).json()
    assert not row["changes"] and row["fields"]["note"] == "Teknik ekip inceleyecek."
    row = (await act(client, headers, row, "continue")).json()
    operation = uuid4()
    saved = await act(client, headers, row, "complete", mid=operation)
    assert saved.status_code == 200, saved.text
    assert (await act(client, headers, row, "complete", mid=operation)).json() == saved.json()
    async with session_scope(tid) as db:
        current = await db.get(SelectionRequest, UUID(str(rid)))
        assert current.status == "in_review" and current.assigned_to == uid
        assert current.confirmed_snapshot == snapshot and current.answers == {}
        assert sum(n.get("text") == "Teknik ekip inceleyecek." for n in current.internal_notes) == 1
        assert len(current.internal_notes) == 3


async def test_request_review_empty_note_and_draft_cannot_apply(client, monkeypatch):
    headers, tid, _, sid, cid, _ = await seeded(client, monkeypatch)
    async with session_scope(tid) as db:
        request = SelectionRequest(
            tenant_id=tid, conversation_id=cid, definition={"steps": []}, answers={}, status="draft"
        )
        db.add(request)
        await db.commit()
    response = await start(
        client, headers, sid, "request_update", {"request": str(request.id), "operation": "note"}
    )
    assert response.status_code == 200, response.text
    row = (await act(client, headers, response.json(), "continue")).json()
    assert row["status"] == "awaiting_input" and "note" in row["errors"]
    row = (await act(client, headers, row, "continue", {"note": "İncele"})).json()
    assert (await act(client, headers, row, "complete")).status_code == 409


async def test_natural_review_opens_shared_form_without_early_write(client, monkeypatch):
    from tests.test_chat_workspace import model, turn

    headers, tid, _, sid, cid, _ = await seeded(client, monkeypatch)
    async with session_scope(tid) as db:
        request = SelectionRequest(
            tenant_id=tid,
            conversation_id=cid,
            definition={"steps": []},
            answers={},
            status="waiting_review",
        )
        db.add(request)
        await db.commit()
    model(monkeypatch, {"tool": "status", "target": str(request.id), "status": "in_review"})
    response = await turn(client, headers, sid, f"{request.id} talebini incelemeye al")
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    row = response.json()["workflows"][-1]
    assert row["kind"] == "request_update" and row["step"] == "details"
    assert row["fields"]["status"] == "in_review"
    async with session_scope(tid) as db:
        assert (await db.get(SelectionRequest, request.id)).status == "waiting_review"
    row = (await act(client, headers, row, "continue")).json()
    assert (await act(client, headers, row, "complete")).status_code == 200
    # No active request means a pronoun cannot silently select the completed card.
    model(monkeypatch, {"tool": "note", "note": "Teknik inceleme"})
    response = await turn(client, headers, sid, "Teknik inceleme not ekle")
    assert response.status_code == 200, response.text
    assert "İşlem yapılmadı" in response.json()["reply"]
    assert response.json()["workflows"][-1]["kind"] == "records"


async def test_natural_assignee_resolution_uses_current_tenant(client, monkeypatch):
    from src.modules.auth.models import User
    from tests.test_chat_workspace import model, turn

    headers, tid, uid, sid, cid, _ = await seeded(client, monkeypatch)
    async with session_scope(tid) as db:
        user = await db.get(User, uid)
        email = user.email
        request = SelectionRequest(
            tenant_id=tid,
            conversation_id=cid,
            definition={"steps": []},
            answers={},
            status="waiting_review",
        )
        db.add(request)
        await db.commit()
    for assignee, expected in [(email, str(uid)), ("missing@example.com", None)]:
        model(monkeypatch, {"tool": "assign", "target": str(request.id), "assignee": assignee})
        response = await turn(client, headers, sid, f"{request.id} talebine {assignee} ata")
        assert response.status_code == 200, response.text
        row = response.json()["workflows"][-1]
        assert row["kind"] == "request_update"
        assert row["fields"].get("assignee") == expected
    async with session_scope(tid) as db:
        assert (await db.get(SelectionRequest, request.id)).assigned_to is None


async def test_request_details_files_missing_and_natural_read(client, monkeypatch):
    from hashlib import sha256

    from sqlalchemy import select

    from src.modules.outreach.models import Message
    from src.modules.selection.models import SelectionFile
    from tests.test_chat_workspace import model, turn

    headers, tid, _, sid, cid, _ = await seeded(client, monkeypatch)
    definition = {
        "steps": [{"id": "contact_name", "label": "Müşteri"}, {"id": "diameter", "label": "Çap"}]
    }
    async with session_scope(tid) as db:
        request = SelectionRequest(
            tenant_id=tid,
            conversation_id=cid,
            definition=definition,
            answers={},
            confirmed_snapshot={
                "answers": {"contact_name": {"value": "Deniz"}, "diameter": {"status": "unknown"}},
                "fields": definition["steps"],
            },
            status="waiting_review",
            internal_notes=[{"text": "Teknik ekip ölçü bekliyor."}],
        )
        db.add(request)
        await db.flush()
        inbound = await db.scalar(
            select(Message).where(Message.conversation_id == cid, Message.direction == "inbound")
        )
        content = b"%PDF-1.4\nsynthetic drawing fixture"
        file = SelectionFile(
            tenant_id=tid,
            request_id=request.id,
            inbound_message_id=inbound.id,
            filename="drawing.pdf",
            mime_type="application/pdf",
            sha256=sha256(content).hexdigest(),
            content=content,
            size_bytes=len(content),
        )
        db.add(file)
        await db.commit()
    listing = (await start(client, headers, sid, "records", {"category": "requests"})).json()
    response = await act(
        client,
        headers,
        listing,
        "launch",
        {"operation": "request_details", "record": str(request.id)},
    )
    assert response.status_code == 200, response.text
    detail = (await views(client, headers, sid))[-1]
    assert detail["fields"]["category"] == "request_details"
    assert detail["records"][0]["details"]["Eksik bilgiler"] == "Çap"
    assert detail["records"][0]["details"]["İç notlar"] == "Teknik ekip ölçü bekliyor."
    assert detail["records"][1]["file"] == {"request_id": str(request.id), "file_id": str(file.id)}
    downloaded = await client.get(
        f"/api/v1/selection-requests/{request.id}/files/{file.id}", headers=headers
    )
    assert downloaded.status_code == 200 and downloaded.content == content
    model(monkeypatch, {"tool": "missing"})
    response = await turn(client, headers, sid, "Bu talepte ne eksik?")
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    assert response.json()["workflows"][-1]["records"][0]["details"]["Eksik bilgiler"] == "Çap"
    model(monkeypatch, {"tool": "conversation"})
    response = await turn(client, headers, sid, "Konuşmayı göster")
    assert response.status_code == 200, response.text
    assert response.json()["workflows"][-1]["kind"] == "conversation"


async def test_natural_delivery_uses_shared_request_then_conversation_context(client, monkeypatch):
    from src.modules.outreach.models import Message
    from tests.test_chat_workspace import model, turn

    headers, tid, _, sid, cid, _ = await seeded(client, monkeypatch)
    async with session_scope(tid) as db:
        request = SelectionRequest(
            tenant_id=tid,
            conversation_id=cid,
            definition={"steps": []},
            answers={},
            status="waiting_review",
        )
        message = Message(
            tenant_id=tid,
            conversation_id=cid,
            direction="outbound",
            body="Yanıt",
            raw={"manual_send_state": "sent"},
        )
        db.add_all([request, message])
        await db.commit()
    response = await start(
        client, headers, sid, "records", {"category": "request_details", "request": str(request.id)}
    )
    assert response.status_code == 200, response.text
    assert "henüz doğrulanmadı" in response.json()["records"][0]["details"]["Son mesaj"]
    async with session_scope(tid) as db:
        current = await db.get(Message, message.id)
        current.raw = {**current.raw, "delivery_status": "delivered"}
        await db.commit()
    model(monkeypatch, {"tool": "delivery"})
    response = await turn(client, headers, sid, "Son mesaj ulaştı mı?")
    assert response.status_code == 200, response.text
    assert response.json()["cards"] == []
    row = response.json()["workflows"][-1]
    assert row["records"][0]["details"]["Son mesaj"] == "Son mesaj teslim edildi."
    await start(client, headers, sid, "conversation", {"conversation": str(cid)})
    response = await turn(client, headers, sid, "Son mesaj ulaştı mı?")
    assert response.status_code == 200, response.text
    assert response.json()["workflows"][-1]["kind"] == "conversation"
    assert "Son mesaj teslim edildi." in response.json()["workflows"][-1]["output"]["delivery_note"]
