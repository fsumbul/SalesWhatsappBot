"""Repository layer for auth models."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Invitation, RefreshToken, Tenant, User


class TenantRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_slug(self, slug: str) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, tenant_id: UUID) -> Tenant | None:
        return await self.session.get(Tenant, tenant_id)

    async def create(self, tenant: Tenant) -> Tenant:
        self.session.add(tenant)
        await self.session.flush()
        return tenant


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, tenant_id: UUID, email: str) -> User | None:
        stmt = select(User).where(User.tenant_id == tenant_id, User.email == email.lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def list_by_tenant(self, tenant_id: UUID) -> list[User]:
        stmt = select(User).where(User.tenant_id == tenant_id).order_by(User.created_at.desc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user


class InvitationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_token(self, token: str) -> Invitation | None:
        stmt = select(Invitation).where(Invitation.token == token)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_tenant(self, tenant_id: UUID) -> list[Invitation]:
        stmt = (
            select(Invitation)
            .where(Invitation.tenant_id == tenant_id)
            .order_by(Invitation.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, invitation: Invitation) -> Invitation:
        self.session.add(invitation)
        await self.session.flush()
        return invitation


class RefreshTokenRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, rt: RefreshToken) -> RefreshToken:
        self.session.add(rt)
        await self.session.flush()
        return rt
