"""enforce one WhatsApp phone identity per tenant

Revision ID: e13c8a7b4d29
Revises: d7429a13c6e1
Create Date: 2026-08-15 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e13c8a7b4d29"
down_revision = "d7429a13c6e1"
branch_labels = None
depends_on = None

_INDEX = "uq_lead_contacts_tenant_phone"


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM lead_contacts
                WHERE type = 'phone'
                GROUP BY tenant_id, normalized_value
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'duplicate tenant WhatsApp phone identities must be reconciled before upgrade';
            END IF;
        END $$;
        """
    )
    op.create_index(
        _INDEX,
        "lead_contacts",
        ["tenant_id", "normalized_value"],
        unique=True,
        postgresql_where=sa.text("type = 'phone'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="lead_contacts")
