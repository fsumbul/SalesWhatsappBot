"""Pytest configuration.

For Phase 0 we only ship the app-level health test. In later phases we'll add
`testcontainers` fixtures for a real Postgres + Redis.
"""

import os

# Ensure a valid secret key exists before Settings loads
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-with-enough-length-1234567890")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://leadpulse:leadpulse_dev@localhost:5432/leadpulse_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/10")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/11")
os.environ.setdefault("APP_ENV", "test")
