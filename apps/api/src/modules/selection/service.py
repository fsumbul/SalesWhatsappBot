# ruff: noqa: RUF001
"""Transactional workflow adapter; called inside the durable worker transaction."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select

from src.core.config import get_settings
from src.modules.agents.company_runtime import (
    CustomerReplyAction,
    RuntimeInteraction,
    RuntimeInteractionKind,
    RuntimeInteractionOption,
    RuntimeTurn,
)

from .engine import State, advance, normalize, prompt, reduce
from .models import SelectionEvent, SelectionFile, SelectionRequest
from .schema import SelectionFlow

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_FILES = 10


def enabled(config: Any, conversation_id: Any) -> bool:
    # The published company configuration is the rollout authority for every
    # conversation. Installation-wide pilot IDs must not split a company's flow.
    return config.selection_flow is not None


def starts(config: Any, body: str) -> bool:
    definition = config.selection_flow
    n = normalize(body).strip(" !.,:;?")
    return bool(
        definition
        and (
            intake_mode(body) in {"chat", "form"}
            or n in definition.start_phrases + definition.greeting_phrases
            or (
                config.whatsapp_presentation
                and n in config.whatsapp_presentation.intake_start_phrases
            )
            or "fact_request:quote_product_question" in n
        )
    )


def intake_mode(body: str) -> str | None:
    clean = normalize(body).strip()
    for mode in ("form", "chat"):
        token = "intake:" + mode
        if clean == token or clean.endswith("[" + token + "]"):
            return mode
    return None


async def applicable(session: Any, config: Any, conversation: Any, inbound: Any) -> bool:
    if (inbound.body or "").strip() == "[flow_response]":
        return False
    if not enabled(config, conversation.id):
        return False
    if starts(config, inbound.body or "") or "sel:" in (inbound.body or ""):
        return True
    return (
        await session.scalar(
            select(SelectionRequest.id)
            .where(
                SelectionRequest.conversation_id == conversation.id,
                SelectionRequest.status == "draft",
            )
            .limit(1)
        )
    ) is not None


def as_turn(response: dict[str, Any]) -> RuntimeTurn:
    options = tuple(RuntimeInteractionOption(**x) for x in response.get("options", []))
    # List title supports 24 chars; reply buttons are limited to 20.
    kind = (
        RuntimeInteractionKind.REPLY_BUTTONS
        if len(options) <= 3 and all(len(o.title) <= 20 for o in options)
        else RuntimeInteractionKind.LIST
    )
    interaction = (
        RuntimeInteraction(
            kind=kind, button_text="Seçenekler", options=options, section_title="Seçiminiz"
        )
        if options
        else None
    )
    if response.get("form_url"):
        interaction = RuntimeInteraction(
            kind=RuntimeInteractionKind.CTA_URL, button_text="Formu aç", url=response["form_url"]
        )
    return RuntimeTurn(
        action=CustomerReplyAction.REPLY,
        reply=response["body"],
        fact_ids=(),
        interaction=interaction,
        response_source="guided",
    )


def state_of(row: Any) -> State:
    return State(
        row.id.hex,
        SelectionFlow.model_validate(row.definition),
        dict(row.answers),
        row.step_index,
        row.revision,
        row.status,
    )


async def download_media(raw: dict[str, Any]) -> tuple[str, str, bytes]:
    """Fetch only Meta-owned media, bounded in memory. Never trust caption or filename."""
    kind = str(raw.get("type", ""))
    obj = raw.get(kind, {})
    media_id = obj.get("id", "")
    if not isinstance(media_id, str) or not re.fullmatch(r"\d+", media_id):
        raise ValueError("Missing media ID")
    settings = get_settings()
    headers = {"Authorization": "Bearer " + settings.whatsapp_access_token}
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        meta = await client.get(
            f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}/{media_id}",
            headers=headers,
        )
        if not meta.is_success:
            raise ValueError("Media metadata unavailable")
        info = meta.json()
        mime = info.get("mime_type")
        if (
            mime not in {"application/pdf", "image/jpeg", "image/png"}
            or int(info.get("file_size", 0)) > MAX_FILE_BYTES
        ):
            raise ValueError("Unsupported file")
        url = info.get("url", "")
        parts = urlparse(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in ("facebook.com", "fbcdn.net", "fbsbx.com")
        ):
            raise ValueError("Unexpected media host")
        content = bytearray()
        async with client.stream("GET", url, headers=headers) as response:
            if not response.is_success:
                raise ValueError("Media unavailable")
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("File too large")
    signatures = {
        "application/pdf": b"%PDF-",
        "image/jpeg": b"\xff\xd8\xff",
        "image/png": b"\x89PNG\r\n\x1a\n",
    }
    if not content.startswith(signatures[mime]):
        raise ValueError("File signature mismatch")
    name = PurePath(str(obj.get("filename", "dosya"))).name
    name = re.sub(r"[^\w. -]", "_", name)[:150] or "dosya"
    suffix = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[mime]
    if not name.lower().endswith(suffix):
        name += suffix
    return name, mime, bytes(content)


async def handle(
    session: Any, config: Any, conversation: Any, inbound: Any
) -> tuple[RuntimeTurn | None, dict[str, Any] | None, SelectionRequest | None, bool]:
    """Handled turn, side-question resume prompt, request, newly confirmed flag."""
    prior = await session.scalar(
        select(SelectionEvent).where(SelectionEvent.inbound_message_id == inbound.id)
    )
    if prior:
        row = await session.get(SelectionRequest, prior.request_id)
        return as_turn(prior.response), None, row, False
    row = await session.scalar(
        select(SelectionRequest)
        .where(
            SelectionRequest.conversation_id == conversation.id, SelectionRequest.status == "draft"
        )
        .with_for_update()
    )
    mode = intake_mode(inbound.body or "")
    presentation = config.whatsapp_presentation
    offer_choice = bool(
        presentation and presentation.offer_intake_choice and presentation.intake_form_url
    )
    entry = offer_choice and (
        mode is not None
        or (
            starts(config, inbound.body or "")
            and normalize(inbound.body or "").strip(" !.,:;?")
            not in config.selection_flow.greeting_phrases
        )
    )
    if entry and row is not None and mode is None:
        current = state_of(row)
        if current.step_index < len(current.definition.steps):
            step = current.definition.steps[current.step_index]
            if any(
                normalize(inbound.body or "") in {normalize(o.value), normalize(o.label)}
                for o in step.options
            ):
                entry = False
    definition_changed = bool(
        row and row.definition != config.selection_flow.model_dump(mode="json")
    )
    if definition_changed:
        # Preserve the old draft as an archive. Its values/IDs must not be
        # interpreted as answers to a different published workflow.
        row.status = "superseded"
        await session.flush()
        row = None
    if row is None:
        if "sel:" in (inbound.body or "") and not definition_changed:
            return (
                as_turn(
                    {
                        "body": "Bu seçim artık aktif değil. Yeni talep için: "
                        + config.selection_flow.start_phrases[0],
                        "options": [],
                    }
                ),
                None,
                None,
                False,
            )
        if not definition_changed and not starts(config, inbound.body or ""):
            return None, None, None, False
        previous = await session.scalar(
            select(SelectionRequest)
            .where(SelectionRequest.conversation_id == conversation.id)
            .order_by(SelectionRequest.created_at.desc())
            .limit(1)
        )
        known_name = (previous.answers or {}).get("contact_name") if previous else None
        row = SelectionRequest(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            definition=config.selection_flow.model_dump(mode="json"),
            answers={"contact_name": known_name} if known_name else {},
            step_index=0,
            revision=0,
            status="draft",
        )
        session.add(row)
        await session.flush()
        state = state_of(row)
        advance(state)
        response = prompt(state)
        confirmed = False
    else:
        state = state_of(row)
        if entry:
            response, confirmed = prompt(state), False
        elif inbound.raw.get("type") in {"image", "document"}:
            try:
                count = await session.scalar(
                    select(func.count())
                    .select_from(SelectionFile)
                    .where(SelectionFile.request_id == row.id)
                )
                if count >= MAX_REQUEST_FILES:
                    raise ValueError("attachment_limit")
                name, mime, content = await download_media(inbound.raw)
                file = SelectionFile(
                    tenant_id=row.tenant_id,
                    request_id=row.id,
                    inbound_message_id=inbound.id,
                    filename=name,
                    mime_type=mime,
                    sha256=hashlib.sha256(content).hexdigest(),
                    content=content,
                    size_bytes=len(content),
                )
                session.add(file)
                await session.flush()
                response = prompt(state, prefix="Dosyanız talebe eklendi; teknik ekip inceleyecek.")
            except (ValueError, httpx.HTTPError, TypeError) as exc:
                response = prompt(
                    state,
                    prefix=(
                        "Talebe en fazla 10 dosya eklenebilir. Mevcut dosyalarla veya manuel devam edebilirsiniz."
                        if str(exc) == "attachment_limit"
                        else "Dosya alınamadı. En fazla 5 MB PDF/JPEG/PNG gönderin veya manuel devam edin."
                    ),
                )
            confirmed = False
        else:
            reduced, confirmed = reduce(state, inbound.body or "")
            if reduced is None:
                return None, prompt(state), row, False
            response = reduced
    files = list(
        (
            await session.execute(select(SelectionFile).where(SelectionFile.request_id == row.id))
        ).scalars()
    )
    if not files and state.answers.get("drawing", {}).get("value") == "done":
        state = state_of(row)
        response = prompt(
            state, prefix="Henüz dosya eklenmedi. Dosya gönderin veya Manuel devam seçin."
        )
        confirmed = False
    if state.step_index >= len(state.definition.steps) and state.status == "draft":
        response["body"] += "\nDosyalar: " + (
            ", ".join(f.filename for f in files) if files else "Eklenmedi"
        )
        if len(response["body"]) > 1024:
            response["options"] = []
            if "Onayla, düzenle veya iptal yazın." not in response["body"]:
                response["body"] += "\nOnayla, düzenle veya iptal yazın."
    row.answers = state.answers
    row.step_index = state.step_index
    row.revision = state.revision
    row.status = state.status
    if confirmed:
        row.confirmed_at = datetime.now(UTC)
        row.confirmed_snapshot = {
            "flow_id": state.definition.id,
            "version": state.definition.version,
            "answers": state.answers,
            "fields": [
                {"id": s.id, "label": s.label}
                for s in state.definition.steps
                if s.id in state.answers
            ],
            "files": [
                {
                    "id": str(f.id),
                    "filename": f.filename,
                    "sha256": f.sha256,
                    "size_bytes": f.size_bytes,
                }
                for f in files
            ],
        }
        response["body"] += f"\nTalep no: {str(row.id)[:8]}"
    if entry and mode != "chat":
        if mode == "form":
            from .access import form_token

            response = {
                "body": "Talebinizi Ashiraai formunda doldurabilirsiniz. Buradaki yanıtlarınız aynı kayıtta saklanır; WhatsApp’tan devam edebilirsiniz.",
                "options": [],
                "form_url": str(presentation.intake_form_url) + "#token=" + form_token(row),
            }
        else:
            response = {
                "body": "Teklif bilgilerinizi nasıl iletmek istersiniz? İki yöntem de aynı talep kaydına kaydedilir.",
                "options": [
                    {"id": "intake:form", "title": "Formu doldur"},
                    {"id": "intake:chat", "title": "Sohbetle ilerle"},
                ],
            }
    session.add(
        SelectionEvent(
            tenant_id=row.tenant_id,
            request_id=row.id,
            inbound_message_id=inbound.id,
            response=response,
            kind="confirmed" if confirmed else "answer",
        )
    )
    await session.flush()
    return as_turn(response), None, row, confirmed


def preview_selection(
    config: Any, saved: dict[str, Any] | None, body: str
) -> tuple[RuntimeTurn | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Use the same pure reducer in a web test, without customers, files or Meta.

    The test session pins the configuration revision. This state is never a real
    SelectionRequest and cannot create an operational task or outbound message.
    """
    from uuid import uuid4

    if config.selection_flow is None:
        return None, None, None
    response: dict[str, Any] | None
    state = (
        State(
            saved["id"],
            config.selection_flow,
            dict(saved["answers"]),
            saved["step_index"],
            saved["revision"],
            saved["status"],
        )
        if saved
        else None
    )
    if state is None or state.status != "draft":
        if not starts(config, body):
            return None, saved, None
        state = State(uuid4().hex, config.selection_flow, {})
        response = prompt(state)
    else:
        response, _ = reduce(state, body)
    saved = {
        "id": state.id,
        "answers": state.answers,
        "step_index": state.step_index,
        "revision": state.revision,
        "status": state.status,
    }
    return (as_turn(response), saved, None) if response else (None, saved, prompt(state))
