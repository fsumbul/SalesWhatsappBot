"""Sector Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import KeywordType


class SectorKeywordIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=160)
    language: str = Field(default="tr", min_length=2, max_length=8)
    keyword_type: KeywordType = KeywordType.POSITIVE


class SectorKeywordOut(SectorKeywordIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID


class SectorTargetCustomerIn(BaseModel):
    customer_type: str = Field(min_length=2, max_length=160)
    language: str = "tr"
    description: str | None = None


class SectorTargetCustomerOut(SectorTargetCustomerIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID


class SectorCountryIn(BaseModel):
    country_code: str = Field(min_length=2, max_length=2)
    timezone: str = "UTC"
    priority: int = Field(default=50, ge=0, le=100)


class SectorCountryOut(SectorCountryIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID


class SectorMessageAngleIn(BaseModel):
    angle: str = Field(min_length=2, max_length=255)
    language: str = "tr"


class SectorMessageAngleOut(SectorMessageAngleIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID


class SectorIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    product_name: str | None = None
    description: str | None = None
    default_language: str = "tr"
    is_active: bool = True


class SectorPatchIn(BaseModel):
    name: str | None = None
    product_name: str | None = None
    description: str | None = None
    default_language: str | None = None
    is_active: bool | None = None


class SectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    name: str
    slug: str
    product_name: str | None
    description: str | None
    default_language: str
    is_active: bool
    created_at: datetime


class SectorDetailOut(SectorOut):
    keywords: list[SectorKeywordOut]
    target_customers: list[SectorTargetCustomerOut]
    countries: list[SectorCountryOut]
    message_angles: list[SectorMessageAngleOut]


class SectorExport(BaseModel):
    sector: SectorIn
    keywords: list[SectorKeywordIn]
    target_customers: list[SectorTargetCustomerIn]
    countries: list[SectorCountryIn]
    message_angles: list[SectorMessageAngleIn]
