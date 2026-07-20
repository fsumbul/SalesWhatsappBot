"""Compliance service — evaluates whether a contact may be messaged now.

Rules (in order, defense in depth):
  1. Opt-out list (hard block)
  2. Cooldown (30d since last outbound to this number)
  3. Quiet hours (target country timezone 09:00-18:00)
  4. Turkey: IYS check (marketing consent registry)
  5. Blacklist / consent_status

Returns a `ComplianceDecision` with result, reason, and next_allowed_at
(so scheduler can defer instead of drop).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.iys import IYSClient, IYSDecision

from ..discovery.models import ConsentStatus, LeadContact
from .models import AuditLog, ComplianceCheck, ComplianceResult, OptOut, OptOutSource
from .schemas import OptOutIn

_QUIET_START = time(9, 0)
_QUIET_END = time(18, 0)
_COOLDOWN_DAYS = 30

_COUNTRY_TZ: dict[str, str] = {
    "TR": "Europe/Istanbul",
    "DE": "Europe/Berlin",
    "GB": "Europe/London",
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "RU": "Europe/Moscow",
}


class ComplianceDecision:
    def __init__(
        self,
        decision: ComplianceResult,
        reason: str,
        next_allowed_at: datetime | None = None,
        checks: list[dict] | None = None,
    ) -> None:
        self.decision = decision
        self.reason = reason
        self.next_allowed_at = next_allowed_at
        self.checks = checks or []


class ComplianceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.iys = IYSClient()

    # --- Public API ---

    async def check_contact(
        self, tenant_id: UUID, contact: LeadContact, country: str | None
    ) -> ComplianceDecision:
        checks: list[dict] = []

        # 1. Opt-out
        opt_stmt = select(OptOut).where(
            OptOut.tenant_id == tenant_id, OptOut.phone_e164 == contact.normalized_value
        )
        if (await self.session.execute(opt_stmt)).scalar_one_or_none():
            checks.append({"type": "opt_out", "result": "block"})
            return await self._persist(
                tenant_id, contact, ComplianceResult.BLOCK,
                "opt_out_present", None, checks
            )

        # 2. Consent status
        if contact.consent_status == ConsentStatus.OPT_OUT:
            checks.append({"type": "consent_status", "result": "block"})
            return await self._persist(
                tenant_id, contact, ComplianceResult.BLOCK,
                "contact_opted_out", None, checks
            )

        # 3. Cooldown — did we message this number in last 30 days?
        from src.modules.outreach.models import OutreachJob, OutreachJobStatus  # local import

        cooldown_stmt = (
            select(func.max(OutreachJob.sent_at))
            .where(
                OutreachJob.tenant_id == tenant_id,
                OutreachJob.contact_id == contact.id,
                OutreachJob.status.in_(
                    [OutreachJobStatus.SENT, OutreachJobStatus.DELIVERED, OutreachJobStatus.READ]
                ),
            )
        )
        last_sent = (await self.session.execute(cooldown_stmt)).scalar_one_or_none()
        if last_sent is not None:
            elapsed = datetime.now(UTC) - last_sent
            if elapsed < timedelta(days=_COOLDOWN_DAYS):
                next_ok = last_sent + timedelta(days=_COOLDOWN_DAYS)
                checks.append({"type": "cooldown", "result": "block", "last_sent": str(last_sent)})
                return await self._persist(
                    tenant_id, contact, ComplianceResult.BLOCK,
                    "cooldown_active", next_ok, checks
                )

        # 4. Quiet hours (defer instead of block)
        tz_name = _COUNTRY_TZ.get((country or "").upper(), "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        now_local = datetime.now(tz)
        if not (_QUIET_START <= now_local.time() <= _QUIET_END):
            # Compute next 09:00 local
            next_local = now_local.replace(
                hour=_QUIET_START.hour, minute=0, second=0, microsecond=0
            )
            if now_local.time() > _QUIET_END:
                next_local = next_local + timedelta(days=1)
            checks.append({"type": "quiet_hours", "result": "defer", "next": str(next_local)})
            return await self._persist(
                tenant_id, contact, ComplianceResult.DEFER,
                "quiet_hours", next_local.astimezone(UTC), checks
            )

        # 5. IYS (Turkey only)
        if (country or "").upper() == "TR":
            iys = await self.iys.check(contact.normalized_value)
            checks.append({"type": "iys", "result": iys.value})
            if iys == IYSDecision.BLOCKED:
                return await self._persist(
                    tenant_id, contact, ComplianceResult.BLOCK,
                    "iys_rejected", None, checks
                )

        checks.append({"type": "all_checks", "result": "pass"})
        return await self._persist(
            tenant_id, contact, ComplianceResult.PASS, "allowed", None, checks
        )

    async def _persist(
        self,
        tenant_id: UUID,
        contact: LeadContact,
        result: ComplianceResult,
        reason: str,
        next_allowed_at: datetime | None,
        checks: list[dict],
    ) -> ComplianceDecision:
        record = ComplianceCheck(
            tenant_id=tenant_id,
            lead_id=contact.lead_id,
            contact_id=contact.id,
            check_type="pre_send",
            result=result,
            details={"checks": checks, "reason": reason},
            next_allowed_at=next_allowed_at,
        )
        self.session.add(record)
        await self.session.commit()
        return ComplianceDecision(result, reason, next_allowed_at, checks)

    # --- Opt-outs CRUD ---

    async def list_opt_outs(self, tenant_id: UUID) -> list[OptOut]:
        stmt = (
            select(OptOut)
            .where(OptOut.tenant_id == tenant_id)
            .order_by(OptOut.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add_opt_out(
        self, tenant_id: UUID, data: OptOutIn, *, actor_id: UUID | None = None
    ) -> OptOut:
        # idempotent
        existing = (
            await self.session.execute(
                select(OptOut).where(
                    OptOut.tenant_id == tenant_id,
                    OptOut.phone_e164 == data.phone_e164,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        obj = OptOut(
            tenant_id=tenant_id,
            phone_e164=data.phone_e164,
            source=data.source,
            reason=data.reason,
        )
        self.session.add(obj)
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="add_opt_out",
                entity="opt_out",
                entity_id=data.phone_e164,
                meta={"source": data.source.value, "reason": data.reason},
            )
        )
        await self.session.commit()
        return obj

    async def remove_opt_out(self, tenant_id: UUID, opt_out_id: UUID) -> None:
        obj = await self.session.get(OptOut, opt_out_id)
        if obj is not None and obj.tenant_id == tenant_id:
            await self.session.delete(obj)
            await self.session.commit()

    async def report(self, tenant_id: UUID) -> dict:
        total_stmt = select(func.count()).select_from(OptOut).where(
            OptOut.tenant_id == tenant_id
        )
        total = (await self.session.execute(total_stmt)).scalar_one()
        source_stmt = (
            select(OptOut.source, func.count())
            .where(OptOut.tenant_id == tenant_id)
            .group_by(OptOut.source)
        )
        by_source = {s.value: c for s, c in (await self.session.execute(source_stmt)).all()}

        thirty = datetime.now(UTC) - timedelta(days=30)
        blocked_stmt = select(func.count()).select_from(ComplianceCheck).where(
            ComplianceCheck.tenant_id == tenant_id,
            ComplianceCheck.result == ComplianceResult.BLOCK,
            ComplianceCheck.created_at >= thirty,
        )
        blocked = (await self.session.execute(blocked_stmt)).scalar_one()
        return {
            "total_opt_outs": total,
            "by_source": by_source,
            "blocked_last_30d": blocked,
        }
