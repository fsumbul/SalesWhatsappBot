# LeadPulse API

FastAPI backend for the LeadPulse platform.

## Development

```bash
poetry install
cp ../../.env.example ../../.env  # from repo root
poetry run alembic upgrade head
poetry run uvicorn src.main:app --reload
```

Then visit http://localhost:8000/docs for the OpenAPI UI.

## Structure

```
src/
  core/         Cross-cutting concerns (config, db, security, tenancy, logging)
  modules/      Bounded contexts (sectors, discovery, ...)
  workers/      Celery task modules
  integrations/ External API clients (WA, Google, SerpAPI, ...)
  main.py       FastAPI app entrypoint
alembic/        Database migrations
tests/          Pytest test suite
```
