"""Private progressive workflows and separate person names (additive).

Revision ID: f34cd56ef78a
Revises: f23bc45de67f
"""
from alembic import op

revision = "f34cd56ef78a"
down_revision = "f23bc45de67f"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE leads ADD COLUMN person_name varchar(160)")
    op.execute("""CREATE TABLE chat_workflows (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        session_id uuid NOT NULL REFERENCES admin_chat_sessions(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind varchar(60) NOT NULL, revision integer NOT NULL DEFAULT 0,
        step varchar(60) NOT NULL DEFAULT 'details',
        status varchar(30) NOT NULL DEFAULT 'awaiting_input'
          CHECK (status IN ('awaiting_input','ready','running','completed','failed','paused','cancelled')),
        fields jsonb NOT NULL DEFAULT '{}', result jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE UNIQUE INDEX uq_workflow_foreground ON chat_workflows(session_id)
        WHERE status IN ('awaiting_input','ready','running','failed')""")
    op.execute("""CREATE TABLE chat_workflow_actions (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        session_id uuid NOT NULL REFERENCES admin_chat_sessions(id) ON DELETE CASCADE,
        workflow_id uuid NOT NULL REFERENCES chat_workflows(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        client_operation_id uuid NOT NULL, request jsonb NOT NULL, response jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_workflow_client_operation UNIQUE(session_id, client_operation_id)
    )""")
    for table in ('chat_workflows', 'chat_workflow_actions'):
        op.execute(f'CREATE INDEX ix_{table}_tenant_id ON {table}(tenant_id)')
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY private_workflow ON {table}
          USING (tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid
            AND user_id = NULLIF(current_setting('app.current_user',true),'')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid
            AND user_id = NULLIF(current_setting('app.current_user',true),'')::uuid)""")
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO leadpulse_app')


def downgrade():
    op.execute('DROP TABLE chat_workflow_actions')
    op.execute('DROP TABLE chat_workflows')
    op.execute('ALTER TABLE leads DROP COLUMN person_name')
