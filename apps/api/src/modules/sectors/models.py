"""Sector ORM models."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.db import Base
from src.core.mixins import TenantScoped, Timestamped, UUIDPrimaryKey


class KeywordType(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class Sector(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "sectors"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_sectors_tenant_slug"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_language: Mapped[str] = mapped_column(String(8), default="tr", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    keywords: Mapped[list[SectorKeyword]] = relationship(
        back_populates="sector", cascade="all, delete-orphan"
    )
    target_customers: Mapped[list[SectorTargetCustomer]] = relationship(
        back_populates="sector", cascade="all, delete-orphan"
    )
    countries: Mapped[list[SectorCountry]] = relationship(
        back_populates="sector", cascade="all, delete-orphan"
    )
    message_angles: Mapped[list[SectorMessageAngle]] = relationship(
        back_populates="sector", cascade="all, delete-orphan"
    )


class SectorKeyword(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "sector_keywords"

    sector_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    keyword: Mapped[str] = mapped_column(String(160), nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="tr", nullable=False)
    keyword_type: Mapped[KeywordType] = mapped_column(
        SAEnum(KeywordType, name="keyword_type", values_callable=lambda e: [x.value for x in e]), default=KeywordType.POSITIVE, nullable=False
    )

    sector: Mapped[Sector] = relationship(back_populates="keywords")


class SectorTargetCustomer(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "sector_target_customers"

    sector_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_type: Mapped[str] = mapped_column(String(160), nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="tr", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    sector: Mapped[Sector] = relationship(back_populates="target_customers")


class SectorCountry(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "sector_countries"

    sector_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)

    sector: Mapped[Sector] = relationship(back_populates="countries")


class SectorMessageAngle(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __tablename__ = "sector_message_angles"

    sector_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    angle: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="tr", nullable=False)

    sector: Mapped[Sector] = relationship(back_populates="message_angles")
