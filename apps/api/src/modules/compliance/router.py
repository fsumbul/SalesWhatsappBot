"""Compliance HTTP router."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from fastapi.responses import Response

from src.core.deps import ClaimsDep, DBSessionDep
from src.core.rbac import RequireManager

from .schemas import ComplianceReportOut, OptOutIn, OptOutOut
from .service import ComplianceService

router = APIRouter(tags=["compliance"])
opt_outs_router = APIRouter(prefix="/opt-outs", tags=["opt-outs"])


def _tid(claims: dict) -> UUID:
    return UUID(claims["tid"])


@opt_outs_router.get("", response_model=list[OptOutOut])
async def list_opt_outs(db: DBSessionDep, claims: ClaimsDep) -> list[OptOutOut]:
    items = await ComplianceService(db).list_opt_outs(_tid(claims))
    return [OptOutOut.model_validate(o) for o in items]


@opt_outs_router.post("", response_model=OptOutOut, status_code=status.HTTP_201_CREATED)
async def add_opt_out(
    payload: OptOutIn, db: DBSessionDep, claims: RequireManager
) -> OptOutOut:
    obj = await ComplianceService(db).add_opt_out(
        _tid(claims), payload, actor_id=UUID(claims["sub"])
    )
    return OptOutOut.model_validate(obj)


@opt_outs_router.delete("/{opt_out_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def remove_opt_out(
    opt_out_id: UUID, db: DBSessionDep, claims: RequireManager
) -> Response:
    await ComplianceService(db).remove_opt_out(_tid(claims), opt_out_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/compliance/report", response_model=ComplianceReportOut, tags=["compliance"])
async def compliance_report(db: DBSessionDep, claims: ClaimsDep) -> ComplianceReportOut:
    return ComplianceReportOut(**await ComplianceService(db).report(_tid(claims)))
