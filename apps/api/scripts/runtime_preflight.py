# ruff: noqa: E402
"""Sanitized hard-gate preflight for the production WhatsApp agent runtime."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select, text

# Running ``python scripts/runtime_preflight.py`` sets sys.path to the
# scripts directory, not the application root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import models_registry  # noqa: F401
from src.core.config import Settings, get_settings
from src.core.db import session_scope, set_tenant_context
from src.core.errors import ConflictError
from src.modules.agents.models import Agent, AgentVersion, AgentVersionStatus
from src.modules.agents.runtime_models import AgentRuntimeJob
from src.modules.auth.models import Tenant, TenantStatus, User, UserRole
from src.modules.outreach.channel import resolve_channel

_EXPECTED_ALEMBIC_REVISION = "d9e6f1a2b035"
_WORKER_NAME = "agent-runtime@ashiraai"
_SCHEDULED_TASKS = ["AshiraaiApi", "AshiraaiAgentWorker", "AshiraaiAgentRecovery"]
_CPU_AVERAGE_BLOCK_PERCENT = 90
_CPU_PEAK_BLOCK_PERCENT = 98


def _scheduled_task_states() -> dict[str, str]:
    if os.name != "nt":
        return {name: "unsupported-platform" for name in _SCHEDULED_TASKS}
    states: dict[str, str] = {name: "query-failed" for name in _SCHEDULED_TASKS}
    for name in _SCHEDULED_TASKS:
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    "C:\\Windows\\System32\\schtasks.exe",
                    "/Query",
                    "/TN",
                    name,
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if completed.returncode == 0:
                row = next(csv.reader([completed.stdout.strip()]), [])
                if len(row) >= 3:
                    states[name] = row[2].strip()
        except (OSError, subprocess.SubprocessError):
            continue
    return states


def _worker_ping() -> bool:
    try:
        from src.core.agent_celery_app import agent_celery_app

        replies = agent_celery_app.control.ping(
            destination=[_WORKER_NAME],
            timeout=5,
        )
    except Exception:
        return False
    return bool(replies and any(_WORKER_NAME in reply for reply in replies))


def _host_health() -> dict[str, Any]:
    if os.name != "nt":
        return {"reachable": True, "cpu_check": "unsupported-platform"}
    samples: list[int] = []
    try:
        for _ in range(3):
            completed = subprocess.run(  # noqa: S603
                ["C:\\Windows\\System32\\wbem\\WMIC.exe", "cpu", "get", "LoadPercentage", "/value"],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
            if completed.returncode != 0:
                return {"reachable": False, "error_type": "wmic-query-failed"}
            values = [
                int(line.partition("=")[2])
                for line in completed.stdout.splitlines()
                if line.strip().startswith("LoadPercentage=")
                and line.partition("=")[2].strip().isdigit()
            ]
            if not values:
                return {"reachable": False, "error_type": "wmic-output-invalid"}
            samples.append(round(sum(values) / len(values)))
    except (OSError, subprocess.SubprocessError):
        return {"reachable": False, "error_type": "wmic-unavailable"}
    return {
        "reachable": True,
        "cpu_load_samples_percent": samples,
        "cpu_load_average_percent": round(sum(samples) / len(samples), 1),
        "cpu_load_max_percent": max(samples),
    }


async def _nim_health(settings: Settings) -> dict[str, Any]:
    """Readiness of every self-hosted NIM endpoint the settings enable.

    Only host names, readiness and served model ids are reported — never a
    request or response body.
    """

    from src.integrations.nim import NimHttp

    report: dict[str, Any] = {}
    for endpoint in settings.nim_endpoints():
        entry: dict[str, Any] = {"kind": endpoint.kind, "host": endpoint.host}
        try:
            http = NimHttp(
                endpoint.url,
                api_key=settings.nim_api_key,
                timeout_seconds=5.0,
                name=endpoint.role,
            )
            entry["ready"] = await http.ready()
            if endpoint.kind in {"llm", "embedding", "rerank"}:
                try:
                    entry["models"] = await http.models()
                except Exception as exc:
                    entry["models_error"] = type(exc).__name__
        except Exception as exc:
            entry["ready"] = False
            entry["error_type"] = type(exc).__name__
        report[endpoint.role] = entry
    return report


async def inspect(tenant_slug: str) -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = {
        "tenant_slug": tenant_slug,
        "app_env": settings.app_env,
        "app_debug": settings.app_debug,
        "production_configuration_errors": settings.production_runtime_errors(),
        "whatsapp_credentials_configured": bool(
            settings.whatsapp_access_token
            and settings.whatsapp_app_secret
            and settings.whatsapp_verify_token
            and settings.whatsapp_phone_number_id
            and settings.whatsapp_business_account_id
        ),
        "whatsapp_agent_slug": settings.whatsapp_agent_slug,
        "whatsapp_graph_api_version": settings.whatsapp_graph_api_version,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "expected_alembic_revision": _EXPECTED_ALEMBIC_REVISION,
    }

    async with session_scope() as session:
        role_row = (
            await session.execute(
                text(
                    """
                    SELECT current_user, rolsuper, rolbypassrls
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
            )
        ).one()
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
        ).scalar_one_or_none()
        tenants = list(
            (await session.execute(select(Tenant).order_by(Tenant.slug.asc()))).scalars().all()
        )
        alembic_revision = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        runtime_table = (
            await session.execute(text("SELECT to_regclass('agent_runtime_jobs')"))
        ).scalar_one_or_none()
        active_waba_unique_index = (
            await session.execute(text("SELECT to_regclass('uq_tenants_active_waba')"))
        ).scalar_one_or_none()
        duplicate_active_waba_bindings = (
            await session.execute(
                text(
                    """
                    SELECT count(*)
                    FROM (
                        SELECT wa_business_account_id
                        FROM tenants
                        WHERE status = 'active'::tenant_status
                          AND wa_business_account_id IS NOT NULL
                        GROUP BY wa_business_account_id
                        HAVING count(*) > 1
                    ) conflicts
                    """
                )
            )
        ).scalar_one()
        configured_waba_active_tenant_count = (
            await session.execute(
                select(func.count(Tenant.id)).where(
                    Tenant.status == TenantStatus.ACTIVE,
                    Tenant.wa_business_account_id == settings.whatsapp_business_account_id,
                )
            )
        ).scalar_one()

        database: dict[str, Any] = {
            "tenant_found": tenant is not None,
            "alembic_revision": alembic_revision,
            "runtime_table_present": runtime_table is not None,
            "runtime_role": role_row[0],
            "runtime_role_superuser": role_row[1],
            "runtime_role_bypassrls": role_row[2],
            "active_waba_unique_index_present": active_waba_unique_index is not None,
            "duplicate_active_waba_bindings": duplicate_active_waba_bindings,
            "configured_waba_active_tenant_count": configured_waba_active_tenant_count,
            "duplicate_inbound_wa_ids": 0,
            "duplicate_tenant_phone_identities": 0,
            "active_human_reviewers": 0,
            "customer_handoff_contact_configured": False,
            "tenant_user_role_counts": {},
            "target_agent_live_approved": False,
            "runtime_job_status_counts": {},
            "agent_versions": [],
        }
        if tenant is not None:
            database.update(
                {
                    "tenant_status": tenant.status.value,
                    "tenant_waba_bound": bool(
                        tenant.wa_business_account_id
                        and tenant.wa_business_account_id == settings.whatsapp_business_account_id
                    ),
                }
            )
            await set_tenant_context(session, tenant.id)
            duplicate_inbound = (
                await session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM (
                            SELECT wa_message_id
                            FROM messages
                            WHERE tenant_id = :tenant_id
                              AND direction = 'inbound'
                              AND wa_message_id IS NOT NULL
                            GROUP BY wa_message_id
                            HAVING count(*) > 1
                        ) duplicates
                        """
                    ),
                    {"tenant_id": tenant.id},
                )
            ).scalar_one()
            duplicate_phones = (
                await session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM (
                            SELECT normalized_value
                            FROM lead_contacts
                            WHERE tenant_id = :tenant_id AND type = 'phone'
                            GROUP BY normalized_value
                            HAVING count(*) > 1
                        ) duplicates
                        """
                    ),
                    {"tenant_id": tenant.id},
                )
            ).scalar_one()
            agents = list(
                (
                    await session.execute(
                        select(Agent, AgentVersion)
                        .join(AgentVersion, AgentVersion.agent_id == Agent.id)
                        .where(Agent.tenant_id == tenant.id)
                        .order_by(Agent.slug, AgentVersion.version)
                    )
                ).all()
            )
            active_reviewers = (
                await session.execute(
                    select(func.count(User.id)).where(
                        User.tenant_id == tenant.id,
                        User.is_active.is_(True),
                        User.role.in_(
                            [
                                UserRole.TENANT_OWNER,
                                UserRole.SALES_MANAGER,
                                UserRole.SALES_AGENT,
                            ]
                        ),
                    )
                )
            ).scalar_one()
            role_counts = list(
                (
                    await session.execute(
                        select(User.role, User.is_active, func.count(User.id))
                        .where(User.tenant_id == tenant.id)
                        .group_by(User.role, User.is_active)
                    )
                ).all()
            )
            runtime_job_counts = list(
                (
                    await session.execute(
                        select(AgentRuntimeJob.status, func.count(AgentRuntimeJob.id))
                        .where(AgentRuntimeJob.tenant_id == tenant.id)
                        .group_by(AgentRuntimeJob.status)
                    )
                ).all()
            )
            try:
                sender = await resolve_channel(session, tenant.id)
                sender_agent_id = sender.agent_id
            except ConflictError:
                sender_agent_id = None
            target_live = [
                (agent, version)
                for agent, version in agents
                if agent.id == sender_agent_id
                and agent.is_active
                and version.status == AgentVersionStatus.LIVE
                and (version.company_config or {}).get("lifecycle") == "approved"
            ]
            customer_handoff_contact_configured = False
            if len(target_live) == 1:
                target_config = target_live[0][1].company_config or {}
                agent_policy = target_config.get("agent") or {}
                handoff_fact_id = agent_policy.get("handoff_fact_id")
                facts = target_config.get("facts") or []
                customer_handoff_contact_configured = bool(
                    handoff_fact_id
                    and any(
                        isinstance(fact, dict)
                        and fact.get("id") == handoff_fact_id
                        and fact.get("customer_visible") is True
                        and fact.get("customer_text")
                        for fact in facts
                    )
                )
            database.update(
                {
                    "duplicate_inbound_wa_ids": duplicate_inbound,
                    "duplicate_tenant_phone_identities": duplicate_phones,
                    "active_human_reviewers": active_reviewers,
                    "customer_handoff_contact_configured": (customer_handoff_contact_configured),
                    "tenant_user_role_counts": {
                        f"{role.value}:{'active' if is_active else 'inactive'}": count
                        for role, is_active, count in role_counts
                    },
                    "target_agent_live_approved": len(target_live) == 1,
                    "selection_enabled": bool(len(target_live) == 1 and target_live[0][1].company_config.get("selection_flow")),
                    "runtime_job_status_counts": dict(runtime_job_counts),
                    "agent_versions": [
                        {
                            "slug": agent.slug,
                            "active": agent.is_active,
                            "version": version.version,
                            "status": version.status.value,
                            "config_lifecycle": (version.company_config or {}).get("lifecycle"),
                        }
                        for agent, version in agents
                    ],
                }
            )
        result["database"] = database
        tenant_candidates: list[dict[str, Any]] = []
        for candidate in tenants:
            await set_tenant_context(session, candidate.id)
            active_roles = list(
                (
                    await session.execute(
                        select(User.role, func.count(User.id))
                        .where(User.tenant_id == candidate.id, User.is_active.is_(True))
                        .group_by(User.role)
                    )
                ).all()
            )
            tenant_candidates.append(
                {
                    "slug": candidate.slug,
                    "status": candidate.status.value,
                    "configured_waba_bound": bool(
                        candidate.wa_business_account_id
                        and candidate.wa_business_account_id
                        == settings.whatsapp_business_account_id
                    ),
                    "active_user_roles": {role.value: count for role, count in active_roles},
                }
            )
        result["tenant_candidates"] = tenant_candidates

    graph_base = f"https://graph.facebook.com/{settings.whatsapp_graph_api_version}"
    try:
        headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, headers=headers) as client:
            phone_response = await client.get(
                f"{graph_base}/{settings.whatsapp_phone_number_id}",
                params={"fields": "id"},
            )
            phone_response.raise_for_status()
            phone_payload = phone_response.json()
            waba_response = await client.get(
                f"{graph_base}/{settings.whatsapp_business_account_id}",
                params={"fields": "id"},
            )
            waba_response.raise_for_status()
            waba_payload = waba_response.json()
            subscription_response = await client.get(
                f"{graph_base}/{settings.whatsapp_business_account_id}/subscribed_apps",
                params={"limit": "100"},
            )
            subscription_response.raise_for_status()
            subscription_payload = subscription_response.json()
        subscriptions = subscription_payload.get("data", [])
        result["meta"] = {
            "reachable": True,
            "phone_id_matches": phone_payload.get("id") == settings.whatsapp_phone_number_id,
            "waba_id_matches": waba_payload.get("id") == settings.whatsapp_business_account_id,
            "subscribed_apps_count": len(subscriptions) if isinstance(subscriptions, list) else 0,
        }
    except Exception as exc:
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        result["meta"] = {
            "reachable": False,
            "error_type": type(exc).__name__,
            "status_code": status_code,
        }

    try:
        base_url = settings.llm_base_url.rstrip("/")
        headers = (
            {"Authorization": f"Bearer {settings.llm_api_key}"}
            if settings.llm_api_key.strip()
            else {}
        )
        if settings.llm_provider == "ollama":
            root = base_url[:-3] if base_url.endswith("/v1") else base_url
            url = f"{root}/api/tags"
        else:
            root = base_url.removesuffix("/chat/completions")
            url = f"{root}/models"
        async with httpx.AsyncClient(
            timeout=5, follow_redirects=False, headers=headers
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        model_items = (
            payload.get("models", [])
            if settings.llm_provider == "ollama"
            else payload.get("data", [])
        )
        model_key = "name" if settings.llm_provider == "ollama" else "id"
        models = [item.get(model_key) for item in model_items if isinstance(item, dict)]
        result["llm"] = {
            "reachable": True,
            "provider": settings.llm_provider,
            "models": models,
            "configured_model_present": settings.llm_model in models,
        }
    except Exception as exc:
        result["llm"] = {
            "reachable": False,
            "provider": settings.llm_provider,
            "configured_model_present": False,
            "error_type": type(exc).__name__,
        }

    result["nim"] = await _nim_health(settings)

    try:
        redis = Redis.from_url(str(settings.celery_broker_url))
        try:
            await redis.ping()
            result["redis"] = {
                "reachable": True,
                "agent_runtime_queue_depth": await redis.llen("agent_runtime"),
            }
        finally:
            await redis.aclose()
    except Exception as exc:
        result["redis"] = {"reachable": False, "error_type": type(exc).__name__}

    worker_ping, scheduled_tasks, host = await asyncio.gather(
        asyncio.to_thread(_worker_ping),
        asyncio.to_thread(_scheduled_task_states),
        asyncio.to_thread(_host_health),
    )
    result["worker_ping"] = worker_ping
    result["scheduled_tasks"] = scheduled_tasks
    result["host"] = host
    return result


def _failed(
    result: dict[str, Any],
    *,
    require_llm: bool | None = None,
    require_ollama: bool | None = None,
    require_nim: bool = False,
) -> bool:
    database = result.get("database", {})
    nim = result.get("nim", {})
    llm = result.get("llm", result.get("ollama", {}))
    require_llm = require_llm if require_llm is not None else bool(require_ollama)
    meta = result.get("meta", {})
    redis = result.get("redis", {})
    host = result.get("host", {})
    task_states = result.get("scheduled_tasks", {})
    return bool(
        result.get("app_env") != "production"
        or result.get("app_debug")
        or result.get("production_configuration_errors")
        or not result.get("whatsapp_credentials_configured")
        or not meta.get("reachable")
        or not meta.get("phone_id_matches")
        or not meta.get("waba_id_matches")
        or meta.get("subscribed_apps_count", 0) < 1
        or not database.get("tenant_found")
        or database.get("tenant_status") != TenantStatus.ACTIVE.value
        or not database.get("tenant_waba_bound")
        or database.get("alembic_revision") != _EXPECTED_ALEMBIC_REVISION
        or not database.get("runtime_table_present")
        or database.get("runtime_role_superuser")
        or database.get("runtime_role_bypassrls")
        or not database.get("active_waba_unique_index_present")
        or database.get("duplicate_active_waba_bindings", 1) > 0
        or database.get("configured_waba_active_tenant_count") != 1
        or database.get("duplicate_inbound_wa_ids", 1) > 0
        or database.get("duplicate_tenant_phone_identities", 1) > 0
        or (
            database.get("active_human_reviewers", 0) < 1
            and not database.get("customer_handoff_contact_configured")
        )
        or (database.get("selection_enabled") and database.get("active_human_reviewers", 0) < 1)
        or not database.get("target_agent_live_approved")
        or not redis.get("reachable")
        or not host.get("reachable")
        or host.get("cpu_load_average_percent", 0) >= _CPU_AVERAGE_BLOCK_PERCENT
        or host.get("cpu_load_max_percent", 0) >= _CPU_PEAK_BLOCK_PERCENT
        or not result.get("worker_ping")
        or any(state.lower() != "running" for state in task_states.values())
        or (
            require_llm and (not llm.get("reachable") or not llm.get("configured_model_present"))
        )
        or (require_nim and any(not entry.get("ready") for entry in nim.values()))
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-slug", default="kasnak")
    parser.add_argument("--require-llm", action="store_true")
    parser.add_argument("--require-ollama", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--require-nim",
        action="store_true",
        help="fail unless every configured self-hosted NIM endpoint reports ready",
    )
    args = parser.parse_args()
    result = asyncio.run(inspect(args.tenant_slug))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(
        1
        if _failed(
            result,
            require_llm=args.require_llm or args.require_ollama,
            require_nim=args.require_nim,
        )
        else 0
    )


if __name__ == "__main__":
    main()
