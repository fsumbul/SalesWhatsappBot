# ruff: noqa: RUF001
"""Turn accepted / auto-eligible candidates into approved company config.

Publishing always goes through the existing draft mutation point
(``AgentService.update_draft``) and, when the draft belongs to the publisher,
``promote_to_live``. Revocation uses ``AgentService.publish_hotfix`` so a
wrong fact disappears from LIVE immediately, independent of any admin draft.

Auto-publish policy (ADR-003): only candidates from the tenant's own sources,
above the confidence threshold and outside protected topics become
``customer_visible``. Everything else is staged into the draft hidden
(``customer_visible=false``) so the runtime index never sees it until an
administrator accepts it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.errors import ConflictError
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.models import AgentVersion
from src.modules.agents.schemas import AgentVersionPatchIn
from src.modules.agents.service import AgentService
from src.modules.compliance.models import AuditLog

from .media import media_public_path
from .models import KnowledgeCandidate, KnowledgeMedia, KnowledgeSource

logger = structlog.get_logger(__name__)


@dataclass
class PublishReport:
    published_facts: list[str] = field(default_factory=list)
    hidden_facts: list[str] = field(default_factory=list)
    offerings: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    draft_version_id: str | None = None
    live_version_id: str | None = None
    deferred_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RevokeReport:
    revoked: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    live_version_id: str | None = None
    draft_updated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def fact_identifier(subject_id: str, fingerprint: str) -> str:
    return f"kn_{subject_id[:40]}_{fingerprint[:8]}"


def media_asset_identifier(sha256: str) -> str:
    return f"kn_img_{sha256[:12]}"


def public_media_url(settings: Settings, tenant_id: UUID, media: KnowledgeMedia) -> str:
    base = (settings.knowledge_public_media_base_url or settings.app_base_url).rstrip("/")
    return base + media_public_path(tenant_id.hex, media.sha256, media.mime_type)


def _validate(config: dict[str, Any]) -> CompanyAgentConfig:
    return CompanyAgentConfig.model_validate({**config, "lifecycle": "draft"})


def _company_id(config: dict[str, Any]) -> str:
    organization = config.get("organization") or {}
    return str(organization.get("id") or "company")


def _known_subjects(config: dict[str, Any]) -> set[str]:
    return {_company_id(config)} | {str(o["id"]) for o in config.get("offerings", [])}


def _upsert(items: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
    return [*[i for i in items if i.get("id") != item["id"]], item]


class ConfigPatcher:
    """Incrementally apply candidates to a config dict, validating each step."""

    def __init__(self, config: dict[str, Any], locale: str) -> None:
        self.config = config
        self.locale = locale
        self.applied: dict[str, str] = {}
        self.skipped: dict[str, str] = {}

    def _try(self, key: str, candidate: dict[str, Any]) -> bool:
        try:
            _validate(candidate)
        except ValidationError as exc:
            self.skipped[key] = (
                exc.errors()[0].get("msg", "invalid")[:160] if exc.errors() else "invalid"
            )
            return False
        self.config = candidate
        return True

    def add_offering(self, candidate: KnowledgeCandidate) -> bool:
        payload = candidate.payload
        offering_id = str(payload.get("id") or candidate.subject_id or "")
        if not offering_id:
            self.skipped[str(candidate.id)] = "offering has no id"
            return False
        if offering_id in _known_subjects(self.config):
            self.applied[str(candidate.id)] = offering_id
            return True
        offering = {
            "id": offering_id,
            "kind": payload.get("kind", "physical_product"),
            "display_names": {self.locale: str(payload.get("name") or offering_id)[:120]},
            "provider_id": _company_id(self.config),
            "active": True,
        }
        candidate_config = {
            **self.config,
            "offerings": [*self.config.get("offerings", []), offering],
        }
        parent = payload.get("parent_id")
        if parent and parent in _known_subjects(self.config):
            candidate_config["relationships"] = [
                *self.config.get("relationships", []),
                {"subject_id": offering_id, "predicate": "part_of", "object_id": parent},
            ]
        if self._try(str(candidate.id), candidate_config):
            self.applied[str(candidate.id)] = offering_id
            return True
        return False

    def add_fact(
        self, candidate: KnowledgeCandidate, source: KnowledgeSource, *, visible: bool
    ) -> bool:
        subject_id = candidate.subject_id or _company_id(self.config)
        if subject_id not in _known_subjects(self.config):
            self.skipped[str(candidate.id)] = f"subject {subject_id} is not in the configuration"
            return False
        text = str(candidate.payload.get("customer_text") or "").strip()
        if len(text) < 8:
            self.skipped[str(candidate.id)] = "empty customer text"
            return False
        fact_id = fact_identifier(subject_id, candidate.fingerprint)
        locator = str(
            candidate.evidence.get("locator") or source.canonical_uri or source.display_name
        )
        fact = {
            "id": fact_id,
            "subject_id": subject_id,
            "category": candidate.category or "other",
            "value": {
                "candidate_id": str(candidate.id),
                "evidence": str(candidate.evidence.get("quote") or "")[:400],
                "confidence": candidate.confidence,
                "source_id": str(source.id),
            },
            "customer_visible": visible,
            "customer_text": {self.locale: text[:4000]},
            "search_terms": [
                t for t in candidate.payload.get("search_terms", []) if isinstance(t, str)
            ][:100],
            "source": locator[:160],
        }
        candidate_config = {
            **self.config,
            "facts": _upsert(list(self.config.get("facts", [])), fact),
        }
        if self._try(str(candidate.id), candidate_config):
            self.applied[str(candidate.id)] = fact_id
            return True
        return False

    def add_media(self, media: KnowledgeMedia, url: str) -> bool:
        subject_id = media.subject_id
        if not subject_id or subject_id not in _known_subjects(self.config):
            self.skipped[str(media.id)] = "image subject is not in the configuration"
            return False
        presentation = dict(self.config.get("whatsapp_presentation") or {})
        offering_media = dict(presentation.get("offering_media") or {})
        if subject_id in offering_media:
            self.skipped[str(media.id)] = "offering already has an image"
            return False
        asset_id = media_asset_identifier(media.sha256)
        asset = {
            "id": asset_id,
            "kind": "image",
            "url": url,
            "mime_type": media.mime_type,
            "size_bytes": media.size_bytes,
            "provenance": media.origin_url[:240],
        }
        presentation["assets"] = _upsert(list(presentation.get("assets") or []), asset)
        offering_media[subject_id] = asset_id
        presentation["offering_media"] = offering_media
        candidate_config = {
            **self.config,
            "schema_version": "company-agent-config/1.2",
            "whatsapp_presentation": presentation,
        }
        if self._try(str(media.id), candidate_config):
            self.applied[str(media.id)] = asset_id
            return True
        return False

    def remove_fact(self, fact_id: str) -> str | None:
        """Remove a fact; if something still references it, hide it instead."""

        facts = list(self.config.get("facts", []))
        if not any(f.get("id") == fact_id for f in facts):
            return None
        removed = {**self.config, "facts": [f for f in facts if f.get("id") != fact_id]}
        try:
            _validate(removed)
            self.config = removed
            return "removed"
        except ValidationError:
            hidden = {
                **self.config,
                "facts": [
                    {**f, "customer_visible": False} if f.get("id") == fact_id else f for f in facts
                ],
            }
            self.config = hidden
            return "hidden"

    def deactivate_offering(self, offering_id: str) -> str | None:
        offerings = list(self.config.get("offerings", []))
        if not any(o.get("id") == offering_id for o in offerings):
            return None
        self.config = {
            **self.config,
            "offerings": [
                {**o, "active": False} if o.get("id") == offering_id else o for o in offerings
            ],
        }
        presentation = dict(self.config.get("whatsapp_presentation") or {})
        media = dict(presentation.get("offering_media") or {})
        if offering_id in media:
            media.pop(offering_id)
            presentation["offering_media"] = media
            self.config = {**self.config, "whatsapp_presentation": presentation}
        return "deactivated"

    def remove_media(self, asset_id: str) -> str | None:
        presentation = dict(self.config.get("whatsapp_presentation") or {})
        assets = list(presentation.get("assets") or [])
        if not any(a.get("id") == asset_id for a in assets):
            return None
        presentation["assets"] = [a for a in assets if a.get("id") != asset_id]
        presentation["offering_media"] = {
            k: v for k, v in (presentation.get("offering_media") or {}).items() if v != asset_id
        }
        candidate = {**self.config, "whatsapp_presentation": presentation}
        try:
            _validate(candidate)
        except ValidationError:
            return None  # a carousel still needs it; the administrator must edit the carousel first
        self.config = candidate
        return "removed"


class KnowledgePublisher:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # --- publish -------------------------------------------------------------------

    def _auto_visible(self, candidate: KnowledgeCandidate, source: KnowledgeSource) -> bool:
        return (
            self.settings.knowledge_auto_publish
            and source.auto_publish
            and not candidate.protected
            and candidate.confidence >= self.settings.knowledge_auto_publish_threshold
        )

    async def publish_pending(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        *,
        candidate_ids: set[UUID] | None = None,
        media_ids: set[UUID] | None = None,
        force_visible: set[UUID] | None = None,
        actor_id: UUID | None = None,
    ) -> PublishReport:
        report = PublishReport()
        force_visible = force_visible or set()
        sources = {
            s.id: s
            for s in (
                await self.session.execute(
                    select(KnowledgeSource).where(
                        KnowledgeSource.tenant_id == tenant_id, KnowledgeSource.agent_id == agent_id
                    )
                )
            ).scalars()
        }
        if not sources:
            return report
        candidate_query = select(KnowledgeCandidate).where(
            KnowledgeCandidate.source_id.in_(list(sources)),
            KnowledgeCandidate.kind.in_(["offering", "fact"]),
        )
        candidate_query = (
            candidate_query.where(KnowledgeCandidate.id.in_(list(candidate_ids)))
            if candidate_ids
            else candidate_query.where(KnowledgeCandidate.review_status == "pending")
        )
        candidates = list(
            (
                await self.session.execute(candidate_query.order_by(KnowledgeCandidate.created_at))
            ).scalars()
        )
        media_query = select(KnowledgeMedia).where(
            KnowledgeMedia.source_id.in_(list(sources)), KnowledgeMedia.subject_id.is_not(None)
        )
        media_query = (
            media_query.where(KnowledgeMedia.id.in_(list(media_ids)))
            if media_ids
            else media_query.where(KnowledgeMedia.status == "pending")
        )
        media_rows = list(
            (
                await self.session.execute(media_query.order_by(KnowledgeMedia.score.desc()))
            ).scalars()
        )
        if not candidates and not media_rows:
            return report

        service = AgentService(self.session)
        draft = await service.get_draft(tenant_id, agent_id)
        publisher_owned = False
        if draft is None:
            draft = await service.create_draft(tenant_id, agent_id, actor_id=None)
            publisher_owned = True
        elif draft.created_by is None:
            publisher_owned = True
        report.draft_version_id = str(draft.id)

        patcher: ConfigPatcher | None = None
        for attempt in range(3):
            assert draft is not None
            config = dict(draft.company_config)
            locale = str((config.get("agent") or {}).get("default_locale") or "tr")
            patcher = ConfigPatcher(config, locale)
            visible_ids: set[str] = set()
            for candidate in candidates:
                if candidate.kind != "offering":
                    continue
                # A newly discovered product changes the company's catalogue;
                # that is an administrator decision (one click), never automatic.
                if candidate.id in force_visible or candidate.review_status == "accepted":
                    patcher.add_offering(candidate)
                else:
                    patcher.skipped[str(candidate.id)] = (
                        "new offering awaits administrator acceptance"
                    )
            for candidate in candidates:
                if candidate.kind != "fact":
                    continue
                source = sources[candidate.source_id]
                subject = candidate.subject_id or _company_id(patcher.config)
                if candidate.payload.get("subject_is_new") and subject not in _known_subjects(
                    patcher.config
                ):
                    patcher.skipped[str(candidate.id)] = (
                        "waiting for the new offering to be accepted"
                    )
                    continue
                visible = (
                    candidate.id in force_visible
                    or candidate.review_status == "accepted"
                    or self._auto_visible(candidate, source)
                )
                if (
                    candidate.review_status == "accepted"
                    and candidate.protected
                    and candidate.id not in force_visible
                ):
                    visible = False
                if patcher.add_fact(candidate, source, visible=visible) and visible:
                    visible_ids.add(str(candidate.id))
            for media in media_rows:
                source = sources[media.source_id]
                if not (
                    source.auto_publish or media.id in force_visible or media.status == "accepted"
                ):
                    patcher.skipped[str(media.id)] = "source auto-publish disabled"
                    continue
                patcher.add_media(media, public_media_url(self.settings, tenant_id, media))
            if patcher.config == config:
                break
            try:
                validated = _validate(patcher.config)
                draft = await service.update_draft(
                    tenant_id,
                    agent_id,
                    AgentVersionPatchIn(expected_revision=draft.revision, company_config=validated),
                    actor_id=actor_id,
                )
                break
            except ConflictError:
                await self.session.rollback()
                draft = await service.get_draft(tenant_id, agent_id)
                if draft is None or attempt == 2:
                    report.deferred_reason = "draft changed concurrently; retry later"
                    return report
        assert patcher is not None and draft is not None

        # Bookkeeping on candidates/media before promotion so a failed promote
        # still leaves an accurate trail.
        for candidate in candidates:
            ref = patcher.applied.get(str(candidate.id))
            if ref is None:
                skipped_reason = patcher.skipped.get(str(candidate.id))
                if skipped_reason and not skipped_reason.startswith(
                    ("new offering awaits", "waiting for")
                ):
                    candidate.error = skipped_reason
                continue
            candidate.published_ref = ref
            candidate.published_version_id = draft.id
            candidate.error = None
            if candidate.kind == "offering":
                # Offerings are only ever published after an explicit decision.
                candidate.review_status = "accepted"
                report.offerings.append(ref)
            elif str(candidate.id) in visible_ids:
                candidate.review_status = (
                    "auto_published" if candidate.review_status == "pending" else "accepted"
                )
                report.published_facts.append(ref)
            else:
                candidate.review_status = (
                    "staged" if candidate.review_status == "pending" else candidate.review_status
                )
                report.hidden_facts.append(ref)
        for media in media_rows:
            ref = patcher.applied.get(str(media.id))
            if ref is None:
                continue
            media.asset_id = ref
            media.published_version_id = draft.id
            media.status = "published"
            report.media.append(ref)
        report.skipped = patcher.skipped
        await self.session.commit()

        wants_live = self.settings.knowledge_publish_target == "live" and (
            report.published_facts or report.offerings or report.media
        )
        if wants_live and publisher_owned:
            try:
                live = await service.promote_to_live(
                    tenant_id, agent_id, draft.id, actor_id=actor_id
                )
                report.live_version_id = str(live.id)
            except ConflictError as exc:
                report.deferred_reason = f"not publishable yet: {exc.detail}"[:300]
        elif wants_live:
            report.deferred_reason = "an administrator draft is open; changes staged in that draft"
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge_publish",
                entity="agent_version",
                entity_id=str(draft.id),
                meta={"agent_id": str(agent_id), **report.as_dict()},
            )
        )
        await self.session.commit()
        return report

    # --- revoke ----------------------------------------------------------------------

    async def _apply_everywhere(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        mutate: Any,
        *,
        actor_id: UUID | None,
        reason: str,
    ) -> tuple[str | None, bool]:
        """Apply ``mutate(config_dict) -> changed`` to LIVE (hotfix) and to the draft."""

        service = AgentService(self.session)
        live_version_id: str | None = None
        live = await service.get_live(tenant_id, agent_id)
        if live is not None:
            probe = ConfigPatcher(dict(live.company_config), "tr")
            if mutate(probe):

                def _live_mutation(config: CompanyAgentConfig) -> CompanyAgentConfig:
                    patcher = ConfigPatcher(config.model_dump(mode="json"), "tr")
                    mutate(patcher)
                    return CompanyAgentConfig.model_validate(
                        {**patcher.config, "lifecycle": "approved"}
                    )

                new_live = await service.publish_hotfix(
                    tenant_id, agent_id, _live_mutation, actor_id=actor_id, reason=reason
                )
                live_version_id = str(new_live.id)
        draft_updated = False
        draft = await service.get_draft(tenant_id, agent_id)
        if draft is not None:
            patcher = ConfigPatcher(dict(draft.company_config), "tr")
            if mutate(patcher):
                try:
                    validated = _validate(patcher.config)
                    await service.update_draft(
                        tenant_id,
                        agent_id,
                        AgentVersionPatchIn(
                            expected_revision=draft.revision, company_config=validated
                        ),
                        actor_id=actor_id,
                    )
                    draft_updated = True
                except ConflictError:
                    await self.session.rollback()
        return live_version_id, draft_updated

    async def revoke_candidate(
        self,
        tenant_id: UUID,
        candidate: KnowledgeCandidate,
        *,
        actor_id: UUID | None = None,
        reason: str,
    ) -> RevokeReport:
        report = RevokeReport()
        source = await self.session.get(KnowledgeSource, candidate.source_id)
        if source is None:
            report.skipped[str(candidate.id)] = "source missing"
            return report
        ref = candidate.published_ref
        if ref:
            if candidate.kind == "fact":

                def mutate(patcher: ConfigPatcher) -> bool:
                    return patcher.remove_fact(ref) is not None

            else:

                def mutate(patcher: ConfigPatcher) -> bool:
                    return patcher.deactivate_offering(ref) is not None

            live_id, draft_updated = await self._apply_everywhere(
                tenant_id, source.agent_id, mutate, actor_id=actor_id, reason=reason
            )
            report.live_version_id = live_id
            report.draft_updated = draft_updated
        candidate.review_status = "revoked"
        candidate.error = reason[:240]
        report.revoked.append(str(candidate.id))
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge_revoke",
                entity="knowledge_candidate",
                entity_id=str(candidate.id),
                meta={"reason": reason[:240], "published_ref": ref, **report.as_dict()},
            )
        )
        await self.session.commit()
        return report

    async def revoke_media(
        self, tenant_id: UUID, media: KnowledgeMedia, *, actor_id: UUID | None = None, reason: str
    ) -> RevokeReport:
        report = RevokeReport()
        source = await self.session.get(KnowledgeSource, media.source_id)
        if source is None:
            report.skipped[str(media.id)] = "source missing"
            return report
        asset_id = media.asset_id
        if asset_id:

            def mutate(patcher: ConfigPatcher) -> bool:
                return patcher.remove_media(asset_id) is not None

            live_id, draft_updated = await self._apply_everywhere(
                tenant_id, source.agent_id, mutate, actor_id=actor_id, reason=reason
            )
            report.live_version_id = live_id
            report.draft_updated = draft_updated
        media.status = "revoked"
        report.revoked.append(str(media.id))
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge_revoke",
                entity="knowledge_media",
                entity_id=str(media.id),
                meta={"reason": reason[:240], "asset_id": asset_id, **report.as_dict()},
            )
        )
        await self.session.commit()
        return report

    async def revoke_source(
        self, tenant_id: UUID, source: KnowledgeSource, *, actor_id: UUID | None = None, reason: str
    ) -> RevokeReport:
        """Remove everything this source published in one hotfix + one draft update."""

        report = RevokeReport()
        candidates = list(
            (
                await self.session.execute(
                    select(KnowledgeCandidate).where(
                        KnowledgeCandidate.source_id == source.id,
                        KnowledgeCandidate.published_ref.is_not(None),
                        KnowledgeCandidate.review_status.in_(
                            ["auto_published", "accepted", "staged"]
                        ),
                    )
                )
            ).scalars()
        )
        media_rows = list(
            (
                await self.session.execute(
                    select(KnowledgeMedia).where(
                        KnowledgeMedia.source_id == source.id, KnowledgeMedia.status == "published"
                    )
                )
            ).scalars()
        )
        fact_refs = [c.published_ref for c in candidates if c.kind == "fact" and c.published_ref]
        offering_refs = [
            c.published_ref for c in candidates if c.kind == "offering" and c.published_ref
        ]
        asset_refs = [m.asset_id for m in media_rows if m.asset_id]

        def mutate(patcher: ConfigPatcher) -> bool:
            changed = False
            for asset in asset_refs:
                changed = (patcher.remove_media(asset) is not None) or changed
            for fact in fact_refs:
                changed = (patcher.remove_fact(fact) is not None) or changed
            for offering in offering_refs:
                changed = (patcher.deactivate_offering(offering) is not None) or changed
            return changed

        if fact_refs or offering_refs or asset_refs:
            live_id, draft_updated = await self._apply_everywhere(
                tenant_id, source.agent_id, mutate, actor_id=actor_id, reason=reason
            )
            report.live_version_id = live_id
            report.draft_updated = draft_updated
        for candidate in candidates:
            candidate.review_status = "revoked"
            report.revoked.append(str(candidate.id))
        for media in media_rows:
            media.status = "revoked"
            report.revoked.append(str(media.id))
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge_revoke_source",
                entity="knowledge_source",
                entity_id=str(source.id),
                meta={"reason": reason[:240], **report.as_dict()},
            )
        )
        await self.session.commit()
        return report


__all__ = [
    "ConfigPatcher",
    "KnowledgePublisher",
    "PublishReport",
    "RevokeReport",
    "public_media_url",
]

_ = AgentVersion  # re-exported type for callers that want to annotate results
