"""create agents + agent_versions (Phase E1: self-service agent builder)

Revision ID: f3a91c2e8b7d
Revises: 0ba5bf6fe643
Create Date: 2026-07-22 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f3a91c2e8b7d"
down_revision = "0ba5bf6fe643"
branch_labels = None
depends_on = None

_TABLES = ["agents", "agent_versions"]


def upgrade() -> None:
    # create_type=False: without it, op.create_table() below would *also*
    # emit its own CREATE TYPE for this column's type, colliding with the
    # explicit .create() call here (matches migration 0001_initial's
    # _pgenum() pattern).
    agent_version_status_enum = postgresql.ENUM(
        "draft", "testing", "live", "archived", name="agent_version_status", create_type=False
    )
    agent_version_status_enum.create(op.get_bind())

    _ts_cols = [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]
    _id_col = lambda: sa.Column(  # noqa: E731
        "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
    )
    _tid_col = lambda: sa.Column(  # noqa: E731
        "tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True
    )

    op.create_table(
        "agents",
        _id_col(),
        _tid_col(),
        sa.Column("sector_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_ts_cols,
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_agents_tenant_slug"),
    )

    op.create_table(
        "agent_versions",
        _id_col(),
        _tid_col(),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column(
            "status", agent_version_status_enum, nullable=False, server_default="draft", index=True
        ),
        sa.Column("persona", sa.Text, nullable=False, server_default=""),
        sa.Column("tone", sa.String(80), nullable=False, server_default=""),
        sa.Column("languages", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("product_knowledge", sa.Text, nullable=False, server_default=""),
        sa.Column("qualification_questions", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("guardrails", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("reply_policies", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rolled_back_from_version", sa.Integer, nullable=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )

    # Row-Level Security — same pattern as migration 0001_initial.
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
    # No separate GRANT needed for leadpulse_app: migration 0ba5bf6fe643 set up
    # ALTER DEFAULT PRIVILEGES for the schema, so newly created tables inherit
    # the same SELECT/INSERT/UPDATE/DELETE + sequence USAGE automatically.


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    op.drop_table("agent_versions")
    op.drop_table("agents")

    op.execute("DROP TYPE IF EXISTS agent_version_status;")
