"""Sector service — CRUD + import/export + duplicate."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.errors import ConflictError, NotFoundError

from .models import (
    Sector,
    SectorCountry,
    SectorKeyword,
    SectorMessageAngle,
    SectorTargetCustomer,
)
from .repository import SectorRepo
from .schemas import (
    SectorCountryIn,
    SectorExport,
    SectorIn,
    SectorKeywordIn,
    SectorMessageAngleIn,
    SectorPatchIn,
    SectorTargetCustomerIn,
)


class SectorService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SectorRepo(session)

    async def list(self, tenant_id: UUID) -> list[Sector]:
        return await self.repo.list(tenant_id)

    async def get(self, tenant_id: UUID, sector_id: UUID) -> Sector:
        sector = await self.repo.get(tenant_id, sector_id)
        if sector is None:
            raise NotFoundError("Sector", str(sector_id))
        return sector

    async def create(self, tenant_id: UUID, data: SectorIn) -> Sector:
        if await self.repo.get_by_slug(tenant_id, data.slug):
            raise ConflictError(f"Sector slug '{data.slug}' already exists")
        sector = Sector(tenant_id=tenant_id, **data.model_dump())
        await self.repo.add(sector)
        await self.session.commit()
        return await self.get(tenant_id, sector.id)

    async def patch(self, tenant_id: UUID, sector_id: UUID, data: SectorPatchIn) -> Sector:
        sector = await self.get(tenant_id, sector_id)
        for field, value in data.model_dump(exclude_none=True).items():
            setattr(sector, field, value)
        await self.session.commit()
        return sector

    async def delete(self, tenant_id: UUID, sector_id: UUID) -> None:
        sector = await self.get(tenant_id, sector_id)
        await self.session.delete(sector)
        await self.session.commit()

    # --- Child collections helpers ---

    async def add_keyword(self, tenant_id: UUID, sector_id: UUID, data: SectorKeywordIn) -> SectorKeyword:
        sector = await self.get(tenant_id, sector_id)
        obj = SectorKeyword(tenant_id=tenant_id, sector_id=sector.id, **data.model_dump())
        self.session.add(obj)
        await self.session.commit()
        return obj

    async def add_target_customer(
        self, tenant_id: UUID, sector_id: UUID, data: SectorTargetCustomerIn
    ) -> SectorTargetCustomer:
        sector = await self.get(tenant_id, sector_id)
        obj = SectorTargetCustomer(tenant_id=tenant_id, sector_id=sector.id, **data.model_dump())
        self.session.add(obj)
        await self.session.commit()
        return obj

    async def add_country(
        self, tenant_id: UUID, sector_id: UUID, data: SectorCountryIn
    ) -> SectorCountry:
        sector = await self.get(tenant_id, sector_id)
        obj = SectorCountry(tenant_id=tenant_id, sector_id=sector.id, **data.model_dump())
        self.session.add(obj)
        await self.session.commit()
        return obj

    async def add_message_angle(
        self, tenant_id: UUID, sector_id: UUID, data: SectorMessageAngleIn
    ) -> SectorMessageAngle:
        sector = await self.get(tenant_id, sector_id)
        obj = SectorMessageAngle(tenant_id=tenant_id, sector_id=sector.id, **data.model_dump())
        self.session.add(obj)
        await self.session.commit()
        return obj

    async def delete_child(self, tenant_id: UUID, table: str, child_id: UUID) -> None:
        mapping = {
            "keywords": SectorKeyword,
            "target-customers": SectorTargetCustomer,
            "countries": SectorCountry,
            "message-angles": SectorMessageAngle,
        }
        model = mapping.get(table)
        if model is None:
            raise NotFoundError("Child collection")
        obj = await self.session.get(model, child_id)
        if obj is None or getattr(obj, "tenant_id") != tenant_id:
            raise NotFoundError("Item", str(child_id))
        await self.session.delete(obj)
        await self.session.commit()

    # --- Duplicate / import / export ---

    async def duplicate(self, tenant_id: UUID, sector_id: UUID, new_slug: str) -> Sector:
        src = await self.get(tenant_id, sector_id)
        if await self.repo.get_by_slug(tenant_id, new_slug):
            raise ConflictError(f"Sector slug '{new_slug}' already exists")
        clone = Sector(
            tenant_id=tenant_id,
            name=f"{src.name} (copy)",
            slug=new_slug,
            product_name=src.product_name,
            description=src.description,
            default_language=src.default_language,
            is_active=src.is_active,
        )
        await self.repo.add(clone)
        for k in src.keywords:
            self.session.add(
                SectorKeyword(
                    tenant_id=tenant_id,
                    sector_id=clone.id,
                    keyword=k.keyword,
                    language=k.language,
                    keyword_type=k.keyword_type,
                )
            )
        for c in src.target_customers:
            self.session.add(
                SectorTargetCustomer(
                    tenant_id=tenant_id,
                    sector_id=clone.id,
                    customer_type=c.customer_type,
                    language=c.language,
                    description=c.description,
                )
            )
        for co in src.countries:
            self.session.add(
                SectorCountry(
                    tenant_id=tenant_id,
                    sector_id=clone.id,
                    country_code=co.country_code,
                    timezone=co.timezone,
                    priority=co.priority,
                )
            )
        for m in src.message_angles:
            self.session.add(
                SectorMessageAngle(
                    tenant_id=tenant_id,
                    sector_id=clone.id,
                    angle=m.angle,
                    language=m.language,
                )
            )
        await self.session.commit()
        return await self.get(tenant_id, clone.id)

    async def import_from(self, tenant_id: UUID, data: SectorExport) -> Sector:
        sector = await self.create(tenant_id, data.sector)
        for k in data.keywords:
            await self.add_keyword(tenant_id, sector.id, k)
        for c in data.target_customers:
            await self.add_target_customer(tenant_id, sector.id, c)
        for co in data.countries:
            await self.add_country(tenant_id, sector.id, co)
        for m in data.message_angles:
            await self.add_message_angle(tenant_id, sector.id, m)
        return await self.get(tenant_id, sector.id)

    async def export_data(self, tenant_id: UUID, sector_id: UUID) -> SectorExport:
        sector = await self.get(tenant_id, sector_id)
        return SectorExport(
            sector=SectorIn(
                name=sector.name,
                slug=sector.slug,
                product_name=sector.product_name,
                description=sector.description,
                default_language=sector.default_language,
                is_active=sector.is_active,
            ),
            keywords=[
                SectorKeywordIn(keyword=k.keyword, language=k.language, keyword_type=k.keyword_type)
                for k in sector.keywords
            ],
            target_customers=[
                SectorTargetCustomerIn(
                    customer_type=c.customer_type, language=c.language, description=c.description
                )
                for c in sector.target_customers
            ],
            countries=[
                SectorCountryIn(
                    country_code=co.country_code, timezone=co.timezone, priority=co.priority
                )
                for co in sector.countries
            ],
            message_angles=[
                SectorMessageAngleIn(angle=m.angle, language=m.language)
                for m in sector.message_angles
            ],
        )
