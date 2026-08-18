"""Production selector configuration must preserve every secret verbatim."""

from pathlib import Path

from scripts.configure_production_runtime import configure


def test_configure_is_idempotent_and_never_changes_secret_lines(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    secret_line = "APP_SECRET_KEY=fK9!vT2@qL7#sN4$wR8%mC5^xP1&zD6*"
    whatsapp_line = "WHATSAPP_ACCESS_TOKEN=never-print-or-rewrite-this-token"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=development",
                "APP_DEBUG=true",
                "APP_ENV=staging",
                secret_line,
                whatsapp_line,
                "LLM_MODEL=old-model",
                "",
            ]
        ),
        encoding="utf-8",
    )

    first_backup = configure(env_file)
    configured_once = env_file.read_text(encoding="utf-8")

    assert first_backup is not None
    assert first_backup.read_text(encoding="utf-8").count("APP_ENV=") == 2
    assert configured_once.count("APP_ENV=production") == 1
    assert configured_once.count("APP_DEBUG=false") == 1
    assert secret_line in configured_once
    assert whatsapp_line in configured_once

    assert configure(env_file) is None
    assert env_file.read_text(encoding="utf-8") == configured_once
    assert first_backup.read_text(encoding="utf-8").count("APP_ENV=") == 2
