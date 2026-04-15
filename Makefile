# ──────────────────────────────────────────────────────────────────────
# GreenMind – Developer Convenience Commands
# ──────────────────────────────────────────────────────────────────────
.PHONY: help dev stop logs clean build test lint format seed health setup

# Default target
help: ## Show this help
	@echo ""
	@echo "  GreenMind – Available Commands"
	@echo "  ──────────────────────────────────────"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ─── Docker Stack ────────────────────────────────────────────────────

dev: ## Start the full Docker stack (build + detach)
	docker compose up -d --build

stop: ## Stop all containers
	docker compose stop

logs: ## Tail logs from all containers
	docker compose logs -f

clean: ## Stop containers, remove volumes (full reset)
	docker compose down -v
	@echo "⚠️  Remember to also remove your PGDATA_DIR if using external storage."

build: ## Build all Docker images
	docker compose build

health: ## Check service health
	@echo "Backend:"
	@curl -sf http://localhost:8000/health && echo " ✅" || echo " ❌"
	@echo "Frontend:"
	@curl -sf http://localhost:3000 > /dev/null && echo " ✅" || echo " ❌"

seed: ## Seed demo data
	docker compose exec backend python -m scripts.seed_data

# ─── Backend ─────────────────────────────────────────────────────────

test: ## Run backend tests
	cd backend && python -m pytest tests/ -v --tb=short -x

lint: ## Lint backend (ruff) + frontend (eslint)
	cd backend && python -m ruff check app/ tests/
	cd frontend && npm run lint

format: ## Format backend (black)
	cd backend && python -m black app/ tests/

lint-fix: ## Auto-fix lint issues (ruff)
	cd backend && python -m ruff check app/ tests/ --fix

# ─── Setup ───────────────────────────────────────────────────────────

setup: ## Initial project setup (copy .env, install deps)
	@test -f .env || cp .env.example .env
	@echo "✅ .env created (edit it with your settings)"
	cd backend && pip install -r requirements.txt
	cd frontend && npm install
	@echo ""
	@echo "✅ Setup complete. Run 'make dev' to start."

install-hooks: ## Install pre-commit hooks
	pip install pre-commit
	pre-commit install
	@echo "✅ Pre-commit hooks installed."
