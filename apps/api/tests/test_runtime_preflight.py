"""Pure hard-gate checks for the production runtime preflight."""

from scripts.runtime_preflight import _failed


def _healthy_result() -> dict[str, object]:
    return {
        "app_env": "production",
        "app_debug": False,
        "production_configuration_errors": [],
        "whatsapp_credentials_configured": True,
        "meta": {
            "reachable": True,
            "phone_id_matches": True,
            "waba_id_matches": True,
            "subscribed_apps_count": 1,
        },
        "database": {
            "tenant_found": True,
            "tenant_status": "active",
            "tenant_waba_bound": True,
            "alembic_revision": "d9e6f1a2b035",
            "runtime_table_present": True,
            "runtime_role_superuser": False,
            "runtime_role_bypassrls": False,
            "active_waba_unique_index_present": True,
            "duplicate_active_waba_bindings": 0,
            "configured_waba_active_tenant_count": 1,
            "duplicate_inbound_wa_ids": 0,
            "duplicate_tenant_phone_identities": 0,
            "active_human_reviewers": 0,
            "customer_handoff_contact_configured": True,
            "target_agent_live_approved": True,
        },
        "ollama": {"reachable": True, "configured_model_present": True},
        "redis": {"reachable": True},
        "host": {
            "reachable": True,
            "cpu_load_average_percent": 25,
            "cpu_load_max_percent": 40,
        },
        "worker_ping": True,
        "scheduled_tasks": {
            "AshiraaiApi": "Running",
            "AshiraaiAgentWorker": "Running",
            "AshiraaiAgentRecovery": "Running",
        },
    }


def test_customer_contact_route_satisfies_handoff_gate_without_dashboard_user() -> None:
    assert _failed(_healthy_result(), require_ollama=True) is False


def test_preflight_rejects_sustained_host_cpu_saturation() -> None:
    result = _healthy_result()
    result["host"]["cpu_load_average_percent"] = 90

    assert _failed(result, require_ollama=True) is True


def test_preflight_rejects_critical_host_cpu_peak() -> None:
    result = _healthy_result()
    result["host"]["cpu_load_max_percent"] = 98

    assert _failed(result, require_ollama=True) is True


def test_preflight_rejects_ambiguous_active_waba_routing() -> None:
    result = _healthy_result()
    result["database"]["duplicate_active_waba_bindings"] = 1
    result["database"]["configured_waba_active_tenant_count"] = 2

    assert _failed(result, require_ollama=True) is True


def test_preflight_requires_active_waba_unique_index() -> None:
    result = _healthy_result()
    result["database"]["active_waba_unique_index_present"] = False

    assert _failed(result, require_ollama=True) is True


def test_preflight_requires_exactly_one_active_tenant_for_configured_waba() -> None:
    result = _healthy_result()
    result["database"]["configured_waba_active_tenant_count"] = 0

    assert _failed(result, require_ollama=True) is True


def test_ollama_is_optional_only_for_the_safe_contact_fallback_mode() -> None:
    result = _healthy_result()
    result["ollama"] = {"reachable": False, "configured_model_present": False}

    assert _failed(result, require_ollama=False) is False
    assert _failed(result, require_ollama=True) is True


def test_selection_requires_an_active_human_reviewer():
    result = _healthy_result()
    result["database"]["selection_enabled"] = True
    assert _failed(result, require_llm=True)
    result["database"]["active_human_reviewers"] = 1
    assert not _failed(result, require_llm=True)


def test_preflight_revision_matches_current_migration_head() -> None:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from scripts.runtime_preflight import _EXPECTED_ALEMBIC_REVISION

    api = Path(__file__).resolve().parents[1]
    config = Config(str(api / "alembic.ini"))
    config.set_main_option("script_location", str(api / "alembic"))
    assert ScriptDirectory.from_config(config).get_heads() == [_EXPECTED_ALEMBIC_REVISION]


def test_require_nim_gates_on_every_configured_endpoint() -> None:
    result = _healthy_result()
    result["nim"] = {
        "guardrail.jailbreak": {"kind": "classify", "ready": True},
        "guardrail.content_safety": {"kind": "llm", "ready": False},
    }

    assert _failed(result, require_ollama=True) is False
    assert _failed(result, require_ollama=True, require_nim=True) is True

    result["nim"]["guardrail.content_safety"]["ready"] = True
    assert _failed(result, require_ollama=True, require_nim=True) is False


def test_require_nim_passes_when_nothing_is_configured() -> None:
    result = _healthy_result()
    result["nim"] = {}
    assert _failed(result, require_ollama=True, require_nim=True) is False
