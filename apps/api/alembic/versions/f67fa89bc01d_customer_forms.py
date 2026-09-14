"""Same customer request shared by web form and WhatsApp."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f67fa89bc01d"
down_revision = "f56ef78ab90c"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("selection_files", "inbound_message_id", nullable=True)
    op.create_table(
        "selection_form_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("selection_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_selection_form_operation",
        "selection_form_actions",
        ["request_id", "operation_id"],
        unique=True,
    )
    op.execute("ALTER TABLE selection_form_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE selection_form_actions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON selection_form_actions USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid) WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)"
    )


def downgrade():
    op.drop_table("selection_form_actions")
    # Nullable inbound ids intentionally retained to preserve uploaded form files.
