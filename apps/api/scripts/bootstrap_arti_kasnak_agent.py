# ruff: noqa: E402, RUF001, RUF002
"""Create or reconcile the production Artı Kasnak agent configuration.

The script is intentionally idempotent. It validates the checked-in
``CompanyAgentConfig``, locks the tenant while reconciling, and creates a new
LIVE version only when the complete desired version content has changed.

Run from ``apps/api`` with the production environment loaded::

    poetry run python scripts/bootstrap_arti_kasnak_agent.py --validate-only
    poetry run python scripts/bootstrap_arti_kasnak_agent.py --dry-run
    poetry run python scripts/bootstrap_arti_kasnak_agent.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text

# Support the documented direct invocation from the application root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import models_registry  # noqa: F401
from src.core.config import get_settings
from src.core.db import get_sessionmaker, reset_tenant_context, set_tenant_context
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.models import Agent, AgentVersion, AgentVersionStatus
from src.modules.auth.models import Tenant, TenantStatus
from src.modules.compliance.models import AuditLog

_DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "arti_kasnak.production.json"
_DEFAULT_TENANT_SLUG = "kasnak"
_DEFAULT_AGENT_SLUG = "arti-kasnak"
_AGENT_NAME = "Artı Kasnak"

_LEGACY_CONTENT: dict[str, Any] = {
    "persona": "Artı Kasnak'ın Türkçe müşteri bilgi ve teklif ön hazırlık asistanı.",
    "tone": "profesyonel, kısa, açık ve çözüm odaklı",
    "languages": ["tr"],
    "product_knowledge": (
        "Müşteri yanıtlarında yalnızca company_config içindeki "
        "customer_visible facts kullanılmalıdır."
    ),
    "qualification_questions": [
        "İhtiyaç duyduğunuz kasnak türü ve kullanım amacı nedir?",
        "Teknik çiziminiz var mı?",
        "Kasnak çapı A, genişlik B, halat adedi C, halat ölçüsü D ve kanal mesafesi E nedir?",
        "Kullanılacak rulman modeli nedir?",
        "Mil veya aks için SD, SL, GDi, GW ve GDe ölçüleri nedir?",
        "Asansör kapasitesi ve hızı nedir?",
        "İhtiyaç adedi nedir?",
        "Teslimat şehri ve ülkesi neresidir?",
    ],
    "guardrails": {
        "forbidden_claims": [
            "doğrulanmamış fiyat",
            "doğrulanmamış stok",
            "doğrulanmamış teslim süresi",
            "güncel sertifika veya uygunluk iddiası",
        ],
        "escalation_triggers": [
            "nihai ürün seçimi",
            "teknik uygunluk veya güvenlik değerlendirmesi",
            "fiyat, stok veya teslim tarihi talebi",
            "company_config ile yanıtlanamayan her soru",
        ],
        "automatic_product_selection": False,
    },
    "reply_policies": {
        "default_locale": "tr",
        "max_response_length": 1200,
        "unknown_fact_action": "handoff",
        "require_customer_visible_fact_ids": True,
    },
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--tenant-slug", default=_DEFAULT_TENANT_SLUG)
    parser.add_argument("--agent-slug", default=_DEFAULT_AGENT_SLUG)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the configuration without connecting to the database",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="perform and flush reconciliation, then roll the transaction back",
    )
    return parser.parse_args()


def _load_config(path: Path) -> CompanyAgentConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = CompanyAgentConfig.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise SystemExit(f"Invalid Artı Kasnak configuration: {exc}") from exc

    errors = config.publishability_errors()
    if errors:
        raise SystemExit("Configuration is not publishable: " + "; ".join(errors))
    return config


def _desired_content(config: CompanyAgentConfig) -> dict[str, Any]:
    return {
        **_LEGACY_CONTENT,
        "company_config": config.model_dump(mode="json"),
    }


def _version_content(version: AgentVersion) -> dict[str, Any]:
    return {
        "persona": version.persona,
        "tone": version.tone,
        "languages": version.languages,
        "product_knowledge": version.product_knowledge,
        "qualification_questions": version.qualification_questions,
        "guardrails": version.guardrails,
        "reply_policies": version.reply_policies,
        "company_config": version.company_config,
    }


def _fingerprint(content: dict[str, Any]) -> str:
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical, usedforsecurity=False).hexdigest()


async def _reconcile(
    config: CompanyAgentConfig,
    *,
    tenant_slug: str,
    agent_slug: str,
    dry_run: bool,
) -> dict[str, Any]:
    desired = _desired_content(config)
    desired_fingerprint = _fingerprint(desired)
    async with get_sessionmaker()() as session:
        try:
            # Locking the tenant serializes bootstrap attempts, including the
            # first run where the Agent row does not exist yet.
            tenant = (
                await session.execute(
                    select(Tenant).where(Tenant.slug == tenant_slug).with_for_update()
                )
            ).scalar_one_or_none()
            if tenant is None:
                raise SystemExit(f"Tenant '{tenant_slug}' was not found")

            await set_tenant_context(session, tenant.id)
            tenant_changed = False
            desired_waba = get_settings().whatsapp_business_account_id.strip()
            if desired_waba:
                # Serialize bootstrap attempts for the same external sender.
                # The partial unique index is the final invariant for every
                # writer; this lock plus pre-check provides an actionable
                # error before the transaction reaches that constraint.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:binding, 0))"),
                    {"binding": f"active-tenant-waba:{desired_waba}"},
                )
                conflicting_slug = (
                    await session.execute(
                        select(Tenant.slug)
                        .where(
                            Tenant.id != tenant.id,
                            Tenant.status == TenantStatus.ACTIVE,
                            Tenant.wa_business_account_id == desired_waba,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if conflicting_slug is not None:
                    raise SystemExit(
                        "Configured WhatsApp Business Account is already bound "
                        f"to active tenant '{conflicting_slug}'"
                    )
            if desired_waba and tenant.wa_business_account_id != desired_waba:
                tenant.wa_business_account_id = desired_waba
                tenant_changed = True

            agent = (
                await session.execute(
                    select(Agent)
                    .where(Agent.tenant_id == tenant.id, Agent.slug == agent_slug)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            agent_changed = False
            if agent is None:
                agent = Agent(
                    tenant_id=tenant.id,
                    name=_AGENT_NAME,
                    slug=agent_slug,
                    is_active=True,
                )
                session.add(agent)
                await session.flush()
                agent_changed = True
            else:
                if agent.name != _AGENT_NAME:
                    agent.name = _AGENT_NAME
                    agent_changed = True
                if not agent.is_active:
                    agent.is_active = True
                    agent_changed = True

            versions = list(
                (
                    await session.execute(
                        select(AgentVersion)
                        .where(
                            AgentVersion.tenant_id == tenant.id,
                            AgentVersion.agent_id == agent.id,
                        )
                        .order_by(AgentVersion.version.desc())
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            live_versions = [
                version for version in versions if version.status == AgentVersionStatus.LIVE
            ]
            matching_live = next(
                (
                    version
                    for version in live_versions
                    if _fingerprint(_version_content(version)) == desired_fingerprint
                ),
                None,
            )

            if matching_live is not None:
                matching_live_version = matching_live.version
                archived_versions: list[int] = []
                for version in live_versions:
                    if version.id != matching_live.id:
                        version.status = AgentVersionStatus.ARCHIVED
                        archived_versions.append(version.version)

                reconciled = tenant_changed or agent_changed or bool(archived_versions)
                if reconciled:
                    session.add(
                        AuditLog(
                            tenant_id=tenant.id,
                            actor_id=None,
                            action="reconcile_company_agent_bootstrap",
                            entity="agent_version",
                            entity_id=str(matching_live.id),
                            meta={
                                "agent_id": str(agent.id),
                                "fingerprint": desired_fingerprint,
                                "tenant_waba_bound": bool(desired_waba),
                                "archived_versions": archived_versions,
                            },
                        )
                    )

                if dry_run:
                    await session.rollback()
                else:
                    await session.commit()
                return {
                    "status": "dry_run" if dry_run else ("reconciled" if reconciled else "current"),
                    "tenant_slug": tenant_slug,
                    "agent_slug": agent_slug,
                    "version": matching_live_version,
                    "fingerprint": desired_fingerprint,
                    "archived_versions": archived_versions,
                }

            archived_versions = []
            for version in live_versions:
                version.status = AgentVersionStatus.ARCHIVED
                archived_versions.append(version.version)

            next_version = (versions[0].version + 1) if versions else 1
            new_version = AgentVersion(
                tenant_id=tenant.id,
                agent_id=agent.id,
                version=next_version,
                status=AgentVersionStatus.LIVE,
                **desired,
            )
            session.add(new_version)
            await session.flush()
            session.add(
                AuditLog(
                    tenant_id=tenant.id,
                    actor_id=None,
                    action="bootstrap_company_agent",
                    entity="agent_version",
                    entity_id=str(new_version.id),
                    meta={
                        "agent_id": str(agent.id),
                        "version": next_version,
                        "fingerprint": desired_fingerprint,
                        "tenant_waba_bound": bool(desired_waba),
                        "archived_versions": archived_versions,
                    },
                )
            )

            if dry_run:
                await session.rollback()
            else:
                await session.commit()
            return {
                "status": "dry_run" if dry_run else "created",
                "tenant_slug": tenant_slug,
                "agent_slug": agent_slug,
                "version": next_version,
                "fingerprint": desired_fingerprint,
                "archived_versions": archived_versions,
            }
        except BaseException:
            await session.rollback()
            raise
        finally:
            await reset_tenant_context(session)


async def _async_main(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    desired_fingerprint = _fingerprint(_desired_content(config))
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "valid",
                    "config": str(args.config),
                    "fingerprint": desired_fingerprint,
                },
                ensure_ascii=False,
            )
        )
        return

    result = await _reconcile(
        config,
        tenant_slug=args.tenant_slug,
        agent_slug=args.agent_slug,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    asyncio.run(_async_main(_parse_args()))


if __name__ == "__main__":
    main()
