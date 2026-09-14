"""Durable, tenant-isolated conversational outreach."""

from alembic import op

revision = "f23bc45de67f"
down_revision = "f12ab34cd56e"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE chat_outbound_batches (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
      user_id uuid NOT NULL REFERENCES users(id), session_id uuid NOT NULL REFERENCES admin_chat_sessions(id),
      sender_id uuid NOT NULL REFERENCES sender_profiles(id), status varchar(30) NOT NULL DEFAULT 'draft',
      template jsonb NOT NULL, variables jsonb NOT NULL DEFAULT '{}', consent_evidence text,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE chat_outbound_recipients (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
      batch_id uuid NOT NULL REFERENCES chat_outbound_batches(id), phone varchar(20) NOT NULL,
      status varchar(30) NOT NULL DEFAULT 'draft', reason text, wa_message_id varchar(120), attempted_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_chat_outbound_phone UNIQUE(batch_id, phone))""")
    for table in ("chat_outbound_batches", "chat_outbound_recipients"):
        op.execute(f"CREATE INDEX ix_{table}_tenant_id ON {table}(tenant_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_isolation ON {table}
          USING (tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid)""")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO leadpulse_app")
    op.execute(
        "CREATE INDEX ix_chat_outbound_recipients_status ON chat_outbound_recipients(status)"
    )
    op.execute(
        "CREATE INDEX ix_chat_outbound_recipients_wa_message_id ON chat_outbound_recipients(wa_message_id)"
    )


def downgrade():
    op.execute("DROP TABLE chat_outbound_recipients")
    op.execute("DROP TABLE chat_outbound_batches")
