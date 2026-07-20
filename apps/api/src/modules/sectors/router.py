"""Sector HTTP router."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from fastapi.responses import Response

from src.core.deps import ClaimsDep, DBSessionDep
from src.core.rbac import RequireManager

from .schemas import (
    SectorCountryIn,
    SectorCountryOut,
    SectorDetailOut,
    SectorExport,
    SectorIn,
    SectorKeywordIn,
    SectorKeywordOut,
    SectorMessageAngleIn,
    SectorMessageAngleOut,
    SectorOut,
    SectorPatchIn,
    SectorTargetCustomerIn,
    SectorTargetCustomerOut,
)
from .service import SectorService

router = APIRouter(prefix="/sectors", tags=["sectors"])


def _tid(claims: dict) -> UUID:
    return UUID(claims["tid"])


@router.get("", response_model=list[SectorOut])
async def list_sectors(db: DBSessionDep, claims: ClaimsDep) -> list[SectorOut]:
    items = await SectorService(db).list(_tid(claims))
    return [SectorOut.model_validate(s) for s in items]


@router.post("", response_model=SectorDetailOut, status_code=status.HTTP_201_CREATED)
async def create_sector(
    payload: SectorIn, db: DBSessionDep, claims: RequireManager
) -> SectorDetailOut:
    sector = await SectorService(db).create(_tid(claims), payload)
    return SectorDetailOut.model_validate(sector)


@router.get("/{sector_id}", response_model=SectorDetailOut)
async def get_sector(sector_id: UUID, db: DBSessionDep, claims: ClaimsDep) -> SectorDetailOut:
    sector = await SectorService(db).get(_tid(claims), sector_id)
    return SectorDetailOut.model_validate(sector)


@router.patch("/{sector_id}", response_model=SectorDetailOut)
async def patch_sector(
    sector_id: UUID,
    payload: SectorPatchIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> SectorDetailOut:
    sector = await SectorService(db).patch(_tid(claims), sector_id, payload)
    return SectorDetailOut.model_validate(sector)


@router.delete("/{sector_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_sector(sector_id: UUID, db: DBSessionDep, claims: RequireManager) -> Response:
    await SectorService(db).delete(_tid(claims), sector_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sector_id}/duplicate", response_model=SectorDetailOut)
async def duplicate_sector(
    sector_id: UUID,
    new_slug: str,
    db: DBSessionDep,
    claims: RequireManager,
) -> SectorDetailOut:
    sector = await SectorService(db).duplicate(_tid(claims), sector_id, new_slug)
    return SectorDetailOut.model_validate(sector)


@router.get("/{sector_id}/export", response_model=SectorExport)
async def export_sector(sector_id: UUID, db: DBSessionDep, claims: ClaimsDep) -> SectorExport:
    return await SectorService(db).export_data(_tid(claims), sector_id)


@router.post("/import", response_model=SectorDetailOut)
async def import_sector(
    payload: SectorExport, db: DBSessionDep, claims: RequireManager
) -> SectorDetailOut:
    sector = await SectorService(db).import_from(_tid(claims), payload)
    return SectorDetailOut.model_validate(sector)


_PRESETS = {
    "elevator-sheave": "ELEVATOR_SHEAVE_PRESET",
}


@router.post("/import-preset/{preset_key}", response_model=SectorDetailOut)
async def import_preset(
    preset_key: str, db: DBSessionDep, claims: RequireManager
) -> SectorDetailOut:
    from fastapi import HTTPException

    from . import presets

    attr = _PRESETS.get(preset_key)
    if not attr or not hasattr(presets, attr):
        raise HTTPException(status_code=404, detail=f"unknown preset: {preset_key}")
    preset = getattr(presets, attr)
    sector = await SectorService(db).import_from(_tid(claims), preset)
    return SectorDetailOut.model_validate(sector)


# --- Children ---


@router.post("/{sector_id}/keywords", response_model=SectorKeywordOut)
async def add_keyword(
    sector_id: UUID,
    payload: SectorKeywordIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> SectorKeywordOut:
    obj = await SectorService(db).add_keyword(_tid(claims), sector_id, payload)
    return SectorKeywordOut.model_validate(obj)


@router.delete("/keywords/{child_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_keyword(child_id: UUID, db: DBSessionDep, claims: RequireManager) -> Response:
    await SectorService(db).delete_child(_tid(claims), "keywords", child_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sector_id}/target-customers", response_model=SectorTargetCustomerOut)
async def add_customer(
    sector_id: UUID,
    payload: SectorTargetCustomerIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> SectorTargetCustomerOut:
    obj = await SectorService(db).add_target_customer(_tid(claims), sector_id, payload)
    return SectorTargetCustomerOut.model_validate(obj)


@router.delete("/target-customers/{child_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_customer(child_id: UUID, db: DBSessionDep, claims: RequireManager) -> Response:
    await SectorService(db).delete_child(_tid(claims), "target-customers", child_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sector_id}/countries", response_model=SectorCountryOut)
async def add_country(
    sector_id: UUID,
    payload: SectorCountryIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> SectorCountryOut:
    obj = await SectorService(db).add_country(_tid(claims), sector_id, payload)
    return SectorCountryOut.model_validate(obj)


@router.delete("/countries/{child_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_country(child_id: UUID, db: DBSessionDep, claims: RequireManager) -> Response:
    await SectorService(db).delete_child(_tid(claims), "countries", child_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sector_id}/message-angles", response_model=SectorMessageAngleOut)
async def add_angle(
    sector_id: UUID,
    payload: SectorMessageAngleIn,
    db: DBSessionDep,
    claims: RequireManager,
) -> SectorMessageAngleOut:
    obj = await SectorService(db).add_message_angle(_tid(claims), sector_id, payload)
    return SectorMessageAngleOut.model_validate(obj)


@router.delete("/message-angles/{child_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_angle(child_id: UUID, db: DBSessionDep, claims: RequireManager) -> Response:
    await SectorService(db).delete_child(_tid(claims), "message-angles", child_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
