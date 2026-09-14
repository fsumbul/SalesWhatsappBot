"""Auth Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import TenantPlan, TenantStatus, UserRole


class TenantRegisterIn(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=120)
    tenant_slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    admin_email: EmailStr
    admin_password: str = Field(min_length=8, max_length=200)
    admin_full_name: str | None = Field(default=None, max_length=120)


class LoginIn(BaseModel):
    tenant_slug: str
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class InviteIn(BaseModel):
    email: EmailStr
    role: UserRole


class AcceptInviteIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=200)
    full_name: str | None = Field(default=None, max_length=120)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    slug: str
    plan: TenantPlan
    status: TenantStatus
    default_locale: str
    default_timezone: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    email: str
    full_name: str | None
    role: UserRole
    is_active: bool
    locale: str
    timezone: str
    last_login_at: datetime | None
    created_at: datetime


class MeOut(BaseModel):
    user: UserOut
    tenant: TenantOut


class UserPatchIn(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    full_name: str | None = None
    locale: str | None = None
    timezone: str | None = None


class InvitationOut(BaseModel):
    token: str
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    email: str
    role: UserRole
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime
