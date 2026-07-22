"""create agent_builder_sessions (Phase E2 scaffolding: builder bot)

Revision ID: 9c4e5f1a2b6d
Revises: f3a91c2e8b7d
Create Date: 2026-07-22 01:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "9c4e5f1a2b6d"
down_revision = "f3a91c2e8b7d"
branch_labels = None
depends_on = None

_TABLE = "agent_builder_sessions"


def upgrade() -> None:
    # create_type=False: op.create_table() would otherwise also emit its own
    # CREATE TYPE for this column, colliding with the explicit .create() call
    # below (see f3a91c2e8b7d's agent_version_status for the same footgun).
    builder_session_status_enum = postgresql.ENUM(
        "active", "completed", "abandoned", name="builder_session_status", create_type=False
    )
    builder_session_status_enum.create(op.get_bind())

    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("draft_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", builder_session_status_enum, nullable=False, server_default="active", index=True
        ),
        sa.Column("messages", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("started_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_version_id"], ["agent_versions.id"], ondelete="CASCADE"),
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {_TABLE}
        USING (tenant_id::text = current_setting('app.current_tenant', true))
        WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """
    )
    # No separate GRANT needed for leadpulse_app — inherited via the
    # ALTER DEFAULT PRIVILEGES set up in migration 0ba5bf6fe643.


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE};")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY;")
    op.drop_table(_TABLE)
    op.execute("DROP TYPE IF EXISTS builder_session_status;")
