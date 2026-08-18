"""add durable agent runtime jobs and inbound WhatsApp idempotency

Revision ID: d7429a13c6e1
Revises: 8c61d4e2a9f7
Create Date: 2026-08-15 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d7429a13c6e1"
down_revision = "8c61d4e2a9f7"
branch_labels = None
depends_on = None

_TABLE = "agent_runtime_jobs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("inbound_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("action", sa.String(32), nullable=True),
        sa.Column("fact_ids", postgresql.JSONB, server_default="[]", nullable=False),
        sa.Column("used_fallback", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("outbound_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outbound_wa_message_id", sa.String(120), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit", postgresql.JSONB, server_default="{}", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["inbound_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["outbound_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("inbound_message_id", name="uq_agent_runtime_jobs_inbound_message_id"),
    )
    op.create_index("ix_agent_runtime_jobs_tenant_status", _TABLE, ["tenant_id", "status"])

    # Meta retries the same event. Only inbound IDs are unique because outbound
    # status callbacks legitimately refer to the same WA id in another table.
    op.create_index(
        "uq_messages_inbound_wa_message_id",
        "messages",
        ["tenant_id", "wa_message_id"],
        unique=True,
        postgresql_where=sa.text("direction = 'inbound' AND wa_message_id IS NOT NULL"),
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


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE};")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY;")
    op.drop_index("uq_messages_inbound_wa_message_id", table_name="messages")
    op.drop_table(_TABLE)
