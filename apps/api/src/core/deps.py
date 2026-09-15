"""FastAPI dependencies shared across modules."""

from collections.abc import AsyncIterator
from ipaddress import ip_address, ip_network
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_sessionmaker, reset_tenant_context, set_tenant_context
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
            # A route that hit an unhandled exception mid-transaction (e.g.
            # an IntegrityError during flush) leaves the session in SQLAlchemy's
            # "rolled back due to a previous exception" state — any further
            # `session.execute()` on it (like the set_tenant_context call
            # below) raises PendingRollbackError instead of running, which
            # masks the original error and crashes the ASGI middleware stack
            # (browser sees a bare connection failure, not a clean 4xx/5xx).
            # rollback() is safe to call even when there's nothing pending;
            # it's what clears that state so cleanup can proceed normally.
            await reset_tenant_context(session)


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
    from src.modules.auth.models import Tenant, TenantStatus, User

    try:
        user_id = UUID(claims["sub"])
        tenant_id = UUID(claims["tid"])
    except (KeyError, ValueError, TypeError) as exc:
        raise UnauthorizedError("Invalid identity") from exc
    async with get_sessionmaker()() as identity_session:
        await set_tenant_context(identity_session, tenant_id)
        user = await identity_session.get(User, user_id)
        tenant = await identity_session.get(Tenant, tenant_id)
        if (user is None or user.tenant_id != tenant_id or not user.is_active
                or tenant is None or tenant.status != TenantStatus.ACTIVE):
            raise UnauthorizedError("Account is not active")
        claims["role"] = user.role.value
    request.state.user_id = user_id
    request.state.role = claims.get("role")
    return claims


ClaimsDep = Annotated[dict[str, Any], Depends(get_current_claims)]


def get_client_ip(request: Request, x_forwarded_for: Annotated[str | None, Header()] = None) -> str:
    """Use XFF only through trusted proxy hops, never from the public client.

    Proxies append their address from left to right.  Once the immediate peer is
    trusted, walk the supplied chain from the right and discard every configured
    proxy hop.  The first remaining address is the verified client boundary.
    This avoids accepting a spoofed left-most value when an ingress appends to,
    rather than replaces, a client-supplied XFF header.
    """

    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ip_address(peer)
        trusted = any(
            peer_address in ip_network(cidr, strict=False)
            for cidr in get_settings().trusted_proxy_cidrs_list
        )
    except ValueError:
        trusted = False
    if trusted and x_forwarded_for:
        # Reject the complete header if any hop is malformed rather than
        # recording arbitrary text in auth audit rows / Redis keys.
        try:
            hops = [ip_address(part.strip()) for part in x_forwarded_for.split(",")]
        except ValueError:
            return peer
        if not hops:
            return peer
        trusted_networks = [
            ip_network(cidr, strict=False) for cidr in get_settings().trusted_proxy_cidrs_list
        ]
        for hop in reversed(hops):
            if not any(hop in network for network in trusted_networks):
                return str(hop)
    return peer


ClientIPDep = Annotated[str, Depends(get_client_ip)]
