"""Sector repository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Sector


class SectorRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, tenant_id: UUID) -> list[Sector]:
        stmt = select(Sector).where(Sector.tenant_id == tenant_id).order_by(Sector.created_at.desc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def get(self, tenant_id: UUID, sector_id: UUID) -> Sector | None:
        stmt = (
            select(Sector)
            .where(Sector.tenant_id == tenant_id, Sector.id == sector_id)
            .options(
                selectinload(Sector.keywords),
                selectinload(Sector.target_customers),
                selectinload(Sector.countries),
                selectinload(Sector.message_angles),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug(self, tenant_id: UUID, slug: str) -> Sector | None:
        stmt = select(Sector).where(Sector.tenant_id == tenant_id, Sector.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, sector: Sector) -> Sector:
        self.session.add(sector)
        await self.session.flush()
        return sector
