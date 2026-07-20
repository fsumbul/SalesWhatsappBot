"""Alembic environment.

Reads DB URL from application settings, converts to sync psycopg URL for
Alembic (which runs migrations synchronously), and uses metadata from
`src.core.db.Base`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.core.config import get_settings
from src.core.db import Base
from src import models_registry  # noqa: F401 — populates Base.metadata

# Alembic Config object
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the database URL (convert asyncpg → psycopg for offline/sync migrations).
# Migrations run as the privileged role (DDL, role/grant management) — see
# Settings.migrations_database_url — which is distinct from the restricted
# role the running app uses.
_settings = get_settings()
_migrations_url = _settings.migrations_database_url or _settings.database_url
_url = str(_migrations_url).replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
