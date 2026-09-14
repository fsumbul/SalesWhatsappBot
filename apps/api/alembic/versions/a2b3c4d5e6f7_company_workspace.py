"""Durable company workspace and explicit channel agent binding."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "a2b3c4d5e6f7"
down_revision = "c4f9128ab6d0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_versions", sa.Column("revision", sa.Integer(), server_default="0", nullable=False))
    op.add_column("sender_profiles", sa.Column("agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id")))
    for table in ("agent_config_proposals", "agent_test_sessions"):
        columns = [sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
                   sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
                   sa.Column("agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
                   sa.Column("version_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_versions.id"), nullable=False),
                   sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                   sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]
        if table == "agent_config_proposals":
            columns += [sa.Column("base_revision", sa.Integer(), nullable=False),
                        sa.Column("source", sa.String(20), nullable=False),
                        sa.Column("status", sa.String(20), nullable=False),
                        sa.Column("company_config", pg.JSONB(), nullable=False)]
        else:
            columns += [sa.Column("version_revision", sa.Integer(), nullable=False),
                        sa.Column("messages", pg.JSONB(), nullable=False)]
        op.create_table(table, *columns)
        op.create_index(f"ix_{table}_tenant", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (tenant_id::text = current_setting('app.current_tenant', true)) WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true))")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO leadpulse_app")


def downgrade():
    op.drop_table("agent_test_sessions")
    op.drop_table("agent_config_proposals")
    op.drop_column("sender_profiles", "agent_id")
    op.drop_column("agent_versions", "revision")
