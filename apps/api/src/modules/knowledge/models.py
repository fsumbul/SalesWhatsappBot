"""Tenant knowledge sources, documents, snapshots, chunks, candidates and media.

Knowledge Ops pipeline (docs/multi-tenant-vector-retrieval-architecture.md §7.3/§8,
relaxed to auto-publish + revoke per docs/adr/ADR-003-self-service-knowledge.md):

    source (website | document)
      -> documents (uploaded bytes, sha256)          knowledge_documents
      -> snapshots (one per page / document page)    knowledge_snapshots
      -> chunks (text units, mentions)               knowledge_chunks   (+ kn_<tenant> graph)
      -> candidates (fact | offering | media)        knowledge_candidates
      -> media (validated, re-hosted images)         knowledge_media

Every row is tenant-scoped and RLS-protected. Candidates remember what they
published (``published_ref`` + ``published_version_id``) so a revoke can undo
exactly one change. Raw text never reaches the runtime index directly: only
candidates that the publisher turned into approved config facts do.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class KnowledgeSource(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        Index(
            "uq_knowledge_sources_agent_uri",
            "tenant_id",
            "agent_id",
            "canonical_uri",
            unique=True,
            postgresql_where=text("canonical_uri IS NOT NULL"),
        ),
    )

    agent_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # website | document
    display_name: Mapped[str] = mapped_column(String(180), nullable=False)
    canonical_uri: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_policy: Mapped[str] = mapped_column(String(24), default="manual", nullable=False)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="idle", nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class KnowledgeDocument(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "sha256", name="uq_knowledge_documents_source_sha"),
    )

    source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(180), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    extractor_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class KnowledgeSnapshot(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    """Immutable extracted text for one page or document page."""

    __tablename__ = "knowledge_snapshots"
    __table_args__ = (Index("ix_knowledge_snapshots_source_locator", "source_id", "locator"),)

    source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=True,
    )
    locator: Mapped[str] = mapped_column(String(2000), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="new", nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class KnowledgeChunk(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "ordinal", name="uq_knowledge_chunks_snapshot_ordinal"),
    )

    source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    locator: Mapped[str] = mapped_column(String(2000), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    embedded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extracted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Guardrail verdict (plan WP1): labels/scores only, never the chunk text.
    guard: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class KnowledgeCandidate(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    """A proposed fact / offering / media link with evidence and a review state."""

    __tablename__ = "knowledge_candidates"
    __table_args__ = (
        UniqueConstraint("source_id", "fingerprint", name="uq_knowledge_candidates_fingerprint"),
        Index("ix_knowledge_candidates_tenant_status", "tenant_id", "review_status"),
    )

    source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # fact | offering | media
    subject_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    protected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    published_version_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeMedia(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    """A validated product image, stored in the database and served publicly."""

    __tablename__ = "knowledge_media"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256", name="uq_knowledge_media_sha"),)

    source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("knowledge_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    origin_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(40), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True, nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    alt_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    published_version_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Vision verification trail (plan WP3): decision/confidence/model, no pixels.
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
