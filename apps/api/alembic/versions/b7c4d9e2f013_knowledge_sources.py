"""tenant knowledge sources, documents, snapshots, chunks, candidates, media

Revision ID: b7c4d9e2f013
Revises: f67fa89bc01d
Create Date: 2026-09-15 23:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b7c4d9e2f013"
down_revision = "f67fa89bc01d"
branch_labels = None
depends_on = None

_TABLES = (
    "knowledge_sources",
    "knowledge_documents",
    "knowledge_snapshots",
    "knowledge_chunks",
    "knowledge_candidates",
    "knowledge_media",
)


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", _uuid(), primary_key=True, nullable=False),
        sa.Column("tenant_id", _uuid(), nullable=False, index=True),
        sa.Column("agent_id", _uuid(), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(180), nullable=False),
        sa.Column("canonical_uri", sa.String(2000), nullable=True),
        sa.Column("enabled", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("sync_policy", sa.String(24), server_default="manual", nullable=False),
        sa.Column("auto_publish", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("status", sa.String(24), server_default="idle", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("stats", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("settings", postgresql.JSONB, server_default="{}", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_knowledge_sources_agent_uri",
        "knowledge_sources",
        ["tenant_id", "agent_id", "canonical_uri"],
        unique=True,
        postgresql_where=sa.text("canonical_uri IS NOT NULL"),
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("id", _uuid(), primary_key=True, nullable=False),
        sa.Column("tenant_id", _uuid(), nullable=False, index=True),
        sa.Column("source_id", _uuid(), nullable=False, index=True),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("extractor_version", sa.String(32), nullable=True),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("meta", postgresql.JSONB, server_default="{}", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_id", "sha256", name="uq_knowledge_documents_source_sha"),
    )

    op.create_table(
        "knowledge_snapshots",
        sa.Column("id", _uuid(), primary_key=True, nullable=False),
        sa.Column("tenant_id", _uuid(), nullable=False, index=True),
        sa.Column("source_id", _uuid(), nullable=False, index=True),
        sa.Column("document_id", _uuid(), nullable=True),
        sa.Column("locator", sa.String(2000), nullable=False),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default="new", nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", postgresql.JSONB, server_default="{}", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_knowledge_snapshots_source_locator", "knowledge_snapshots", ["source_id", "locator"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", _uuid(), primary_key=True, nullable=False),
        sa.Column("tenant_id", _uuid(), nullable=False, index=True),
        sa.Column("source_id", _uuid(), nullable=False, index=True),
        sa.Column("snapshot_id", _uuid(), nullable=False, index=True),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("locator", sa.String(2000), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("subject_ids", postgresql.JSONB, server_default="[]", nullable=False),
        sa.Column("embedded", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("extracted", sa.Boolean, server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["knowledge_snapshots.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("snapshot_id", "ordinal", name="uq_knowledge_chunks_snapshot_ordinal"),
    )

    op.create_table(
        "knowledge_candidates",
        sa.Column("id", _uuid(), primary_key=True, nullable=False),
        sa.Column("tenant_id", _uuid(), nullable=False, index=True),
        sa.Column("source_id", _uuid(), nullable=False, index=True),
        sa.Column("snapshot_id", _uuid(), nullable=True),
        sa.Column("chunk_id", _uuid(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(80), nullable=True),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("payload", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("evidence", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column("confidence", sa.Float, server_default="0", nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("review_status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("protected", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("published_ref", sa.String(120), nullable=True),
        sa.Column("published_version_id", _uuid(), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["knowledge_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chunk_id"], ["knowledge_chunks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["published_version_id"], ["agent_versions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("source_id", "fingerprint", name="uq_knowledge_candidates_fingerprint"),
    )
    op.create_index(
        "ix_knowledge_candidates_tenant_status", "knowledge_candidates", ["tenant_id", "review_status"]
    )

    op.create_table(
        "knowledge_media",
        sa.Column("id", _uuid(), primary_key=True, nullable=False),
        sa.Column("tenant_id", _uuid(), nullable=False, index=True),
        sa.Column("source_id", _uuid(), nullable=False, index=True),
        sa.Column("snapshot_id", _uuid(), nullable=True),
        sa.Column("origin_url", sa.String(2000), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(40), nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("subject_id", sa.String(80), nullable=True),
        sa.Column("alt_text", sa.String(300), nullable=True),
        sa.Column("score", sa.Float, server_default="0", nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("asset_id", sa.String(80), nullable=True),
        sa.Column("published_version_id", _uuid(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["knowledge_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["published_version_id"], ["agent_versions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_knowledge_media_sha"),
    )

    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
        # The restricted application role only ever receives DML grants (see
        # 0ba5bf6fe643_create_restricted_app_role); new tables need them too.
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO leadpulse_app;")


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_knowledge_candidates_tenant_status", table_name="knowledge_candidates")
    op.drop_index("ix_knowledge_snapshots_source_locator", table_name="knowledge_snapshots")
    op.drop_index("uq_knowledge_sources_agent_uri", table_name="knowledge_sources")
    for table in reversed(_TABLES):
        op.drop_table(table)
