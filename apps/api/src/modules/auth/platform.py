"""Explicit platform provisioning boundary; ordinary tenant routes never bypass RLS."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from src.core.db import set_tenant_context
from src.core.deps import DBSessionDep
from src.core.errors import ConflictError, NotFoundError
from src.core.rbac import RequireSuperAdmin
from src.modules.compliance.models import AuditLog

from .models import Tenant, UserRole
from .schemas import InviteIn, TenantOut
from .service import AuthService

router = APIRouter(prefix="/platform/tenants", tags=["platform"])


class ProvisionIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    owner_email: EmailStr


class OwnerInviteIn(BaseModel):
    email: EmailStr


@router.get("", response_model=list[TenantOut])
async def list_tenants(db: DBSessionDep, _: RequireSuperAdmin) -> list[Tenant]:
    return list((await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))).scalars())


@router.post("")
async def provision(
    payload: ProvisionIn, db: DBSessionDep, claims: RequireSuperAdmin
) -> dict[str, Any]:
    if await AuthService(db).tenants.get_by_slug(payload.slug):
        raise ConflictError("Company code is already in use")
    tenant = Tenant(name=payload.name, slug=payload.slug)
    db.add(tenant)
    await db.flush()
    await set_tenant_context(db, tenant.id)
    db.add(
        AuditLog(
            tenant_id=tenant.id,
            actor_id=UUID(claims["sub"]),
            action="platform_create_tenant",
            entity="tenant",
            entity_id=str(tenant.id),
        )
    )
    invitation = await AuthService(db).create_invitation(
        tenant.id,
        UUID(claims["sub"]),
        InviteIn(email=payload.owner_email, role=UserRole.TENANT_OWNER),
    )
    return {"tenant": TenantOut.model_validate(tenant), "invitation_token": invitation.token}


@router.post("/{tenant_id}/owner-invitations")
async def invite_owner(
    tenant_id: UUID, payload: OwnerInviteIn, db: DBSessionDep, claims: RequireSuperAdmin
) -> dict[str, Any]:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant")
    await set_tenant_context(db, tenant_id)
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_id=UUID(claims["sub"]),
            action="platform_invite_owner",
            entity="tenant",
            entity_id=str(tenant_id),
        )
    )
    inv = await AuthService(db).create_invitation(
        tenant_id, UUID(claims["sub"]), InviteIn(email=payload.email, role=UserRole.TENANT_OWNER)
    )
    return {"tenant_slug": tenant.slug, "invitation_token": inv.token}
