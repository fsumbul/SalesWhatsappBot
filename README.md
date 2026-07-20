# LeadPulse

Multi-tenant B2B lead generation & WhatsApp outreach SaaS platform.

First vertical: elevator sheave sales. Sector-agnostic core.

## Stack

- **Backend:** Python 3.12 + FastAPI + SQLAlchemy 2 + Celery + Redis
- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui
- **DB:** PostgreSQL 16 (with Row-Level Security)
- **Infra:** Docker Compose + Caddy + GitHub Actions

## Monorepo Layout

```
apps/
  api/            FastAPI backend
  web/            Next.js frontend
packages/
  shared/         Shared types (OpenAPI-generated)
infra/
  docker-compose.yml
  caddy/
docs/
```

## Quick Start (Development)

Prerequisites: Docker, Docker Compose, Node.js 20+, pnpm, Python 3.12, Poetry, Make.

```bash
# 1. Copy env template
cp .env.example .env

# 2. Start infra (postgres, redis, mailhog)
make up

# 3. Install dependencies
make install

# 4. Run migrations
make migrate

# 5. Start dev servers (API on :8000, Web on :3000)
make dev
```

Visit:
- Web: http://localhost:3000
- API docs: http://localhost:8000/docs
- Mailhog: http://localhost:8025

## Common Commands

| Command | What it does |
|---|---|
| `make up` | Start Postgres, Redis, Mailhog |
| `make down` | Stop infra containers |
| `make install` | Install API + Web dependencies |
| `make dev` | Run API + Web dev servers |
| `make test` | Run all tests |
| `make lint` | Lint all code |
| `make migrate` | Apply DB migrations |
| `make migration MSG="..."` | Create new migration |

## Project Phases

See [PROJECT_PLAN.md](PROJECT_PLAN.md) and [BUILD_PROMPT.md](BUILD_PROMPT.md).

## License

MIT
