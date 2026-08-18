"""enforce one active tenant per WhatsApp Business Account

Revision ID: c4f9128ab6d0
Revises: e13c8a7b4d29
Create Date: 2026-08-15 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c4f9128ab6d0"
down_revision = "e13c8a7b4d29"
branch_labels = None
depends_on = None

_INDEX = "uq_tenants_active_waba"


def upgrade() -> None:
    # Audit before DDL so an existing routing ambiguity fails with a clear,
    # sanitized count instead of a generic unique-index build error.
    op.execute(
        """
        DO $$
        DECLARE
            duplicate_groups integer;
        BEGIN
            SELECT count(*)
            INTO duplicate_groups
            FROM (
                SELECT wa_business_account_id
                FROM tenants
                WHERE status = 'active'::tenant_status
                  AND wa_business_account_id IS NOT NULL
                GROUP BY wa_business_account_id
                HAVING count(*) > 1
            ) conflicts;

            IF duplicate_groups > 0 THEN
                RAISE EXCEPTION
                    'found % duplicate active tenant WABA binding group(s)', duplicate_groups
                    USING HINT =
                        'Suspend or rebind conflicting tenants before rerunning this migration.';
            END IF;
        END $$;
        """
    )
    op.create_index(
        _INDEX,
        "tenants",
        ["wa_business_account_id"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active'::tenant_status AND wa_business_account_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="tenants")
