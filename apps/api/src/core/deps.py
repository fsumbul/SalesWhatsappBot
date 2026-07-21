"""FastAPI dependencies shared across modules."""

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_sessionmaker, set_tenant_context
from .errors import UnauthorizedError
from .security import decode_token
from .tenancy import set_current_tenant

_bearer = HTTPBearer(auto_error=False)


async def get_db(
    request: Request,
) -> AsyncIterator[AsyncSession]:
    """DB session with tenant context set (if authenticated)."""
    async with get_sessionmaker()() as session:
        tenant_id: UUID | None = getattr(request.state, "tenant_id", None)
        await set_tenant_context(session, tenant_id)
        try:
            yield session
        finally:
            await set_tenant_context(session, None)


DBSessionDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_claims(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict[str, Any]:
    if creds is None:
        raise UnauthorizedError("Missing bearer token")
    try:
        claims = decode_token(creds.credentials)
    except JWTError as e:
        raise UnauthorizedError(f"Invalid token: {e}") from e
    if claims.get("type") != "access":
        raise UnauthorizedError("Wrong token type")
    tenant_id_raw = claims.get("tid")
    if tenant_id_raw:
        try:
            tenant_uuid = UUID(tenant_id_raw)
        except ValueError as e:
            raise UnauthorizedError("Invalid tenant claim") from e
        request.state.tenant_id = tenant_uuid
        set_current_tenant(tenant_uuid)
    request.state.user_id = UUID(claims["sub"])
    request.state.role = claims.get("role")
    return claims


ClaimsDep = Annotated[dict[str, Any], Depends(get_current_claims)]


def get_client_ip(x_forwarded_for: Annotated[str | None, Header()] = None) -> str:
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return "unknown"


ClientIPDep = Annotated[str, Depends(get_client_ip)]
