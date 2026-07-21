"""Role-based access control primitives."""

from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends

from .deps import ClaimsDep
from .errors import ForbiddenError


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    TENANT_OWNER = "tenant_owner"
    SALES_MANAGER = "sales_manager"
    SALES_AGENT = "sales_agent"
    VIEWER = "viewer"


_ROLE_ORDER = [
    Role.VIEWER,
    Role.SALES_AGENT,
    Role.SALES_MANAGER,
    Role.TENANT_OWNER,
    Role.SUPER_ADMIN,
]


def role_at_least(actual: str | None, required: Role) -> bool:
    if actual is None:
        return False
    try:
        return _ROLE_ORDER.index(Role(actual)) >= _ROLE_ORDER.index(required)
    except ValueError:
        return False


def require_role(min_role: Role) -> Any:
    async def _dep(claims: ClaimsDep) -> dict[str, Any]:
        if not role_at_least(claims.get("role"), min_role):
            raise ForbiddenError(f"Requires role >= {min_role}")
        return claims

    return Depends(_dep)


RequireAgent = Annotated[dict[str, Any], require_role(Role.SALES_AGENT)]
RequireManager = Annotated[dict[str, Any], require_role(Role.SALES_MANAGER)]
RequireOwner = Annotated[dict[str, Any], require_role(Role.TENANT_OWNER)]
RequireSuperAdmin = Annotated[dict[str, Any], require_role(Role.SUPER_ADMIN)]
