"""Private admin sessions and atomic, idempotent audited turns.

Revision ID: e71ab2c903df
Revises: d9a0917e26f1
"""
from alembic import op

revision = 'e71ab2c903df'
down_revision = 'd9a0917e26f1'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE admin_chat_sessions (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title varchar(120) NOT NULL, sequence integer NOT NULL DEFAULT 0,
        context jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    )''')
    op.execute('''CREATE TABLE admin_chat_turns (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        session_id uuid NOT NULL REFERENCES admin_chat_sessions(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        client_message_id uuid NOT NULL, sequence integer NOT NULL,
        text text NOT NULL, response jsonb NOT NULL, audit jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_admin_turn_client UNIQUE(session_id, client_message_id),
        CONSTRAINT uq_admin_turn_sequence UNIQUE(session_id, sequence)
    )''')
    for table in ('admin_chat_sessions', 'admin_chat_turns'):
        op.execute(f'CREATE INDEX ix_{table}_tenant_id ON {table}(tenant_id)')
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f'''CREATE POLICY private_admin_chat ON {table}
          USING (tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid
            AND user_id = NULLIF(current_setting('app.current_user',true),'')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant',true),'')::uuid
            AND user_id = NULLIF(current_setting('app.current_user',true),'')::uuid)''')
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO leadpulse_app')


def downgrade():
    op.execute('DROP TABLE admin_chat_turns')
    op.execute('DROP TABLE admin_chat_sessions')
