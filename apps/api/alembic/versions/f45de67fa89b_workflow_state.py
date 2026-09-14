"""Private server-owned workflow previews and record choices.
Revision ID: f45de67fa89b
Revises: f34cd56ef78a
"""
from alembic import op
revision = "f45de67fa89b"
down_revision = "f34cd56ef78a"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE chat_workflows ADD COLUMN state jsonb NOT NULL DEFAULT '{}'")


def downgrade():
    op.execute("ALTER TABLE chat_workflows DROP COLUMN state")
