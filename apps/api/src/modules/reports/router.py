"""Reports HTTP router."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from src.core.deps import ClaimsDep, DBSessionDep

from .schemas import (
    FunnelReportOut,
    SalesPerformanceOut,
    SenderHealthOut,
    SenderHealthReportOut,
)
from .service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


def _tid(claims: dict) -> UUID:
    return UUID(claims["tid"])


@router.get("/funnel", response_model=FunnelReportOut)
async def funnel(db: DBSessionDep, claims: ClaimsDep) -> FunnelReportOut:
    return FunnelReportOut(**await ReportService(db).funnel(_tid(claims)))


@router.get("/senders", response_model=SenderHealthReportOut)
async def senders(db: DBSessionDep, claims: ClaimsDep) -> SenderHealthReportOut:
    rows = await ReportService(db).sender_health(_tid(claims))
    return SenderHealthReportOut(senders=[SenderHealthOut(**r) for r in rows])


@router.get("/sales", response_model=SalesPerformanceOut)
async def sales(db: DBSessionDep, claims: ClaimsDep) -> SalesPerformanceOut:
    return SalesPerformanceOut(**await ReportService(db).sales_performance(_tid(claims)))
