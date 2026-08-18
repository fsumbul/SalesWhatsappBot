"""add universal company_config to agent_versions

Revision ID: 8c61d4e2a9f7
Revises: 2d7e4a9c1f03
Create Date: 2026-07-28 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "8c61d4e2a9f7"
down_revision = "2d7e4a9c1f03"
branch_labels = None
depends_on = None

_EMPTY_DRAFT_CONFIG = (
    "'{\"schema_version\":\"company-agent-config/1.0\","
    "\"lifecycle\":\"draft\",\"parties\":[],\"offerings\":[],"
    "\"relationships\":[],\"facts\":[],\"customer_profiles\":[],"
    "\"processes\":[],\"policies\":[],\"modules\":[]}'::jsonb"
)


def upgrade() -> None:
    # agent_versions is already tenant-scoped and protected by its table RLS
    # policy. A column-only change needs no new policy or grants.
    op.add_column(
        "agent_versions",
        sa.Column(
            "company_config",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text(_EMPTY_DRAFT_CONFIG),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "company_config")
