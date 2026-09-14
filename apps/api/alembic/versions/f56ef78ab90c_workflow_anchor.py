"""Keep workflows beside the conversation turn that started them.

Revision ID: f56ef78ab90c
Revises: f45de67fa89b
"""

from alembic import op

revision = "f56ef78ab90c"
down_revision = "f45de67fa89b"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE chat_workflows ADD COLUMN anchor_sequence integer NOT NULL DEFAULT 0 CHECK (anchor_sequence >= 0)"
    )
    # An exact chat-operation receipt wins; form starts use the preceding turn.
    # Tenant, user and session predicates are intentional even under a DDL role.
    op.execute("""
        UPDATE chat_workflows w SET anchor_sequence = COALESCE(
            (SELECT MIN(t.sequence) FROM admin_chat_turns t
             JOIN chat_workflow_actions a ON a.client_operation_id = t.client_message_id
               AND a.session_id = t.session_id AND a.tenant_id = t.tenant_id
               AND a.user_id = t.user_id
             WHERE a.workflow_id = w.id AND a.request ? 'start'
               AND t.session_id = w.session_id AND t.tenant_id = w.tenant_id
               AND t.user_id = w.user_id),
            (SELECT MAX(t.sequence) FROM admin_chat_turns t
             WHERE t.session_id = w.session_id AND t.tenant_id = w.tenant_id
               AND t.user_id = w.user_id AND t.created_at <= w.created_at), 0)
    """)


def downgrade():
    op.execute("ALTER TABLE chat_workflows DROP COLUMN anchor_sequence")
