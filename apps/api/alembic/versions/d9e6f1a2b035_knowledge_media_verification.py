"""knowledge_media.verification — vision verification trail (NIM plan WP3)

Revision ID: d9e6f1a2b035
Revises: c8d5e0f1a024
Create Date: 2026-09-16 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d9e6f1a2b035"
down_revision = "c8d5e0f1a024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # knowledge_media already carries RLS + grants (b7c4d9e2f013).
    op.add_column(
        "knowledge_media",
        sa.Column("verification", postgresql.JSONB, server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("knowledge_media", "verification")
