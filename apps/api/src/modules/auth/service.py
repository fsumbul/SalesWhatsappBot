"""Auth service — orchestrates tenant registration, login, refresh, invitations."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.errors import ConflictError, NotFoundError, UnauthorizedError, ValidationError
from src.core.security import create_token, hash_password, verify_password

from .models import Invitation, RefreshToken, Tenant, TenantPlan, TenantStatus, User, UserRole
from .repository import InvitationRepo, RefreshTokenRepo, TenantRepo, UserRepo
from .schemas import (
    AcceptInviteIn,
    InviteIn,
    LoginIn,
    TenantRegisterIn,
    TokenPair,
)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tenants = TenantRepo(session)
        self.users = UserRepo(session)
        self.invitations = InvitationRepo(session)
        self.refresh_tokens = RefreshTokenRepo(session)
        self.settings = get_settings()

    # --- Registration -------------------------------------------------------

    async def register_tenant(self, data: TenantRegisterIn) -> tuple[Tenant, User]:
        existing = await self.tenants.get_by_slug(data.tenant_slug)
        if existing is not None:
            raise ConflictError(f"Tenant slug '{data.tenant_slug}' already taken")

        tenant = Tenant(
            name=data.tenant_name,
            slug=data.tenant_slug,
            plan=TenantPlan.TRIAL,
            status=TenantStatus.ACTIVE,
        )
        await self.tenants.create(tenant)

        user = User(
            tenant_id=tenant.id,
            email=data.admin_email.lower(),
            password_hash=hash_password(data.admin_password),
            full_name=data.admin_full_name,
            role=UserRole.TENANT_OWNER,
            is_active=True,
        )
        await self.users.create(user)
        await self.session.commit()
        return tenant, user

    # --- Login --------------------------------------------------------------

    async def login(
        self, data: LoginIn, *, user_agent: str | None, ip: str | None
    ) -> tuple[User, TokenPair]:
        tenant = await self.tenants.get_by_slug(data.tenant_slug)
        if tenant is None:
            raise UnauthorizedError("Invalid credentials")
        if tenant.status == TenantStatus.SUSPENDED:
            raise UnauthorizedError("Tenant suspended")

        user = await self.users.get_by_email(tenant.id, data.email)
        if user is None or not user.is_active:
            raise UnauthorizedError("Invalid credentials")
        if not verify_password(data.password, user.password_hash):
            raise UnauthorizedError("Invalid credentials")

        user.last_login_at = datetime.now(UTC)
        tokens = await self._issue_tokens(user, user_agent=user_agent, ip=ip)
        await self.session.commit()
        return user, tokens

    # --- Refresh ------------------------------------------------------------

    async def refresh(
        self, refresh_token: str, *, user_agent: str | None, ip: str | None
    ) -> TokenPair:
        token_hash = _hash_token(refresh_token)
        rt = await self.refresh_tokens.get_active_by_hash(token_hash)
        if rt is None:
            raise UnauthorizedError("Invalid refresh token")
        if rt.expires_at < datetime.now(UTC):
            raise UnauthorizedError("Refresh token expired")

        user = await self.users.get_by_id(rt.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("User inactive")

        rt.revoked_at = datetime.now(UTC)
        tokens = await self._issue_tokens(
            user, user_agent=user_agent, ip=ip, previous_id=rt.id
        )
        await self.session.commit()
        return tokens

    async def logout(self, refresh_token: str) -> None:
        rt = await self.refresh_tokens.get_active_by_hash(_hash_token(refresh_token))
        if rt is not None:
            rt.revoked_at = datetime.now(UTC)
            await self.session.commit()

    async def _issue_tokens(
        self,
        user: User,
        *,
        user_agent: str | None,
        ip: str | None,
        previous_id: UUID | None = None,
    ) -> TokenPair:
        access = create_token(
            subject=user.id,
            tenant_id=user.tenant_id,
            token_type="access",  # noqa: S106 - JWT token category, not a secret
            extra_claims={"role": user.role.value, "email": user.email},
        )
        refresh_plain = secrets.token_urlsafe(48)
        rt = RefreshToken(
            user_id=user.id,
            tenant_id=user.tenant_id,
            token_hash=_hash_token(refresh_plain),
            expires_at=datetime.now(UTC) + timedelta(days=self.settings.jwt_refresh_ttl_days),
            user_agent=(user_agent or "")[:255],
            ip_address=ip,
            replaced_by_id=None,
        )
        await self.refresh_tokens.create(rt)
        if previous_id is not None:
            prev = await self.session.get(RefreshToken, previous_id)
            if prev is not None:
                prev.replaced_by_id = rt.id
        return TokenPair(
            access_token=access,
            refresh_token=refresh_plain,
            expires_in=self.settings.jwt_access_ttl_minutes * 60,
        )

    # --- Invitations --------------------------------------------------------

    async def create_invitation(
        self, tenant_id: UUID, inviter_id: UUID, data: InviteIn
    ) -> Invitation:
        existing = await self.users.get_by_email(tenant_id, data.email)
        if existing is not None:
            raise ConflictError("User with this email already exists")
        inv = Invitation(
            tenant_id=tenant_id,
            email=data.email.lower(),
            role=data.role,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            invited_by_id=inviter_id,
        )
        await self.invitations.create(inv)
        await self.session.commit()
        return inv

    async def accept_invitation(self, data: AcceptInviteIn) -> User:
        inv = await self.invitations.get_by_token(data.token)
        if inv is None:
            raise NotFoundError("Invitation")
        if inv.accepted_at is not None:
            raise ValidationError("Invitation already accepted")
        if inv.expires_at < datetime.now(UTC):
            raise ValidationError("Invitation expired")

        existing = await self.users.get_by_email(inv.tenant_id, inv.email)
        if existing is not None:
            raise ConflictError("User already exists")

        user = User(
            tenant_id=inv.tenant_id,
            email=inv.email,
            password_hash=hash_password(data.password),
            full_name=data.full_name,
            role=inv.role,
            is_active=True,
        )
        await self.users.create(user)
        inv.accepted_at = datetime.now(UTC)
        await self.session.commit()
        return user
