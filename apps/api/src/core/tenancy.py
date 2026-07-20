"""Tenant context helpers.

The current tenant id is propagated via a contextvar so that services and
repositories can enforce isolation without threading `tenant_id` through
every function signature. Postgres RLS provides defence-in-depth via
`set_tenant_context()` from `core.db`.
"""

from contextvars import ContextVar
from uuid import UUID

_current_tenant: ContextVar[UUID | None] = ContextVar("current_tenant", default=None)


def set_current_tenant(tenant_id: UUID | None) -> None:
    _current_tenant.set(tenant_id)


def get_current_tenant() -> UUID | None:
    return _current_tenant.get()


def require_current_tenant() -> UUID:
    tid = _current_tenant.get()
    if tid is None:
        raise RuntimeError("No tenant in context. This request is not tenant-scoped.")
    return tid


__all__ = [
    "get_current_tenant",
    "require_current_tenant",
    "set_current_tenant",
]
