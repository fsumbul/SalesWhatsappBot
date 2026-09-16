# ruff: noqa: RUF001
"""Source sync orchestration: fetch/extract → snapshots → chunks → graph → candidates → publish."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.integrations.llm import LLMClient
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.models import AgentVersion, AgentVersionStatus
from src.modules.guardrails.ports import GuardDecision, GuardVerdict, InputGuard

from .chunking import chunk_text, content_hash
from .crawler import WebsiteCrawler
from .evidence import EvidenceGraph
from .extract import EXTRACTOR_VERSION, PageExtract, TextUnit, extract_document
from .extraction import ValidatedExtraction, extract_chunk, subject_labels
from .media import discover_images, fetch_image
from .models import (
    KnowledgeCandidate,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeMedia,
    KnowledgeSnapshot,
    KnowledgeSource,
)
from .ocr import DocumentOcr, ocr_pdf_pages, pages_without_text
from .publisher import KnowledgePublisher, PublishReport
from .vision import MediaVerifier, verify_media

logger = structlog.get_logger(__name__)
_MAX_CHUNKS_PER_SYNC = 400
_EXTRACTION_CONCURRENCY = 2
_GUARD_CONCURRENCY = 4


async def agent_config_for(
    session: AsyncSession, tenant_id: UUID, agent_id: UUID
) -> CompanyAgentConfig | None:
    """Prefer the LIVE config; fall back to the draft so a new tenant can start from zero."""

    for status in (AgentVersionStatus.LIVE, AgentVersionStatus.DRAFT, AgentVersionStatus.TESTING):
        version = (
            (
                await session.execute(
                    select(AgentVersion)
                    .where(
                        AgentVersion.tenant_id == tenant_id,
                        AgentVersion.agent_id == agent_id,
                        AgentVersion.status == status,
                    )
                    .order_by(AgentVersion.version.desc())
                )
            )
            .scalars()
            .first()
        )
        if version is not None:
            try:
                return CompanyAgentConfig.model_validate(version.company_config)
            except Exception as exc:  # an invalid draft must not block a valid live config
                logger.warning(
                    "knowledge.config.invalid", version_id=str(version.id), error=type(exc).__name__
                )
                continue
    return None


async def _document_bytes(session: AsyncSession, document: KnowledgeDocument) -> bytes:
    """Load the deferred blob column inside the greenlet context."""

    def _load(_session: object) -> bytes:
        return bytes(document.content)

    return await session.run_sync(_load)


class KnowledgeIngestService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        llm: LLMClient,
        graph: EvidenceGraph | None,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
        guard: InputGuard | None = None,
        ocr: DocumentOcr | None = None,
        vision: MediaVerifier | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.graph = graph
        # Vision verification (plan WP3): confirms/overrides the image subject.
        self.vision = vision
        # OCR chain (plan WP2): scanned PDF pages become bbox-located units.
        self.ocr = ocr
        # Guardrail gate (plan WP1): blocked chunks are stored with their
        # verdict but never extracted or embedded.
        self.guard = guard
        self.settings = settings or get_settings()
        self.http_client = http_client
        self.stats: Counter[str] = Counter()

    # --- entry point ----------------------------------------------------------

    async def sync_source(self, tenant_id: UUID, source_id: UUID) -> dict[str, Any]:
        source = await self.session.get(KnowledgeSource, source_id)
        if source is None or source.tenant_id != tenant_id:
            return {"status": "missing"}
        config = await agent_config_for(self.session, tenant_id, source.agent_id)
        if config is None:
            source.status = "failed"
            source.last_error = "agent has no configuration version"
            await self.session.commit()
            return {"status": "failed", "error": source.last_error}
        source.status = "running"
        source.last_error = None
        await self.session.commit()
        report: PublishReport | None = None
        try:
            if self.graph is not None:
                assert config.agent is not None
                await self.graph.ensure_indexes(tenant_id, config.agent.default_locale)
            if source.kind == "document":
                await self._sync_documents(tenant_id, source, config)
            elif source.kind == "website":
                await self._sync_website(tenant_id, source, config)
            else:
                raise ValueError(f"unknown source kind {source.kind}")
            report = await KnowledgePublisher(self.session, self.settings).publish_pending(
                tenant_id, source.agent_id
            )
            source.status = "idle"
        except Exception as exc:
            await self.session.rollback()
            source = await self.session.get(KnowledgeSource, source_id)
            if source is not None:
                source.status = "failed"
                source.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.exception("knowledge.sync.failed", source_id=str(source_id))
            await self.session.commit()
            return {"status": "failed", "error": str(exc)[:300], "stats": dict(self.stats)}
        source.last_sync_at = datetime.now(UTC)
        source.stats = {**dict(self.stats), "publish": report.as_dict() if report else None}
        await self.session.commit()
        return {
            "status": "synced",
            "stats": dict(self.stats),
            "publish": report.as_dict() if report else None,
        }

    # --- documents --------------------------------------------------------------

    async def _sync_documents(
        self, tenant_id: UUID, source: KnowledgeSource, config: CompanyAgentConfig
    ) -> None:
        documents = list(
            (
                await self.session.execute(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.source_id == source.id,
                        KnowledgeDocument.status.in_(["pending", "failed"]),
                    )
                )
            ).scalars()
        )
        for document in documents:
            try:
                data = await _document_bytes(self.session, document)
                units = extract_document(document.filename, document.mime_type, data)
                if (
                    self.ocr is not None
                    and self.ocr.available
                    and document.mime_type == "application/pdf"
                ):
                    units = await self._ocr_missing_pages(document, data, units)
                document.page_count = len(units)
                document.extractor_version = EXTRACTOR_VERSION
                if not units:
                    document.status = "failed"
                    document.error = "no extractable text"
                    await self.session.commit()
                    continue
                for unit in units:
                    await self._ingest_unit(
                        tenant_id, source, config, unit, document_id=document.id, page=None
                    )
                document.status = "extracted"
                document.error = None
                self.stats["documents"] += 1
            except Exception as exc:
                document.status = "failed"
                document.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                self.stats["document_errors"] += 1
                logger.warning(
                    "knowledge.document.failed", document_id=str(document.id), error=document.error
                )
            await self.session.commit()

    async def _ocr_missing_pages(
        self, document: KnowledgeDocument, data: bytes, units: list[TextUnit]
    ) -> list[TextUnit]:
        """OCR pages without a text layer; the document report never holds text."""

        assert self.ocr is not None
        try:
            missing = await asyncio.to_thread(
                pages_without_text,
                data,
                units,
                min_chars=self.settings.knowledge_ocr_min_text_chars,
            )
        except Exception as exc:  # unreadable by pdfium: keep the text-layer result
            logger.warning("knowledge.ocr.page_scan_failed", error=type(exc).__name__)
            return units
        if not missing:
            return units
        ocr_units, report = await ocr_pdf_pages(
            document.filename,
            data,
            missing,
            self.ocr,
            dpi=self.settings.knowledge_ocr_dpi,
            max_pages=self.settings.knowledge_ocr_max_pages_per_document,
        )
        document.meta = {**document.meta, "ocr": {**report, "missing_pages": missing}}
        self.stats["ocr_pages"] += len(report["pages"])
        self.stats["ocr_units"] += len(ocr_units)
        merged = [*units, *ocr_units]
        merged.sort(
            key=lambda unit: (
                int(unit.meta.get("page", 0) or 0),
                1 if unit.meta.get("ocr") else 0,
                unit.locator,
            )
        )
        return merged

    # --- website ------------------------------------------------------------------

    async def _sync_website(
        self, tenant_id: UUID, source: KnowledgeSource, config: CompanyAgentConfig
    ) -> None:
        assert source.canonical_uri
        crawl_settings = source.settings or {}
        crawler = WebsiteCrawler(
            user_agent=self.settings.knowledge_crawl_user_agent,
            max_pages=int(crawl_settings.get("max_pages", self.settings.knowledge_crawl_max_pages)),
            max_depth=int(crawl_settings.get("max_depth", self.settings.knowledge_crawl_max_depth)),
            client=self.http_client,
        )
        labels = subject_labels(config)
        seen_locators: set[str] = set()
        async for crawled in crawler.crawl(source.canonical_uri):
            self.stats["pages_fetched"] += 1
            if crawled.page is None or not crawled.page.text:
                self.stats["pages_failed"] += 1
                continue
            seen_locators.add(crawled.url)
            unit = TextUnit(
                locator=crawled.url,
                text=crawled.page.text,
                title=crawled.page.title,
                meta={
                    "product": crawled.page.product,
                    "image_count": len(crawled.page.images),
                    "depth": crawled.depth,
                },
            )
            snapshot = await self._ingest_unit(
                tenant_id, source, config, unit, document_id=None, page=crawled.page
            )
            if snapshot is not None and crawled.page.images:
                await self._ingest_images(tenant_id, source, snapshot, crawled.page, labels)
            await self.session.commit()
        # Pages that disappeared since the last sync are marked, never auto-revoked.
        stale_query = select(KnowledgeSnapshot).where(
            KnowledgeSnapshot.source_id == source.id,
            KnowledgeSnapshot.status.in_(["new", "changed", "unchanged"]),
        )
        if seen_locators:
            stale_query = stale_query.where(KnowledgeSnapshot.locator.not_in(seen_locators))
        stale = (await self.session.execute(stale_query)).scalars()
        for snapshot in stale:
            if snapshot.document_id is None:
                snapshot.status = "removed"
                self.stats["pages_removed"] += 1

    # --- shared unit pipeline ---------------------------------------------------------

    async def _ingest_unit(
        self,
        tenant_id: UUID,
        source: KnowledgeSource,
        config: CompanyAgentConfig,
        unit: TextUnit,
        *,
        document_id: UUID | None,
        page: PageExtract | None,
    ) -> KnowledgeSnapshot | None:
        digest = content_hash(unit.text)
        previous = (
            (
                await self.session.execute(
                    select(KnowledgeSnapshot)
                    .where(
                        KnowledgeSnapshot.source_id == source.id,
                        KnowledgeSnapshot.locator == unit.locator,
                    )
                    .order_by(KnowledgeSnapshot.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if previous is not None and previous.content_hash == digest:
            previous.status = "unchanged"
            previous.fetched_at = datetime.now(UTC)
            self.stats["unchanged"] += 1
            return previous
        if previous is not None:
            previous.status = "removed"
            if self.graph is not None:
                await self.graph.delete_snapshot(tenant_id, previous.id)
        snapshot = KnowledgeSnapshot(
            tenant_id=tenant_id,
            source_id=source.id,
            document_id=document_id,
            locator=unit.locator[:2000],
            title=(unit.title or "")[:300] or None,
            text=unit.text,
            content_hash=digest,
            status="changed" if previous is not None else "new",
            fetched_at=datetime.now(UTC),
            meta=unit.meta,
        )
        self.session.add(snapshot)
        await self.session.flush()
        self.stats["snapshots"] += 1

        chunks = chunk_text(unit.text)
        if self.stats["chunks"] + len(chunks) > _MAX_CHUNKS_PER_SYNC:
            chunks = chunks[: max(0, _MAX_CHUNKS_PER_SYNC - self.stats["chunks"])]
            self.stats["chunks_capped"] += 1
        rows: list[KnowledgeChunk] = []
        for piece in chunks:
            row = KnowledgeChunk(
                id=uuid4(),
                tenant_id=tenant_id,
                source_id=source.id,
                snapshot_id=snapshot.id,
                ordinal=piece.ordinal,
                locator=f"{unit.locator}#chars={piece.start}-{piece.end}"[:2000],
                text=piece.text,
                content_hash=piece.content_hash,
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        self.stats["chunks"] += len(rows)

        rows = await self._guard_chunks(rows)
        extractions = await self._extract_chunks(config, rows)
        for row, extraction in zip(rows, extractions, strict=True):
            if extraction is None:
                continue
            row.subject_ids = extraction.mentions
            row.extracted = True
            await self._store_candidates(tenant_id, source, snapshot, row, extraction)
        if page is not None and page.product.get("name"):
            snapshot.meta = {**snapshot.meta, "product_page": True}
        if self.graph is not None and rows:
            try:
                await self.graph.upsert_chunks(
                    tenant_id,
                    [
                        {
                            "id": str(row.id),
                            "source_id": str(source.id),
                            "snapshot_id": str(snapshot.id),
                            "locator": row.locator,
                            "title": snapshot.title or "",
                            "text": row.text,
                            "content_hash": row.content_hash,
                            "subject_ids": list(row.subject_ids),
                        }
                        for row in rows
                    ],
                )
                for row in rows:
                    row.embedded = True
            except Exception as exc:  # embedding/graph outage: rows stay unembedded, sync continues
                self.stats["graph_errors"] += 1
                logger.warning("knowledge.graph.upsert_failed", error=type(exc).__name__)
        await self.session.commit()
        return snapshot

    async def _guard_chunks(self, rows: list[KnowledgeChunk]) -> list[KnowledgeChunk]:
        """Return the chunks that may be extracted and embedded.

        Blocked chunks (and every chunk when a classifier is unavailable in
        closed mode) keep their verdict in ``guard`` and stay unprocessed;
        flagged chunks proceed with the verdict recorded. The sync itself never
        fails because of the gate.
        """

        if self.guard is None or not self.guard.available or not rows:
            return rows
        guard = self.guard
        semaphore = asyncio.Semaphore(_GUARD_CONCURRENCY)

        async def one(row: KnowledgeChunk) -> GuardVerdict:
            async with semaphore:
                return await guard.check_document_text(row.text)

        verdicts: list[GuardVerdict] = list(await asyncio.gather(*(one(row) for row in rows)))
        active: list[KnowledgeChunk] = []
        for row, verdict in zip(rows, verdicts, strict=True):
            if verdict.decision == GuardDecision.ALLOW:
                active.append(row)
                continue
            row.guard = verdict.audit()
            if verdict.decision == GuardDecision.FLAG:
                self.stats["chunks_flagged"] += 1
                active.append(row)
            elif verdict.decision == GuardDecision.BLOCK:
                self.stats["chunks_blocked"] += 1
            else:
                self.stats["chunks_guard_unavailable"] += 1
        return active

    async def _extract_chunks(
        self, config: CompanyAgentConfig, rows: list[KnowledgeChunk]
    ) -> list[ValidatedExtraction | None]:
        semaphore = asyncio.Semaphore(_EXTRACTION_CONCURRENCY)

        async def one(row: KnowledgeChunk) -> ValidatedExtraction | None:
            async with semaphore:
                try:
                    return await extract_chunk(self.llm, config, row.text, locator=row.locator)
                except Exception as exc:
                    self.stats["extraction_errors"] += 1
                    logger.warning(
                        "knowledge.extract.failed", chunk_id=str(row.id), error=type(exc).__name__
                    )
                    return None

        return list(await asyncio.gather(*(one(row) for row in rows)))

    async def _store_candidates(
        self,
        tenant_id: UUID,
        source: KnowledgeSource,
        snapshot: KnowledgeSnapshot,
        chunk: KnowledgeChunk,
        extraction: ValidatedExtraction,
    ) -> None:
        existing = {
            str(fingerprint)
            for fingerprint in (
                await self.session.execute(
                    select(KnowledgeCandidate.fingerprint).where(
                        KnowledgeCandidate.source_id == source.id
                    )
                )
            ).scalars()
        }
        for offering in extraction.new_offerings:
            fingerprint = f"offering:{offering.id}"[:64]
            if fingerprint in existing:
                continue
            existing.add(fingerprint)
            self.session.add(
                KnowledgeCandidate(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    snapshot_id=snapshot.id,
                    chunk_id=chunk.id,
                    kind="offering",
                    subject_id=offering.id,
                    payload={
                        "id": offering.id,
                        "name": offering.name,
                        "kind": offering.kind.value,
                        "parent_id": offering.parent_id,
                    },
                    evidence={"locator": chunk.locator, "quote": offering.name},
                    confidence=0.8,
                    fingerprint=fingerprint,
                )
            )
            self.stats["candidate_offerings"] += 1
        for fact in extraction.facts:
            if fact.fingerprint in existing:
                continue
            existing.add(fact.fingerprint)
            self.session.add(
                KnowledgeCandidate(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    snapshot_id=snapshot.id,
                    chunk_id=chunk.id,
                    kind="fact",
                    subject_id=fact.subject_id,
                    category=fact.category,
                    payload={
                        "customer_text": fact.customer_text,
                        "search_terms": fact.search_terms,
                        "codes": fact.codes,
                        "subject_is_new": fact.subject_is_new,
                    },
                    evidence={"locator": chunk.locator, "quote": fact.evidence_quote},
                    confidence=fact.confidence,
                    fingerprint=fact.fingerprint,
                    protected=fact.protected,
                )
            )
            self.stats["candidate_facts"] += 1
        await self.session.flush()

    async def _ingest_images(
        self,
        tenant_id: UUID,
        source: KnowledgeSource,
        snapshot: KnowledgeSnapshot,
        page: PageExtract,
        labels: dict[str, str],
    ) -> None:
        # Offerings proposed from this source count as subjects for images too.
        proposed = (
            await self.session.execute(
                select(KnowledgeCandidate).where(
                    KnowledgeCandidate.source_id == source.id, KnowledgeCandidate.kind == "offering"
                )
            )
        ).scalars()
        all_labels = {
            **labels,
            **{
                c.subject_id or "": str(c.payload.get("name", "")) for c in proposed if c.subject_id
            },
        }
        candidates = discover_images(
            page, all_labels, min_pixels=self.settings.knowledge_media_min_pixels
        )
        if not candidates:
            return
        per_subject: Counter[str] = Counter()
        for candidate in candidates:
            subject = candidate.subject_id
            if subject is None:
                continue
            if per_subject[subject] >= self.settings.knowledge_media_max_per_subject:
                continue
            image = await fetch_image(
                candidate.url,
                user_agent=self.settings.knowledge_crawl_user_agent,
                client=self.http_client,
                min_pixels=self.settings.knowledge_media_min_pixels,
            )
            if image is None:
                self.stats["media_rejected"] += 1
                continue
            duplicate = (
                await self.session.execute(
                    select(KnowledgeMedia.id).where(
                        KnowledgeMedia.tenant_id == tenant_id, KnowledgeMedia.sha256 == image.sha256
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                per_subject[subject] += 1
                continue
            decision = await verify_media(
                candidate,
                image,
                subject_labels=all_labels,
                verifier=self.vision,
                min_confidence=self.settings.knowledge_vision_min_confidence,
                max_edge=self.settings.knowledge_vision_max_edge,
                context=page.title or "",
            )
            if decision.verification:
                self.stats["media_verified"] += 1
            if not decision.store:
                self.stats["media_rejected_vision"] += 1
                continue
            if decision.subject_id is not None and decision.subject_id != subject:
                self.stats["media_subject_overridden"] += 1
                subject = decision.subject_id
                if per_subject[subject] >= self.settings.knowledge_media_max_per_subject:
                    continue
            self.session.add(
                KnowledgeMedia(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    snapshot_id=snapshot.id,
                    origin_url=candidate.url[:2000],
                    sha256=image.sha256,
                    mime_type=image.mime_type,
                    width=image.width,
                    height=image.height,
                    size_bytes=len(image.content),
                    content=image.content,
                    subject_id=subject,
                    alt_text=decision.alt_text,
                    score=decision.score,
                    verification=dict(decision.verification),
                )
            )
            per_subject[subject] += 1
            self.stats["media_stored"] += 1
        await self.session.flush()
