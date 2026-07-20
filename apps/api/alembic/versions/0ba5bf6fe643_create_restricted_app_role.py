"""create restricted, non-superuser app role (RLS enforcement)

Postgres never enforces Row-Level Security for a superuser connection —
not even with FORCE ROW LEVEL SECURITY (see migration 0001_initial). The
role migrations run as (typically the Postgres bootstrap user, e.g.
`leadpulse`) is a superuser, so it must never be the role the running
API/worker processes connect as. This migration creates a dedicated
`leadpulse_app` role with only the privileges the app actually needs
(row CRUD + sequence usage, no DDL, no BYPASSRLS) and grants it on both
existing and future tables/sequences in `public`.

The app's `DATABASE_URL` must point at this role from here on;
`MIGRATIONS_DATABASE_URL` (or `DATABASE_URL` if unset) — the privileged
role — is what actually runs `alembic upgrade head`.

Revision ID: 0ba5bf6fe643
Revises: b94554138052
Create Date: 2026-07-20 00:00:00
"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0ba5bf6fe643"
down_revision = "b94554138052"
branch_labels = None
depends_on = None

_APP_ROLE = "leadpulse_app"
_DEFAULT_DEV_PASSWORD = "leadpulse_app_dev"


def _app_role_password() -> str:
    # Production must set LEADPULSE_APP_DB_PASSWORD explicitly (see
    # docs/runbook.md); the fallback is a dev-only placeholder, same
    # convention as the `leadpulse_dev` superuser password already
    # committed in docker-compose.yml.
    password = os.environ.get("LEADPULSE_APP_DB_PASSWORD", _DEFAULT_DEV_PASSWORD)
    # Escape single quotes for the SQL string literal below. The value is
    # operator-controlled config (env var), not end-user input.
    return password.replace("'", "''")


def upgrade() -> None:
    password = _app_role_password()
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
            CREATE ROLE {_APP_ROLE} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS;
          ELSE
            ALTER ROLE {_APP_ROLE} PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS;
          END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE};")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE};"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE};")
    # So tables/sequences added by *future* migrations (run by the
    # privileged role) don't silently need a follow-up grant.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE};"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {_APP_ROLE};"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_APP_ROLE};"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {_APP_ROLE};"
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {_APP_ROLE};")
    op.execute(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {_APP_ROLE};")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_APP_ROLE};")
    op.execute(f"DROP ROLE IF EXISTS {_APP_ROLE};")
