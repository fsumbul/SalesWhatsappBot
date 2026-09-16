"""knowledge_chunks.guard — guardrail verdict per chunk (NIM plan WP1)

Revision ID: c8d5e0f1a024
Revises: b7c4d9e2f013
Create Date: 2026-09-16 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c8d5e0f1a024"
down_revision = "b7c4d9e2f013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The table already has RLS + grants from b7c4d9e2f013; a column needs neither.
    op.add_column(
        "knowledge_chunks",
        sa.Column("guard", postgresql.JSONB, server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "guard")
