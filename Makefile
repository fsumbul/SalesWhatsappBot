.PHONY: help up down install dev test lint format migrate migration api web api-shell db-shell clean

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
	@echo "  lint        Lint API + Web"
	@echo "  format      Auto-format code"
	@echo "  migrate     Apply DB migrations"
	@echo "  migration MSG='...' Create new migration"
	@echo "  api-shell   Open API container shell"
	@echo "  db-shell    Open psql shell"
	@echo "  clean       Remove build artifacts"

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
