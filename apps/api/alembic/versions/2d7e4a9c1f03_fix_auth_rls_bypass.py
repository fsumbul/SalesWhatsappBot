"""fix auth RLS bypass: exempt refresh_tokens/invitations from tenant_isolation

Revision ID: 2d7e4a9c1f03
Revises: 9c4e5f1a2b6d
Create Date: 2026-07-23 19:00:00

This fixes a critical, live-reproduced bug: registering a new tenant,
logging in, refreshing a token, and accepting an invitation were ALL
broken by Row-Level Security, because none of those requests carry a
JWT yet — there is no `tid` claim to derive `app.current_tenant` from,
so `get_db()` sets the GUC to '' and every tenant-scoped query returns
nothing (deny-by-default RLS).

For `users`, the fix is in application code (src/modules/auth/service.py
now calls `set_tenant_context()` explicitly the moment a tenant becomes
known — e.g. right after looking up a tenant by slug during login).

For `refresh_tokens` and `invitations`, that fix doesn't work: both are
looked up by an unguessable secret (a refresh token's hash, an
invitation's token) *before* the tenant is known — the tenant is only
discovered as a side effect of finding the row. RLS's USING clause
requires knowing the tenant ahead of time, which is exactly what these
two lookups can't do. Their actual security boundary is possession of
the secret (48 bytes of entropy for refresh tokens), not a tenant_id
row filter — the same class of table Kompass/Europages taught us about
robots.txt in Phase D: the pattern the code actually needs doesn't fit
the mechanism, so keep the mechanism from the wrong layer off it rather
than bending the wrong thing to fit.

Confirmed safe to drop RLS on just these two tables: grepped
apps/api/src/modules/auth/ for every call site — `InvitationRepo.
list_by_tenant()` (the one place that *would* need tenant-scoped
row visibility) is dead code, never called anywhere. Every other
access is either the secret-lookup pattern above, or an explicit
`.where(tenant_id == ...)` filter already in the service/repository
code (belt-and-suspenders, independent of RLS).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "2d7e4a9c1f03"
down_revision = "9c4e5f1a2b6d"
branch_labels = None
depends_on = None

_TABLES = ["refresh_tokens", "invitations"]


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
            """
        )
