"""Fail-closed production runtime settings tests."""

from src.core.config import Settings

_STRONG_TEST_SECRET = "fK9!vT2@qL7#sN4$wR8%mC5^xP1&zD6*"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "app_debug": False,
        "app_secret_key": _STRONG_TEST_SECRET,
        "database_url": "postgresql+asyncpg://app:pass@db/app",
        "redis_url": "redis://redis:6379/0",
        "celery_broker_url": "redis://redis:6379/1",
        "celery_result_backend": "redis://redis:6379/2",
        "whatsapp_app_secret": "app-secret",
        "whatsapp_access_token": "access-token",
        "whatsapp_verify_token": "verify-token",
        "whatsapp_phone_number_id": "phone-id",
        "whatsapp_business_account_id": "waba-id",
        "whatsapp_agent_slug": "arti-kasnak",
        "llm_provider": "ollama",
        "llm_model": "qwen3:8b",
        "llm_base_url": "http://127.0.0.1:11434/v1",
    }
    values.update(overrides)
    return Settings(**values)


def test_complete_production_runtime_settings_are_accepted() -> None:
    assert _settings().production_runtime_errors() == []


def test_missing_webhook_secret_and_debug_mode_fail_closed() -> None:
    errors = _settings(whatsapp_app_secret="", app_debug=True).production_runtime_errors()

    assert "WHATSAPP_APP_SECRET is required" in errors
    assert "APP_DEBUG must be false" in errors


def test_unsupported_production_provider_is_rejected() -> None:
    assert (
        "LLM_PROVIDER must be ollama or openai_compatible"
        in _settings(llm_provider="").production_runtime_errors()
    )


def test_openai_compatible_production_provider_is_accepted() -> None:
    assert _settings(llm_provider="openai_compatible").production_runtime_errors() == []


def test_known_placeholder_app_secrets_are_rejected_without_echoing_them() -> None:
    placeholder = "change-me-to-a-strong-random-secret-at-least-32-chars"
    settings = _settings(app_secret_key=placeholder)
    errors = settings.production_runtime_errors()

    assert "APP_SECRET_KEY must be a strong randomly generated value" in errors
    assert all(placeholder not in error for error in errors)
    assert settings.app_secret_key_security == {
        "length": len(placeholder),
        "placeholder_detected": True,
        "low_entropy_detected": False,
        "production_acceptable": False,
    }


def test_low_entropy_and_repeated_app_secrets_are_rejected() -> None:
    settings = _settings(app_secret_key="abcd" * 16)

    assert (
        "APP_SECRET_KEY must be a strong randomly generated value"
        in settings.production_runtime_errors()
    )
    assert settings.app_secret_key_security["placeholder_detected"] is False
    assert settings.app_secret_key_security["low_entropy_detected"] is True
    assert settings.app_secret_key_security["production_acceptable"] is False


def test_strong_app_secret_exposes_only_sanitized_strength_metadata() -> None:
    settings = _settings()

    assert settings.app_secret_key_security == {
        "length": len(_STRONG_TEST_SECRET),
        "placeholder_detected": False,
        "low_entropy_detected": False,
        "production_acceptable": True,
    }


def test_invalid_graph_api_version_is_rejected() -> None:
    errors = _settings(whatsapp_graph_api_version="latest").production_runtime_errors()

    assert "WHATSAPP_GRAPH_API_VERSION must look like v20.0" in errors
