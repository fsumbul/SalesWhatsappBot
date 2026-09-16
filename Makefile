.PHONY: help up down install dev test check-local lint format migrate migration api web api-shell db-shell clean agent-worker knowledge-index knowledge-search knowledge-reembed

help:
	@echo "LeadPulse - Make targets"
	@echo ""
	@echo "  up          Start dev infra (postgres, redis, mailhog)"
	@echo "  down        Stop dev infra"
	@echo "  install     Install API + Web dependencies"
	@echo "  dev         Run API and Web dev servers concurrently"
	@echo "  api         Run only API dev server"
	@echo "  web         Run only Web dev server"
	@echo "  test        Run all tests"
	@echo "  check-local Run API tests with reports, lint, types and Web build locally"
	@echo "  lint        Lint API + Web"
	@echo "  format      Auto-format code"
	@echo "  migrate     Apply DB migrations"
	@echo "  migration MSG='...' Create new migration"
	@echo "  api-shell   Open API container shell"
	@echo "  db-shell    Open psql shell"
	@echo "  clean       Remove build artifacts"
	@echo "  agent-worker      Run the WhatsApp reply + knowledge Celery worker"
	@echo "  knowledge-index   Build the GraphRAG index for a tenant (TENANT=kasnak)"
	@echo "  knowledge-search  Evaluate retrieval on the golden set (TENANT_ID=.. VERSION_ID=..)"
	@echo "  knowledge-reembed Rebuild a tenant's chunk graph after an embedding profile change (TENANT=kasnak)"

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down

install:
	cd apps/api && poetry install
	cd apps/web && pnpm install

dev:
	@echo "Starting API on :8000 and Web on :3000..."
	@trap 'kill 0' EXIT; \
	  (cd apps/api && poetry run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000) & \
	  (cd apps/web && pnpm dev) & \
	  wait

api:
	cd apps/api && poetry run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd apps/web && pnpm dev

test:
	cd apps/api && poetry run pytest -v
	cd apps/web && pnpm test --if-present

# Prepare an isolated test database and export test configuration first.
# Reports stay on this device; no GitHub Actions runner is involved.
check-local:
	mkdir -p test-results/local
	cd apps/api && APP_ENV=test REQUIRE_DB_TESTS=1 poetry run pytest -v --cov=src --cov-branch --cov-report=term-missing --cov-report=xml:../../test-results/local/coverage.xml --cov-report=json:../../test-results/local/coverage.json --junitxml=../../test-results/local/junit.xml --durations=20
	$(MAKE) lint
	cd apps/web && pnpm typecheck && pnpm build

lint:
	cd apps/api && poetry run ruff check . && poetry run mypy src
	cd apps/web && pnpm lint

format:
	cd apps/api && poetry run ruff format . && poetry run ruff check --fix .
	cd apps/web && pnpm format --if-present

migrate:
	cd apps/api && poetry run alembic upgrade head

migration:
	@if [ -z "$(MSG)" ]; then echo "Usage: make migration MSG='describe change'"; exit 1; fi
	cd apps/api && poetry run alembic revision --autogenerate -m "$(MSG)"

api-shell:
	docker compose -f infra/docker-compose.yml exec api sh

db-shell:
	docker compose -f infra/docker-compose.yml exec postgres psql -U leadpulse -d leadpulse

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf apps/web/.next apps/web/node_modules/.cache

agent-worker:
	cd apps/api && poetry run celery -A src.core.agent_celery_app.agent_celery_app worker -l info -Q agent_runtime,knowledge --pool=solo

knowledge-index:
	cd apps/api && poetry run python scripts/knowledge_index.py --tenant-slug $(or $(TENANT),kasnak)

knowledge-search:
	cd apps/api && poetry run python scripts/knowledge_search.py --tenant-id $(TENANT_ID) --version-id $(VERSION_ID) --golden config/knowledge_golden.arti_kasnak.json

knowledge-reembed:
	cd apps/api && poetry run python scripts/knowledge_reembed.py --tenant-slug $(or $(TENANT),kasnak)
