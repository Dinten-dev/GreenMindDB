PYTHON ?= python3

.PHONY: help dev stop logs clean build config health migrate \
	test test-backend test-frontend test-docker test-cov \
	lint format format-check setup install-hooks

help: ## Show maintained developer commands
	@printf '\n  GreenMind – available commands\n\n'
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@printf '\n'

# ── Docker stack ────────────────────────────────────────────────────

config: ## Validate the canonical Compose configuration
	docker compose config --quiet

dev: config ## Build and start the canonical local stack
	docker compose up -d --build

stop: ## Stop services without deleting data
	docker compose stop

logs: ## Follow logs from all services
	docker compose logs -f

build: ## Build all Docker images
	docker compose build

clean: ## Stop services and remove named volumes (destructive)
	docker compose down --volumes --remove-orphans
	@echo "Named volumes were removed. Bind-mounted PGDATA_DIR/MINIO_DATA_DIR were not deleted."

health: ## Check backend, frontend, and MinIO health
	@curl -fsS http://localhost:8000/health >/dev/null && echo "backend: healthy"
	@curl -fsS http://localhost:3000 >/dev/null && echo "frontend: healthy"
	@curl -fsS http://localhost:9000/minio/health/live >/dev/null && echo "minio: healthy"

migrate: ## Apply Alembic migrations in the running backend container
	docker compose exec backend alembic upgrade head

# ── Tests and quality gates ─────────────────────────────────────────

test: test-backend test-frontend ## Run backend and frontend unit suites

test-backend: ## Run backend tests that do not require Docker fixtures
	cd backend && SKIP_DOCKER_TESTS=1 \
		JWT_SECRET_KEY=ci-test-secret-key-that-is-at-least-32-chars \
		$(PYTHON) -m pytest tests/ -v --tb=short -x -m "not integration"

test-frontend: ## Run frontend Jest tests once
	cd frontend && npm test -- --ci --passWithNoTests

test-docker: ## Run the Docker-backed backend test stack
	./scripts/run_docker_tests.sh

test-cov: ## Run backend tests with the CI coverage gate
	cd backend && SKIP_DOCKER_TESTS=1 \
		JWT_SECRET_KEY=ci-test-secret-key-that-is-at-least-32-chars \
		$(PYTHON) -m pytest tests/ -v --tb=short -x -m "not integration" \
		--cov=app --cov-report=term-missing --cov-report=html:htmlcov --cov-fail-under=60
	@echo "Coverage report: backend/htmlcov/index.html"

lint: ## Run Ruff, ESLint, and TypeScript checks
	cd backend && $(PYTHON) -m ruff check app/ tests/
	cd frontend && npm run lint
	cd frontend && npm run type-check

format: ## Format maintained backend and frontend source
	cd backend && $(PYTHON) -m ruff format app/ tests/
	cd frontend && npm run format

format-check: ## Verify formatting without changing files
	cd backend && $(PYTHON) -m ruff format --check app/ tests/
	cd frontend && npm run format:check

# ── Setup ───────────────────────────────────────────────────────────

setup: ## Copy local config and install development dependencies
	@test -f .env || cp .env.example .env
	cd backend && $(PYTHON) -m pip install --require-hashes -r requirements.lock
	cd backend && $(PYTHON) -m pip install -r requirements-dev.txt
	cd frontend && npm ci
	@echo "Setup complete. Replace every CHANGE_ME value in .env, then run 'make dev'."

install-hooks: ## Install the repository pre-commit hooks
	$(PYTHON) -m pip install pre-commit
	$(PYTHON) -m pre_commit install
