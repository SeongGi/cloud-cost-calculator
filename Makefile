.PHONY: help install dev-app dev-agent test prices up down logs

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install Node and Python dependencies
	pnpm install
	cd apps/agent && uv sync

dev-agent: ## Agent on http://localhost:8123 (needs local Ollama)
	cd apps/agent && uv run uvicorn main:app --host 127.0.0.1 --port 8123 --reload

dev-app: ## Web app on http://localhost:3000
	pnpm --filter @repo/app dev

test: ## All tests
	cd apps/agent && uv run pytest -q
	pnpm --filter @repo/app test

prices: ## Rebuild bundled AWS/Azure catalogs (GCP needs GCP_ACCESS_TOKEN)
	cd apps/agent && uv run python -m pricing.build_aws && uv run python -m pricing.build_azure

up: ## docker compose up (public port 8095)
	docker compose up -d --build

down: ## docker compose down
	docker compose down

logs: ## Follow container logs
	docker compose logs -f
