# LeadPulse

Multi-tenant B2B lead generation & WhatsApp outreach SaaS platform.

First vertical: elevator sheave sales. Sector-agnostic core.

## Stack

- **Backend:** Python 3.12 + FastAPI + SQLAlchemy 2 + Celery + Redis
- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui
- **DB:** PostgreSQL 16 (with Row-Level Security)
- **Knowledge graph:** FalkorDB (GraphRAG index: BGE-M3 vectors + Turkish full-text + graph lineage, bge-reranker-v2-m3) and per-customer memory graph — see [ADR-002](docs/adr/ADR-002-falkordb-graphrag-retrieval.md)
- **Local LLM:** Qwen3 8B via Ollama (decision-only in strict mode; in hybrid mode it may write audited, evidence-cited descriptive answers — see [ADR-003](docs/adr/ADR-003-self-service-knowledge-and-hybrid-answers.md))
- **Self-service knowledge:** tenants upload PDF/XLSX/CSV/Markdown or register their website; facts and product images are extracted locally, auto-published when safe and revocable in one click
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

## Model server configuration

The model runtime is independent of the application host and operating system.
It can be Ollama on another machine, the optional Docker service, or a
self-hosted chat-compatible server. See the Turkish
[model server guide](docs/model-sunucusu-rehberi.md) for environment variables,
Docker, vLLM, LocalAI, and llama.cpp examples.

## GraphRAG retrieval (optional, ADR-002)

```bash
ollama pull qwen3:8b && ollama pull bge-m3      # local models
# .env: KNOWLEDGE_BACKEND=falkordb  EMBEDDING_PROVIDER=ollama  RERANKER_ENABLED=true
make knowledge-index TENANT=kasnak               # build the FalkorDB index for the LIVE agent
make agent-worker                                # reply + knowledge queues
```

Retrieval only proposes approved fact candidates; the model still chooses ids and the
server renders literal `customer_text`. With the settings left empty the runtime keeps
its in-process lexical selector.

## Common Commands

| Command | What it does |
|---|---|
| `make up` | Start Postgres, Redis, Mailhog, FalkorDB |
| `make agent-worker` | WhatsApp reply worker (`agent_runtime` + `knowledge` queues) |
| `make knowledge-index TENANT=slug` | Build/verify the GraphRAG index for a tenant's LIVE agent |
| `make knowledge-search TENANT_ID=… VERSION_ID=…` | Evaluate retrieval on the Turkish golden set |
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
