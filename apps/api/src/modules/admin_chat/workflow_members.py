# ruff: noqa: RUF001
"""Current-role checked member edits with explicit before/after review."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from src.modules.auth.models import Tenant, User, UserRole
from src.modules.auth.router import patch_user
from src.modules.auth.schemas import UserPatchIn

from .workflow_schema import WorkflowField
from .workspace_tools import TurnTransaction

ROLES = {
    "tenant_owner": "Şirket sahibi",
    "sales_manager": "Satış yöneticisi",
    "sales_agent": "Satış temsilcisi",
    "viewer": "İzleyici",
}
CONTROLS = [
    WorkflowField(key="member", label="Ekip üyesi", control="select", required=True),
    WorkflowField(key="role", label="Yeni yetki", control="select", required=True, options=ROLES),
    WorkflowField(
        key="active",
        label="Hesap durumu",
        control="select",
        required=True,
        options={"true": "Etkin", "false": "Pasif"},
    ),
]


def controls(row: Any) -> list[WorkflowField]:
    return [
        c.model_copy(update={"options": row.state.get("member_choices", {})})
        if c.key == "member"
        else c
        for c in CONTROLS
    ]


async def selected(db: Any, user: Any, row: Any) -> Any:
    member = await db.scalar(
        select(User)
        .where(
            User.tenant_id == user.tenant_id,
            User.email == row.fields.get("member"),
            User.role != UserRole.SUPER_ADMIN,
        )
        .execution_options(populate_existing=True)
    )
    if member is None:
        raise HTTPException(404, "Düzenlenebilir ekip üyesi bulunamadı.")
    return member


async def initialize(db: Any, user: Any, session: Any, row: Any) -> None:
    members = list(
        (
            await db.scalars(
                select(User).where(
                    User.tenant_id == user.tenant_id, User.role != UserRole.SUPER_ADMIN
                )
            )
        ).all()
    )
    row.state = {"member_choices": {m.email: m.full_name or m.email for m in members}}
    if row.fields.get("member"):
        member = await selected(db, user, row)
        row.fields = {
            "role": member.role.value,
            "active": str(member.is_active).lower(),
            **row.fields,
        }


async def prepare(db: Any, user: Any, row: Any) -> None:
    member = await selected(db, user, row)
    row.state = {
        **row.state,
        "member_id": str(member.id),
        "base_role": member.role.value,
        "base_active": member.is_active,
        "changes": [
            {
                "label": "Yetki",
                "before": ROLES[member.role.value],
                "after": ROLES[row.fields["role"]],
            },
            {
                "label": "Hesap durumu",
                "before": "Etkin" if member.is_active else "Pasif",
                "after": "Etkin" if row.fields["active"] == "true" else "Pasif",
            },
        ],
        "output": {"summary": f"{member.email} hesabının erişimi aşağıdaki şekilde güncellenecek."},
    }

    row.state = {
        **row.state,
        "changes": [
            change for change in row.state["changes"] if change["before"] != change["after"]
        ],
    }


async def complete(db: Any, user: Any, row: Any) -> None:
    await db.execute(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
    member = await selected(db, user, row)
    if (
        str(member.id) != row.state["member_id"]
        or member.role.value != row.state["base_role"]
        or member.is_active != row.state["base_active"]
    ):
        raise HTTPException(409, "Ekip üyesinin erişimi değişti. Yeni önizleme hazırlayın.")
    transaction: Any = TurnTransaction(db)
    await patch_user(
        member.id,
        UserPatchIn(role=UserRole(row.fields["role"]), is_active=row.fields["active"] == "true"),
        transaction,
        {"tid": str(user.tenant_id), "sub": str(user.id), "role": user.role.value},
    )
    row.result = {
        "outcome": "member_updated",
        "user_id": str(member.id),
        "message": f"{member.email} hesabının yetkisi ve etkinlik durumu güncellendi.",
    }
