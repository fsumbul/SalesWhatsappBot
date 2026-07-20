"""initial schema with rls

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


# ----------------------------------------------------------------------
# Enums (postgresql.ENUM with create_type=False so create_table doesn't
# auto-emit CREATE TYPE; we explicitly create them once at the top of
# upgrade() via raw DDL.)
# ----------------------------------------------------------------------
def _pgenum(*vals: str, name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*vals, name=name, create_type=False)


role_enum = _pgenum(
    "super_admin", "tenant_owner", "sales_manager", "sales_agent", "viewer",
    name="user_role",
)
tenant_status_enum = _pgenum("active", "suspended", "cancelled", name="tenant_status")
tenant_plan_enum = _pgenum(
    "trial", "starter", "growth", "enterprise", name="tenant_plan",
)
keyword_type_enum = _pgenum("positive", "negative", name="keyword_type")
campaign_status_enum = _pgenum(
    "draft", "discovering", "ready", "running", "paused", "completed", "archived",
    name="campaign_status",
)
lead_status_enum = _pgenum(
    "discovered", "enriching", "enriched", "discarded", "qualified",
    "ready_to_contact", "blocked_by_compliance", "contacted", "replied",
    "interested", "not_interested", "quoted", "won", "lost", "blacklisted",
    name="lead_status",
)
lead_priority_enum = _pgenum("low", "medium", "high", name="lead_priority")
contact_type_enum = _pgenum("phone", "email", name="contact_type")
consent_status_enum = _pgenum("unknown", "opt_in", "opt_out", name="consent_status")
optout_source_enum = _pgenum(
    "user_reply", "manual", "iys", "gdpr", "import", name="optout_source",
)
compliance_result_enum = _pgenum("pass", "block", "defer", name="compliance_result")
template_status_enum = _pgenum(
    "draft", "submitted", "approved", "rejected", "paused", name="template_status",
)
template_category_enum = _pgenum(
    "marketing", "utility", "authentication", name="template_category",
)
outreach_status_enum = _pgenum(
    "pending", "scheduled", "sending", "sent", "delivered", "read",
    "failed", "blocked", "deferred", "canceled",
    name="outreach_job_status",
)
sender_tier_enum = _pgenum("T1", "T2", "T3", "T4", name="sender_tier")
sender_health_enum = _pgenum(
    "healthy", "warning", "flagged", "banned", name="sender_health_status",
)
conversation_status_enum = _pgenum("open", "resolved", "archived", name="conversation_status")
message_direction_enum = _pgenum("outbound", "inbound", name="message_direction")
message_type_enum = _pgenum(
    "text", "template", "image", "document", "system", name="message_type",
)


# Values used by explicit CREATE TYPE inside upgrade().
_ALL_ENUMS = [
    ("user_role", ["super_admin", "tenant_owner", "sales_manager", "sales_agent", "viewer"]),
    ("tenant_status", ["active", "suspended", "cancelled"]),
    ("tenant_plan", ["trial", "starter", "growth", "enterprise"]),
    ("keyword_type", ["positive", "negative"]),
    ("campaign_status", ["draft", "discovering", "ready", "running", "paused", "completed", "archived"]),
    ("lead_status", [
        "discovered", "enriching", "enriched", "discarded", "qualified",
        "ready_to_contact", "blocked_by_compliance", "contacted", "replied",
        "interested", "not_interested", "quoted", "won", "lost", "blacklisted",
    ]),
    ("lead_priority", ["low", "medium", "high"]),
    ("contact_type", ["phone", "email"]),
    ("consent_status", ["unknown", "opt_in", "opt_out"]),
    ("optout_source", ["user_reply", "manual", "iys", "gdpr", "import"]),
    ("compliance_result", ["pass", "block", "defer"]),
    ("template_status", ["draft", "submitted", "approved", "rejected", "paused"]),
    ("template_category", ["marketing", "utility", "authentication"]),
    ("outreach_job_status", [
        "pending", "scheduled", "sending", "sent", "delivered", "read",
        "failed", "blocked", "deferred", "canceled",
    ]),
    ("sender_tier", ["T1", "T2", "T3", "T4"]),
    ("sender_health_status", ["healthy", "warning", "flagged", "banned"]),
    ("conversation_status", ["open", "resolved", "archived"]),
    ("message_direction", ["outbound", "inbound"]),
    ("message_type", ["text", "template", "image", "document", "system"]),
]


TENANT_SCOPED_TABLES = [
    "users",
    "invitations",
    "refresh_tokens",
    "sectors",
    "sector_keywords",
    "sector_target_customers",
    "sector_countries",
    "sector_message_angles",
    "campaigns",
    "leads",
    "lead_contacts",
    "lead_sources",
    "lead_enrichment",
    "opt_outs",
    "compliance_checks",
    "audit_logs",
    "message_templates",
    "sender_profiles",
    "outreach_jobs",
    "conversations",
    "messages",
]


def upgrade() -> None:
    # -- Enum types (created once via raw DDL) --
    bind = op.get_bind()
    for name, values in _ALL_ENUMS:
        values_sql = ", ".join(f"'{v}'" for v in values)
        bind.execute(sa.text(f"CREATE TYPE {name} AS ENUM ({values_sql})"))

    _ts_cols = [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]
    _id_col = lambda: sa.Column(
        "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
    )
    _tid_col = lambda: sa.Column(
        "tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True
    )

    # ---- tenants ----
    op.create_table(
        "tenants",
        _id_col(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("plan", tenant_plan_enum, nullable=False, server_default="trial"),
        sa.Column("status", tenant_status_enum, nullable=False, server_default="active"),
        sa.Column("wa_business_account_id", sa.String(80), nullable=True),
        sa.Column("default_locale", sa.String(10), nullable=False, server_default="tr"),
        sa.Column("default_timezone", sa.String(40), nullable=False, server_default="Europe/Istanbul"),
        *_ts_cols,
    )

    # ---- users ----
    op.create_table(
        "users",
        _id_col(), _tid_col(),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", role_enum, nullable=False, server_default="sales_agent"),
        sa.Column("full_name", sa.String(160), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )

    op.create_table(
        "invitations",
        _id_col(), _tid_col(),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "refresh_tokens",
        _id_col(), _tid_col(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # ---- sectors + children ----
    op.create_table(
        "sectors",
        _id_col(), _tid_col(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("default_language", sa.String(10), nullable=False, server_default="tr"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_ts_cols,
        sa.UniqueConstraint("tenant_id", "slug", name="uq_sectors_tenant_slug"),
    )
    for tbl, extra_cols in [
        ("sector_keywords", [
            sa.Column("keyword", sa.String(160), nullable=False),
            sa.Column("keyword_type", keyword_type_enum, nullable=False, server_default="positive"),
            sa.Column("language", sa.String(10), nullable=False, server_default="tr"),
            sa.Column("weight", sa.Integer, nullable=False, server_default="1"),
        ]),
        ("sector_target_customers", [
            sa.Column("customer_type", sa.String(160), nullable=False),
            sa.Column("language", sa.String(10), nullable=False, server_default="tr"),
        ]),
        ("sector_countries", [
            sa.Column("country_code", sa.String(2), nullable=False),
            sa.Column("priority", sa.Integer, nullable=False, server_default="1"),
            sa.Column("timezone", sa.String(40), nullable=True),
        ]),
        ("sector_message_angles", [
            sa.Column("angle", sa.String(255), nullable=False),
            sa.Column("language", sa.String(10), nullable=False, server_default="tr"),
        ]),
    ]:
        op.create_table(
            tbl,
            _id_col(), _tid_col(),
            sa.Column("sector_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
            *extra_cols,
            *_ts_cols,
            sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="CASCADE"),
        )

    # ---- campaigns + leads ----
    op.create_table(
        "campaigns",
        _id_col(), _tid_col(),
        sa.Column("sector_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", campaign_status_enum, nullable=False, server_default="draft"),
        sa.Column("filters", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("daily_quota", sa.Integer, nullable=False, server_default="200"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "leads",
        _id_col(), _tid_col(),
        sa.Column("sector_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False, index=True),
        sa.Column("website", sa.String(500), nullable=True),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("country", sa.String(2), nullable=True, index=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("status", lead_status_enum, nullable=False, server_default="discovered", index=True),
        sa.Column("fit_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("priority", lead_priority_enum, nullable=False, server_default="low"),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_leads_tenant_status", "leads", ["tenant_id", "status"])
    op.create_index("ix_leads_domain", "leads", ["domain"])

    op.create_table(
        "lead_contacts",
        _id_col(), _tid_col(),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("type", contact_type_enum, nullable=False),
        sa.Column("raw_value", sa.String(255), nullable=False),
        sa.Column("normalized_value", sa.String(255), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("is_valid", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_whatsapp", sa.Boolean, nullable=True),
        sa.Column("wa_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_status", consent_status_enum, nullable=False, server_default="unknown"),
        *_ts_cols,
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("lead_id", "type", "normalized_value", name="uq_lead_contact"),
    )
    op.create_index("ix_lead_contacts_e164", "lead_contacts", ["normalized_value"])

    op.create_table(
        "lead_sources",
        _id_col(), _tid_col(),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("raw_data", postgresql.JSONB, nullable=False, server_default="{}"),
        *_ts_cols,
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "lead_enrichment",
        _id_col(), _tid_col(),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True, index=True),
        sa.Column("fit_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("employees_est", sa.Integer, nullable=True),
        sa.Column("categories", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("contact_page_url", sa.String(500), nullable=True),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
    )

    # ---- compliance ----
    op.create_table(
        "opt_outs",
        _id_col(), _tid_col(),
        sa.Column("phone_e164", sa.String(32), nullable=False, index=True),
        sa.Column("source", optout_source_enum, nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        *_ts_cols,
        sa.UniqueConstraint("tenant_id", "phone_e164", name="uq_optouts_tenant_phone"),
    )
    op.create_table(
        "compliance_checks",
        _id_col(), _tid_col(),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("check_type", sa.String(40), nullable=False),
        sa.Column("result", compliance_result_enum, nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["lead_contacts.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "audit_logs",
        _id_col(), _tid_col(),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        *_ts_cols,
    )

    # ---- outreach ----
    op.create_table(
        "message_templates",
        _id_col(), _tid_col(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("category", template_category_enum, nullable=False, server_default="marketing"),
        sa.Column("status", template_status_enum, nullable=False, server_default="draft"),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("variables", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("wa_template_id", sa.String(120), nullable=True),
        sa.Column("sector_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        *_ts_cols,
        sa.ForeignKeyConstraint(["sector_id"], ["sectors.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "name", "language", name="uq_templates_tenant_name_lang"),
    )
    op.create_table(
        "sender_profiles",
        _id_col(), _tid_col(),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("phone_number_id", sa.String(80), nullable=False, unique=True),
        sa.Column("business_account_id", sa.String(80), nullable=True),
        sa.Column("tier", sender_tier_enum, nullable=False, server_default="T1"),
        sa.Column("quality_rating", sa.String(20), nullable=True),
        sa.Column("health_status", sender_health_enum, nullable=False, server_default="healthy"),
        sa.Column("daily_sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("daily_cap", sa.Integer, nullable=False, server_default="1000"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_ts_cols,
    )
    op.create_table(
        "outreach_jobs",
        _id_col(), _tid_col(),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("variables", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", outreach_status_enum, nullable=False, server_default="pending"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("wa_message_id", sa.String(120), nullable=True, index=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        *_ts_cols,
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["lead_contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["message_templates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sender_id"], ["sender_profiles.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_outreach_jobs_tenant_status", "outreach_jobs", ["tenant_id", "status"])
    op.create_index("ix_outreach_jobs_scheduled_for", "outreach_jobs", ["scheduled_for"])

    op.create_table(
        "conversations",
        _id_col(), _tid_col(),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", conversation_status_enum, nullable=False, server_default="open"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unread_count", sa.Integer, nullable=False, server_default="0"),
        *_ts_cols,
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["lead_contacts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "contact_id", name="uq_conversations_tenant_contact"),
    )
    op.create_table(
        "messages",
        _id_col(), _tid_col(),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("direction", message_direction_enum, nullable=False),
        sa.Column("message_type", message_type_enum, nullable=False, server_default="text"),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("media_url", sa.String(500), nullable=True),
        sa.Column("wa_message_id", sa.String(120), nullable=True, index=True),
        sa.Column("outreach_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw", postgresql.JSONB, nullable=False, server_default="{}"),
        *_ts_cols,
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["outreach_job_id"], ["outreach_jobs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])

    # ---- Row-Level Security ----
    # `app.current_tenant` is set per request via `SET LOCAL` (see db.set_tenant_context).
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")

    for tbl in [
        "messages", "conversations", "outreach_jobs", "sender_profiles",
        "message_templates", "audit_logs", "compliance_checks", "opt_outs",
        "lead_enrichment", "lead_sources", "lead_contacts", "leads", "campaigns",
        "sector_message_angles", "sector_countries", "sector_target_customers",
        "sector_keywords", "sectors", "refresh_tokens", "invitations", "users",
        "tenants",
    ]:
        op.drop_table(tbl)

    for e in (
        message_type_enum, message_direction_enum, conversation_status_enum,
        sender_health_enum, sender_tier_enum, outreach_status_enum,
        template_category_enum, template_status_enum, compliance_result_enum,
        optout_source_enum, consent_status_enum, contact_type_enum,
        lead_priority_enum, lead_status_enum, campaign_status_enum,
        keyword_type_enum, tenant_plan_enum, tenant_status_enum, role_enum,
    ):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {e.name}"))
