"""Auth HTTP router."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status
from fastapi.responses import Response

from src.core.deps import ClaimsDep, ClientIPDep, DBSessionDep
from src.core.errors import NotFoundError
from src.core.rbac import RequireOwner

from .repository import TenantRepo, UserRepo
from .schemas import (
    AcceptInviteIn,
    InvitationOut,
    InviteIn,
    LoginIn,
    MeOut,
    RefreshIn,
    TenantOut,
    TenantRegisterIn,
    TokenPair,
    UserOut,
    UserPatchIn,
)
from .service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/register-tenant",
    response_model=MeOut,
    status_code=status.HTTP_201_CREATED,
)
async def register_tenant(payload: TenantRegisterIn, db: DBSessionDep) -> MeOut:
    service = AuthService(db)
    tenant, user = await service.register_tenant(payload)
    return MeOut(user=UserOut.model_validate(user), tenant=TenantOut.model_validate(tenant))


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginIn,
    db: DBSessionDep,
    ip: ClientIPDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenPair:
    service = AuthService(db)
    _user, tokens = await service.login(payload, user_agent=user_agent, ip=ip)
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshIn,
    db: DBSessionDep,
    ip: ClientIPDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenPair:
    service = AuthService(db)
    return await service.refresh(payload.refresh_token, user_agent=user_agent, ip=ip)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(payload: RefreshIn, db: DBSessionDep) -> Response:
    service = AuthService(db)
    await service.logout(payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invite", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
async def invite_user(
    payload: InviteIn,
    db: DBSessionDep,
    claims: RequireOwner,
) -> InvitationOut:
    service = AuthService(db)
    inv = await service.create_invitation(
        tenant_id=UUID(claims["tid"]),
        inviter_id=UUID(claims["sub"]),
        data=payload,
    )
    return InvitationOut.model_validate(inv)


@router.post("/accept-invite", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def accept_invite(payload: AcceptInviteIn, db: DBSessionDep) -> UserOut:
    service = AuthService(db)
    user = await service.accept_invitation(payload)
    return UserOut.model_validate(user)


@router.get("/me", response_model=MeOut)
async def me(claims: ClaimsDep, db: DBSessionDep) -> MeOut:
    user = await UserRepo(db).get_by_id(UUID(claims["sub"]))
    if user is None:
        raise NotFoundError("User")
    tenant = await TenantRepo(db).get_by_id(UUID(claims["tid"]))
    if tenant is None:
        raise NotFoundError("Tenant")
    return MeOut(user=UserOut.model_validate(user), tenant=TenantOut.model_validate(tenant))


# --- Users management ---


@users_router.get("", response_model=list[UserOut])
async def list_users(db: DBSessionDep, claims: ClaimsDep) -> list[UserOut]:
    tenant_id = UUID(claims["tid"])
    users = await UserRepo(db).list_by_tenant(tenant_id)
    return [UserOut.model_validate(u) for u in users]


@users_router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: UUID,
    payload: UserPatchIn,
    db: DBSessionDep,
    claims: RequireOwner,
) -> UserOut:
    repo = UserRepo(db)
    user = await repo.get_by_id(user_id)
    if user is None or user.tenant_id != UUID(claims["tid"]):
        raise NotFoundError("User", str(user_id))
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.locale is not None:
        user.locale = payload.locale
    if payload.timezone is not None:
        user.timezone = payload.timezone
    await db.commit()
    return UserOut.model_validate(user)
