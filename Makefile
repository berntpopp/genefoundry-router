.PHONY: help install lock upgrade sync format format-check lint lint-ci lint-fix lint-loc typecheck typecheck-fresh test test-fast test-unit test-integration test-cov test-all check ci-local precommit clean run validate doctor list-tools docker-build docker-up docker-down docker-logs docker-prod-config docker-npm-config dev-fleet run-dev test-e2e snapshot-fleet snapshot-baseline snapshot-catalog ci-full

.DEFAULT_GOAL := help

PKG := genefoundry_router
DOCKER_COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install project and development dependencies with uv
	uv sync --group dev

sync: install ## Alias for install

lock: ## Resolve and update uv.lock
	uv lock

upgrade: ## Upgrade locked dependencies
	uv lock --upgrade

format: ## Format Python code
	uv run ruff format $(PKG) tests

format-check: ## Check formatting without writing
	uv run ruff format --check $(PKG) tests

lint: ## Lint Python code
	uv run ruff check $(PKG) tests

lint-ci: ## Lint without modifying files (CI output)
	uv run ruff check $(PKG) tests --output-format=github

lint-fix: ## Lint and apply safe fixes
	uv run ruff check $(PKG) tests --fix

lint-loc: ## Enforce per-file line budget
	uv run python scripts/check_file_size.py

typecheck: ## Type check package
	uv run mypy $(PKG)

typecheck-fresh: ## Clear mypy cache and run typecheck
	rm -rf .mypy_cache
	uv run mypy $(PKG)

test: ## Run unit tests quickly
	uv run pytest tests/unit -q

test-fast: ## Run unit tests in parallel
	uv run pytest tests/unit -q -n auto

test-unit: test-fast ## Alias for parallel unit tests

test-integration: ## Run in-process integration, conformance, and discoverability-benchmark tests
	uv run pytest tests/integration tests/conformance tests/discoverability -q || [ $$? -eq 5 ]  # exit 5 = none collected

test-cov: ## Run tests with coverage
	uv run pytest tests/unit tests/integration tests/conformance tests/discoverability --cov=$(PKG) --cov-report=term-missing --cov-report=html --cov-report=xml

test-all: test-cov ## Alias for full test run with coverage

check: format lint ## Format and lint

ci-local: format-check lint-ci lint-loc typecheck test-fast test-integration ## Fast local CI-equivalent checks

precommit: ci-local ## Run checks expected before commit

clean: ## Remove local caches and generated reports
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml

run: ## Run the router over Streamable HTTP locally (exports .env)
	set -a; [ -f .env ] && . ./.env; set +a; uv run genefoundry-router run --host 127.0.0.1 --port 8000

validate: ## Validate servers.yaml + env
	uv run genefoundry-router validate

doctor: ## Ping each backend and report reachability
	uv run genefoundry-router doctor

list-tools: ## Enumerate federated tools
	uv run genefoundry-router list-tools

bench-discoverability: ## Benchmark tool discoverability over the catalog snapshot (offline)
	uv run python scripts/discoverability_report.py --min-score 9.0

docker-build: ## Build Docker image
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml build

docker-up: ## Start Docker dev stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d

docker-down: ## Stop Docker dev stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml down

docker-logs: ## Follow Docker logs
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml logs -f

docker-rebuild: ## Rebuild image and (re)start the local stack (reads ../.env)
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d --build

docker-restart: ## Recreate the container to re-read ../.env (no image rebuild)
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d --force-recreate

docker-prod-config: ## Render production Compose configuration
	GF_ALLOWED_HOSTS=$${GF_ALLOWED_HOSTS:-genefoundry.org} \
		GF_HEALTHCHECK_HOST=$${GF_HEALTHCHECK_HOST:-genefoundry.org} \
		$(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config

docker-npm-config: ## Render NPM Compose configuration
	$(DOCKER_COMPOSE) --env-file .env.docker.example -f docker/docker-compose.yml -f docker/docker-compose.prod.yml -f docker/docker-compose.npm.yml config

dev-fleet: ## Run the offline fake MCP fleet (port 9100)
	uv run python -m genefoundry_router.devtools.fake_fleet

run-dev: ## Run the router against the fake fleet (exports .env.dev)
	set -a; . ./.env.dev; set +a; uv run genefoundry-router run --servers-file servers.dev.yaml

test-e2e: ## Run the offline end-to-end fake-fleet tests
	uv run pytest tests/e2e -q

snapshot-fleet: ## Refresh the fleet manifest from live backends (online)
	uv run python scripts/snapshot_fleet.py --out tests/fixtures/fleet_manifest.json \
		--captured-at $$(date -u +%FT%TZ)

snapshot-catalog: ## Regenerate the discoverability benchmark catalog from the live fleet (online)
	uv run --env-file ci/fleet-urls.env python scripts/snapshot_catalog.py

snapshot-baseline: ## Re-pin the packaged drift baseline from the live fleet (online)
	uv run --env-file ci/fleet-urls.env python scripts/snapshot_fleet.py \
		--out genefoundry_router/data/fleet-baseline.json --captured-at $$(date -u +%FT%TZ)

ci-full: ci-local test-e2e ## Fast CI plus the offline e2e suite

.PHONY: conformance
conformance:  ## Probe a live MCP server: make conformance MCP_URL=... NAME=... TIER=stateless
	uv run python -m genefoundry_router.conformance $(MCP_URL) --name $(NAME) --tier $(or $(TIER),stateless)

.PHONY: fleet-probe
fleet-probe:  ## Probe EVERY enabled backend's live /mcp for transport conformance (online)
	uv run --env-file ci/fleet-urls.env genefoundry-router fleet-probe
