# ruff: noqa: RUF001
"""Knowledge workflows inside the admin chat (ADR-003).

``knowledge`` adds a website as a knowledge source (documents are uploaded
from the assistant panel because a chat field cannot carry a PDF);
``knowledge_review`` lets the administrator accept, reject or revoke one
extracted candidate or image. Both follow the shared WorkflowView contract:
server-owned fields and options, an explicit review step, one complete action.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select

from src.modules.agents.service import AgentService
from src.modules.knowledge.crawler import normalize_url
from src.modules.knowledge.models import KnowledgeCandidate, KnowledgeMedia, KnowledgeSource
from src.modules.knowledge.publisher import KnowledgePublisher

from .workflow_schema import WorkflowField

KINDS = {"knowledge", "knowledge_review"}
CONTROLS = {
    "knowledge": [
        WorkflowField(key="agent", label="Asistan", control="select", required=True),
        WorkflowField(key="url", label="Web sitesi adresi (https://...)", required=True),
        WorkflowField(
            key="auto_publish",
            label="Bulunan bilgiler",
            control="select",
            required=True,
            options={
                "true": "Güvenli olanlar otomatik yayınlansın (geri alınabilir)",
                "false": "Hepsi onayımı beklesin",
            },
        ),
    ],
    "knowledge_review": [
        WorkflowField(key="agent", label="Asistan", control="select", required=True),
        WorkflowField(key="item", label="Bilgi veya görsel", control="select", required=True),
        WorkflowField(
            key="decision",
            label="Karar",
            control="select",
            required=True,
            options={
                "accept": "Onayla ve yayınla",
                "reject": "Reddet",
                "revoke": "Yayından kaldır",
            },
        ),
    ],
}
_STATUS_LABELS = {
    "pending": "Onay bekliyor",
    "staged": "Taslakta, gizli",
    "auto_published": "Otomatik yayında",
    "accepted": "Onaylandı",
    "rejected": "Reddedildi",
    "revoked": "Kaldırıldı",
    "published": "Yayında",
}


def _claims(user: Any) -> dict[str, str]:
    return {"tid": str(user.tenant_id), "sub": str(user.id), "role": user.role.value}


async def _agent(db: Any, user: Any, row: Any) -> Any:
    agents = await AgentService(db).list_agents(user.tenant_id)
    selected = next((a for a in agents if row.fields.get("agent") in {a.slug, a.name}), None)
    if selected is None:
        raise HTTPException(422, "Bu şirkette asistan bulunamadı. Listeden seçin.")
    return selected


def _candidate_label(candidate: KnowledgeCandidate) -> str:
    status = _STATUS_LABELS.get(candidate.review_status, candidate.review_status)
    if candidate.kind == "offering":
        return f"[Ürün · {status}] {candidate.payload.get('name', candidate.subject_id)}"
    text = str(candidate.payload.get("customer_text", ""))[:90]
    guard = " · korumalı" if candidate.protected else ""
    return f"[Bilgi · {status}{guard}] {candidate.subject_id}: {text}"


async def _item_choices(db: Any, user: Any, agent_id: UUID) -> dict[str, str]:
    source_ids = list(
        (
            await db.execute(
                select(KnowledgeSource.id).where(
                    KnowledgeSource.tenant_id == user.tenant_id, KnowledgeSource.agent_id == agent_id
                )
            )
        ).scalars()
    )
    if not source_ids:
        return {}
    choices: dict[str, str] = {}
    candidates = (
        await db.execute(
            select(KnowledgeCandidate)
            .where(
                KnowledgeCandidate.source_id.in_(source_ids),
                KnowledgeCandidate.review_status.in_(["pending", "staged", "auto_published", "accepted"]),
            )
            .order_by(KnowledgeCandidate.review_status, KnowledgeCandidate.created_at.desc())
            .limit(60)
        )
    ).scalars()
    for candidate in candidates:
        choices[f"candidate:{candidate.id}"] = _candidate_label(candidate)
    media_rows = (
        await db.execute(
            select(KnowledgeMedia)
            .where(KnowledgeMedia.source_id.in_(source_ids), KnowledgeMedia.status.in_(["pending", "published"]))
            .order_by(KnowledgeMedia.score.desc())
            .limit(30)
        )
    ).scalars()
    for media in media_rows:
        status = _STATUS_LABELS.get(media.status, media.status)
        choices[f"media:{media.id}"] = f"[Görsel · {status}] {media.subject_id or '?'} · {media.width}x{media.height}"
    return choices


async def initialize(db: Any, user: Any, session: Any, row: Any) -> None:
    agents = await AgentService(db).list_agents(user.tenant_id)
    choices = {a.slug: a.name for a in agents}
    selected_id = session.context.get("workspace", {}).get("agent_id")
    selected = next((a for a in agents if str(a.id) == selected_id), None)
    if selected is None and len(agents) == 1:
        selected = agents[0]
    defaults = {"auto_publish": "true"} if row.kind == "knowledge" else {}
    if selected:
        defaults["agent"] = selected.slug
    row.fields = {**defaults, **row.fields}
    row.state = {"choices": {"agent": choices}}
    await refresh(db, user, row)


async def refresh(db: Any, user: Any, row: Any) -> None:
    if not row.fields.get("agent"):
        return
    selected = await _agent(db, user, row)
    row.fields = {**row.fields, "agent": selected.slug}
    if row.kind == "knowledge_review":
        items = await _item_choices(db, user, selected.id)
        row.state = {**row.state, "choices": {**row.state.get("choices", {}), "item": items}}
        if row.fields.get("item") and row.fields["item"] not in items:
            row.fields = {**row.fields, "item": ""}


def controls(row: Any) -> list[WorkflowField]:
    result = []
    for control in CONTROLS[row.kind]:
        options = (row.state or {}).get("choices", {}).get(control.key)
        result.append(
            control.model_copy(update={"options": options}) if options is not None else control
        )
    return result


async def prepare(db: Any, user: Any, row: Any) -> None:
    selected = await _agent(db, user, row)
    state = {**row.state, "agent_id": str(selected.id), "agent_name": selected.name}
    if row.kind == "knowledge":
        parsed = urlparse(row.fields["url"].strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(422, "Geçerli bir web adresi girin (https://...).")
        canonical = normalize_url(row.fields["url"])
        existing = await db.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.tenant_id == user.tenant_id,
                KnowledgeSource.agent_id == selected.id,
                KnowledgeSource.canonical_uri == canonical,
            )
        )
        if existing is not None:
            raise HTTPException(409, "Bu web sitesi zaten bilgi kaynağı olarak kayıtlı.")
        counts = dict(
            (
                await db.execute(
                    select(KnowledgeSource.kind, func.count())
                    .where(KnowledgeSource.tenant_id == user.tenant_id, KnowledgeSource.agent_id == selected.id)
                    .group_by(KnowledgeSource.kind)
                )
            ).all()
        )
        auto = row.fields.get("auto_publish", "true") == "true"
        row.state = {
            **state,
            "canonical": canonical,
            "output": {
                "summary": (
                    f"{selected.name}: {canonical} taranacak (robots.txt'e uyularak, en fazla "
                    f"{60} sayfa). Bulunan ürün bilgileri ve görseller "
                    + ("güvenli olduğunda otomatik yayınlanacak; fiyat, stok, teslimat, garanti gibi konular "
                       "ve yeni ürünler onayınızı bekleyecek." if auto else "onayınızı bekleyecek.")
                    + f" Mevcut kaynaklar: {counts.get('website', 0)} site, {counts.get('document', 0)} doküman."
                )
            },
        }
        return
    item = row.fields.get("item", "")
    kind, _, identifier = item.partition(":")
    label = row.state.get("choices", {}).get("item", {}).get(item, item)
    decision = {"accept": "onaylanıp yayınlanacak", "reject": "reddedilecek", "revoke": "yayından kaldırılacak"}[
        row.fields["decision"]
    ]
    if kind == "candidate":
        candidate = await db.get(KnowledgeCandidate, UUID(identifier))
        if candidate is None or candidate.tenant_id != user.tenant_id:
            raise HTTPException(422, "Seçilen bilgi bulunamadı.")
        warning = ""
        if candidate.protected and row.fields["decision"] == "accept" and user.role.value not in {"tenant_owner", "super_admin"}:
            warning = " Korumalı konu: yalnızca şirket sahibi müşteriye görünür yapabilir; kabul taslakta gizli kalır."
        row.state = {**state, "output": {"summary": f"{label} {decision}.{warning}"}}
    elif kind == "media":
        media = await db.get(KnowledgeMedia, UUID(identifier))
        if media is None or media.tenant_id != user.tenant_id:
            raise HTTPException(422, "Seçilen görsel bulunamadı.")
        row.state = {**state, "output": {"summary": f"{label} {decision}."}}
    else:
        raise HTTPException(422, "Listeden bir bilgi veya görsel seçin.")


async def complete(db: Any, user: Any, session: Any, row: Any) -> None:
    aid = UUID(row.state["agent_id"])
    if row.kind == "knowledge":
        source = KnowledgeSource(
            tenant_id=user.tenant_id,
            agent_id=aid,
            kind="website",
            display_name=urlparse(row.state["canonical"]).netloc[:180],
            canonical_uri=row.state["canonical"],
            auto_publish=row.fields.get("auto_publish", "true") == "true",
            status="queued",
        )
        db.add(source)
        await db.flush()
        from src.modules.knowledge.router import _enqueue_sync

        queued = _enqueue_sync(user.tenant_id, source.id)
        row.result = {
            "outcome": "source_added",
            "source_id": str(source.id),
            "message": (
                f"{row.state['agent_name']}: {row.state['canonical']} bilgi kaynağı olarak eklendi"
                + (" ve tarama kuyruğa alındı." if queued else "; tarama kuyruğa alınamadı, asistan panelinden yeniden deneyin.")
            ),
        }
        return
    kind, _, identifier = row.fields["item"].partition(":")
    publisher = KnowledgePublisher(db)
    decision = row.fields["decision"]
    if kind == "candidate":
        candidate = await db.get(KnowledgeCandidate, UUID(identifier))
        if candidate is None:
            raise HTTPException(422, "Seçilen bilgi bulunamadı.")
        if decision == "accept":
            candidate.review_status = "accepted"
            await db.flush()
            force = {candidate.id} if (not candidate.protected or user.role.value in {"tenant_owner", "super_admin"}) else set()
            ids = {candidate.id}
            if candidate.kind == "offering" and candidate.subject_id:
                ids.update(
                    (
                        await db.execute(
                            select(KnowledgeCandidate.id).where(
                                KnowledgeCandidate.tenant_id == user.tenant_id,
                                KnowledgeCandidate.kind == "fact",
                                KnowledgeCandidate.subject_id == candidate.subject_id,
                                KnowledgeCandidate.review_status == "pending",
                            )
                        )
                    ).scalars()
                )
            report = await publisher.publish_pending(user.tenant_id, aid, candidate_ids=ids, force_visible=force, actor_id=user.id)
            message = "Bilgi yayınlandı." if report.published_facts or report.offerings else "Bilgi taslağa kaydedildi."
            if report.deferred_reason:
                message += f" ({report.deferred_reason})"
        elif decision == "reject":
            if candidate.published_ref:
                await publisher.revoke_candidate(user.tenant_id, candidate, actor_id=user.id, reason="rejected in chat")
            candidate.review_status = "rejected"
            await db.flush()
            message = "Bilgi reddedildi ve yayından kaldırıldı."
        else:
            report_r = await publisher.revoke_candidate(user.tenant_id, candidate, actor_id=user.id, reason="revoked in chat")
            message = "Bilgi yayından kaldırıldı." + (" Yeni canlı sürüm oluşturuldu." if report_r.live_version_id else "")
    else:
        media = await db.get(KnowledgeMedia, UUID(identifier))
        if media is None:
            raise HTTPException(422, "Seçilen görsel bulunamadı.")
        if decision == "accept":
            media.status = "accepted"
            await db.flush()
            report = await publisher.publish_pending(user.tenant_id, aid, candidate_ids=set(), media_ids={media.id}, force_visible={media.id}, actor_id=user.id)
            message = "Görsel ürüne bağlandı ve yayınlandı." if report.media else "Görsel yayınlanamadı: " + "; ".join(report.skipped.values())
        elif decision == "reject":
            if media.asset_id:
                await publisher.revoke_media(user.tenant_id, media, actor_id=user.id, reason="rejected in chat")
            media.status = "rejected"
            await db.flush()
            message = "Görsel reddedildi."
        else:
            report_r = await publisher.revoke_media(user.tenant_id, media, actor_id=user.id, reason="revoked in chat")
            message = "Görsel yayından kaldırıldı." + (" Yeni canlı sürüm oluşturuldu." if report_r.live_version_id else "")
    row.result = {"outcome": "knowledge_decision", "message": message}
    _ = _claims


__all__ = ["CONTROLS", "KINDS", "complete", "controls", "initialize", "prepare", "refresh"]
