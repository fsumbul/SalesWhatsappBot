# ruff: noqa: RUF001
"""Platform owner invitation preview using the canonical cross-tenant service."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from src.core.db import set_tenant_context
from src.modules.auth import platform
from src.modules.auth.models import Tenant

from .workflow_schema import WorkflowField
from .workspace_tools import TurnTransaction

CONTROLS = [
    WorkflowField(key="tenant", label="Şirket", control="select", required=True),
    WorkflowField(key="email", label="Şirket sahibinin e-postası", control="email", required=True),
]


def controls(row: Any) -> list[WorkflowField]:
    return [
        c.model_copy(update={"options": row.state.get("companies", {})}) if c.key == "tenant" else c
        for c in CONTROLS
    ]


async def initialize(db: Any, row: Any) -> None:
    tenants = await db.scalars(select(Tenant).order_by(Tenant.name))
    row.state = {**row.state, "companies": {str(t.id): f"{t.name} · {t.slug}" for t in tenants}}


async def prepare(db: Any, row: Any) -> None:
    await initialize(db, row)
    if row.fields["tenant"] not in row.state["companies"]:
        raise HTTPException(404, "Şirket bulunamadı.")
    row.state = {
        **row.state,
        "changes": [
            {
                "label": "Şirket",
                "before": "",
                "after": row.state["companies"][row.fields["tenant"]],
            },
            {"label": "Sahip daveti", "before": "", "after": row.fields["email"]},
        ],
        "output": {
            "summary": "Şirket sahibi yetkisiyle yeni davet bağlantısı hazırlanacak. E-posta gönderilmez; üyelik davet kabul edildiğinde oluşur."
        },
    }


async def complete(db: Any, user: Any, row: Any) -> None:
    transaction: Any = TurnTransaction(db)
    try:
        result = await platform.invite_owner(
            UUID(row.fields["tenant"]),
            platform.OwnerInviteIn(email=row.fields["email"]),
            transaction,
            {"tid": str(user.tenant_id), "sub": str(user.id), "role": user.role.value},
        )
    finally:
        await set_tenant_context(db, user.tenant_id)
        await db.execute(
            text("SELECT set_config('app.current_user', :uid, true)"), {"uid": str(user.id)}
        )
    row.result = {
        "outcome": "invitation_ready",
        "token": result["invitation_token"],
        "email": row.fields["email"],
        "tenant_id": row.fields["tenant"],
        "message": f"{result['tenant_slug']} şirketi için sahip davet bağlantısı hazır. Davet henüz kabul edilmedi.",
    }
