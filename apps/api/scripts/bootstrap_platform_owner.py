"""Server-only bootstrap for the first platform administrator.

Tenant APIs cannot grant this role. Use --invite to let the user set a password.
"""

import argparse
import asyncio
import secrets
import sys
from datetime import UTC, datetime, timedelta
from getpass import getpass
from pathlib import Path
from urllib.parse import urlencode

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.db import session_scope, set_tenant_context
from src.modules.auth.models import Invitation, Tenant, User, UserRole
from src.modules.auth.schemas import TenantRegisterIn
from src.modules.auth.service import AuthService
from src.modules.compliance.models import AuditLog


async def bootstrap(slug: str, email: str, web_url: str | None, password: str | None) -> None:
    async with session_scope() as db:
        if web_url:
            tenant = await db.scalar(select(Tenant).where(Tenant.slug == slug))
            if tenant is None:
                tenant = Tenant(name="Platform", slug=slug)
                db.add(tenant)
                await db.flush()
            await set_tenant_context(db, tenant.id)
            if await db.scalar(
                select(User.id).where(User.tenant_id == tenant.id, User.email == email.lower())
            ):
                raise SystemExit(
                    "Account already exists; bootstrap will not change its role or password"
                )
            invitation = await db.scalar(
                select(Invitation).where(
                    Invitation.tenant_id == tenant.id,
                    Invitation.email == email.lower(),
                    Invitation.role == UserRole.SUPER_ADMIN,
                    Invitation.accepted_at.is_(None),
                    Invitation.expires_at > datetime.now(UTC),
                )
            )
            if invitation is None:
                invitation = Invitation(
                    tenant_id=tenant.id,
                    email=email.lower(),
                    role=UserRole.SUPER_ADMIN,
                    token=secrets.token_urlsafe(32),
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
                db.add(invitation)
                db.add(
                    AuditLog(
                        tenant_id=tenant.id,
                        action="bootstrap_platform_invitation",
                        entity="tenant",
                        entity_id=str(tenant.id),
                    )
                )
                await db.commit()
            print(
                web_url.rstrip("/")
                + "/tr?"
                + urlencode({"company": slug, "invite": invitation.token})
            )
        else:
            assert password is not None
            _, user = await AuthService(db).register_tenant(
                TenantRegisterIn(
                    tenant_name="Platform",
                    tenant_slug=slug,
                    admin_email=email,
                    admin_password=password,
                )
            )
            user.role = UserRole.SUPER_ADMIN
            await db.commit()
            print("Platform administrator created")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company-code", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--invite",
        metavar="WEB_ORIGIN",
        help="Print a private invitation URL instead of setting a password",
    )
    args = parser.parse_args()
    asyncio.run(
        bootstrap(
            args.company_code,
            args.email,
            args.invite,
            None if args.invite else getpass("New administrator password: "),
        )
    )
