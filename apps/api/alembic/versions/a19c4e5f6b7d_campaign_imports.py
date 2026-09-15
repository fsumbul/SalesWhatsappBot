"""Private outreach-file imports with tenant RLS and retained receipts.

Revision ID: a19c4e5f6b7d
Revises: f67fa89bc01d
"""

from alembic import op

revision = "a19c4e5f6b7d"
down_revision = "f67fa89bc01d"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY tenant_isolation ON {table}
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)"""
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO leadpulse_app")


def upgrade() -> None:
    op.execute(
        """CREATE TABLE chat_campaign_imports (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            workflow_id uuid NOT NULL REFERENCES chat_workflows(id) ON DELETE CASCADE,
            uploader_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            client_operation_id uuid NOT NULL,
            object_key varchar(512) NOT NULL UNIQUE,
            original_filename varchar(255) NOT NULL,
            sha256 varchar(64) NOT NULL,
            mime_type varchar(120) NOT NULL,
            status varchar(32) NOT NULL DEFAULT 'queued'
              CHECK (status IN ('queued', 'parsing', 'awaiting_mapping', 'ready', 'failed')),
            parse_token uuid,
            country_code varchar(2) NOT NULL,
            phone_column varchar(255),
            columns jsonb NOT NULL DEFAULT '[]',
            total_rows integer NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
            eligible_count integer NOT NULL DEFAULT 0 CHECK (eligible_count >= 0),
            invalid_count integer NOT NULL DEFAULT 0 CHECK (invalid_count >= 0),
            duplicate_count integer NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
            blocked_count integer NOT NULL DEFAULT 0 CHECK (blocked_count >= 0),
            summary jsonb NOT NULL DEFAULT '{}',
            consent_source varchar(120),
            consent_note text,
            failure_reason text,
            expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_campaign_import_workflow_operation
              UNIQUE (workflow_id, client_operation_id)
        )"""
    )
    op.execute("CREATE INDEX ix_chat_campaign_imports_tenant_id ON chat_campaign_imports(tenant_id)")
    op.execute("CREATE INDEX ix_chat_campaign_imports_workflow_id ON chat_campaign_imports(workflow_id)")
    op.execute(
        "CREATE INDEX ix_campaign_imports_tenant_expires "
        "ON chat_campaign_imports(tenant_id, expires_at)"
    )
    op.execute(
        "CREATE INDEX ix_chat_campaign_imports_status ON chat_campaign_imports(status)"
    )
    _tenant_rls("chat_campaign_imports")

    op.execute(
        """CREATE TABLE chat_campaign_import_rows (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            campaign_import_id uuid NOT NULL REFERENCES chat_campaign_imports(id) ON DELETE CASCADE,
            row_number integer NOT NULL CHECK (row_number > 0),
            phone_e164 varchar(32),
            masked_phone varchar(32),
            status varchar(20) NOT NULL
              CHECK (status IN ('eligible', 'invalid', 'duplicate', 'blocked')),
            reason text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_campaign_import_row_number UNIQUE (campaign_import_id, row_number)
        )"""
    )
    op.execute(
        "CREATE INDEX ix_chat_campaign_import_rows_tenant_id ON chat_campaign_import_rows(tenant_id)"
    )
    op.execute(
        "CREATE INDEX ix_chat_campaign_import_rows_campaign_import_id "
        "ON chat_campaign_import_rows(campaign_import_id)"
    )
    op.execute(
        "CREATE INDEX ix_campaign_import_rows_import_status "
        "ON chat_campaign_import_rows(campaign_import_id, status)"
    )
    op.execute("CREATE INDEX ix_chat_campaign_import_rows_status ON chat_campaign_import_rows(status)")
    _tenant_rls("chat_campaign_import_rows")

    op.execute(
        "ALTER TABLE chat_outbound_batches "
        "ADD COLUMN campaign_import_id uuid REFERENCES chat_campaign_imports(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE chat_outbound_batches ADD COLUMN consent_source varchar(120)")
    op.execute("ALTER TABLE chat_outbound_batches ADD COLUMN consent_note text")
    op.execute("ALTER TABLE chat_outbound_batches ADD COLUMN source_hash varchar(64)")
    op.execute(
        "CREATE INDEX ix_chat_outbound_batches_campaign_import_id "
        "ON chat_outbound_batches(campaign_import_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chat_outbound_batches DROP COLUMN source_hash")
    op.execute("ALTER TABLE chat_outbound_batches DROP COLUMN consent_note")
    op.execute("ALTER TABLE chat_outbound_batches DROP COLUMN consent_source")
    op.execute("ALTER TABLE chat_outbound_batches DROP COLUMN campaign_import_id")
    op.execute("DROP TABLE chat_campaign_import_rows")
    op.execute("DROP TABLE chat_campaign_imports")
