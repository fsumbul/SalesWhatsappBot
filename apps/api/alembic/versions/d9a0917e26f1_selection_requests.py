"""Add persistent customer selection requests with tenant isolation.

Revision ID: d9a0917e26f1
Revises: c4f9128ab6d0
"""
from alembic import op

revision = 'd9a0917e26f1'
down_revision = 'c4f9128ab6d0'
branch_labels = None
depends_on = None


def upgrade():
    statements = """
    CREATE TABLE selection_requests (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      definition jsonb NOT NULL, answers jsonb NOT NULL DEFAULT '{}',
      step_index integer NOT NULL DEFAULT 0, revision integer NOT NULL DEFAULT 0,
      status varchar(32) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','waiting_review','in_review','completed','cancelled')),
      confirmed_snapshot jsonb, confirmed_at timestamptz,
      assigned_to uuid REFERENCES users(id) ON DELETE SET NULL, internal_notes jsonb NOT NULL DEFAULT '[]',
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX uq_selection_active_conversation ON selection_requests(tenant_id,conversation_id) WHERE status='draft';
    CREATE INDEX ix_selection_requests_conversation_id ON selection_requests(conversation_id);
    CREATE INDEX ix_selection_requests_status ON selection_requests(status);
    CREATE TABLE selection_files (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      request_id uuid NOT NULL REFERENCES selection_requests(id) ON DELETE CASCADE,
      inbound_message_id uuid NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
      filename varchar(180) NOT NULL, mime_type varchar(80) NOT NULL,
      sha256 varchar(64) NOT NULL, content bytea NOT NULL,
      size_bytes integer NOT NULL CHECK(size_bytes>0 AND size_bytes<=5242880),
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_selection_files_request_id ON selection_files(request_id);
    CREATE TABLE selection_events (
      id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      request_id uuid NOT NULL REFERENCES selection_requests(id) ON DELETE CASCADE,
      inbound_message_id uuid NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE, response jsonb NOT NULL,
      kind varchar(24) NOT NULL DEFAULT 'answer',
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_selection_events_request_id ON selection_events(request_id);
    CREATE FUNCTION selection_snapshot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.confirmed_snapshot IS NOT NULL AND (NEW.confirmed_snapshot IS DISTINCT FROM OLD.confirmed_snapshot OR NEW.answers IS DISTINCT FROM OLD.answers OR NEW.definition IS DISTINCT FROM OLD.definition OR NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at) THEN
        RAISE EXCEPTION 'Confirmed selection snapshot is immutable';
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER selection_snapshot_immutable BEFORE UPDATE ON selection_requests FOR EACH ROW EXECUTE FUNCTION selection_snapshot_immutable();
    """
    tables, function = statements.split("    CREATE FUNCTION", 1)
    for statement in tables.split(";"):
        if statement.strip():
            op.execute(statement)
    function, trigger = function.split("    CREATE TRIGGER", 1)
    op.execute("CREATE FUNCTION" + function)
    op.execute("CREATE TRIGGER" + trigger)
    for table in ('selection_requests','selection_files','selection_events'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid)")
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO leadpulse_app')


def downgrade():
    for table in ('selection_events','selection_files','selection_requests'):
        op.execute(f'DROP TABLE {table}')
    op.execute('DROP FUNCTION selection_snapshot_immutable()')
