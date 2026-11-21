SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV ?= prod
TF  := scripts/tf.sh $(ENV)
TF_ROOTS := terraform/modules/render-app terraform/envs/prod

.PHONY: help install dev web-install web-dev web-check contract contract-check lint format typecheck test check migrate migrate-status \
        tf-init tf-fmt tf-fmt-check tf-validate tf-test tf-check tf-plan tf-apply tf-output tf-lock

help: ## Show this help
	@awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z0-9_-]+:.*## / { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ------------------------------------------------------------------ app

install: ## Create .venv with locked dev dependencies
	uv sync --extra dev

dev: ## Run the backend with reload on :8000 (HTTP + WebSockets)
	uv run uvicorn app.main:app --reload --port 8000

web-install: ## Install web/ dependencies (npm ci)
	cd web && npm ci

web-dev: ## Run the Vite dev server on :5173 (proxies /ws, /health, /media, /dev to :8000)
	cd web && npm run dev

web-check: ## web/: typecheck + lint + format + tests + knip (what CI runs)
	cd web && npm run check

contract: ## Regenerate web/src/contract/messages.gen.ts from app/contract/messages.py
	uv run python scripts/gen_contract.py

contract-check: ## Fail if the generated TS contract is stale
	uv run python scripts/gen_contract.py --check

lint: ## Ruff lint
	uv run ruff check .

format: ## Ruff format (writes files)
	uv run ruff format .

typecheck: ## mypy over app/
	uv run mypy app

test: ## Unit tests (no DB, no network)
	uv run pytest -q

check: lint typecheck test tf-check ## Everything CI runs, locally

migrate: ## Apply pending SQL migrations to DATABASE_URL
	uv run python scripts/migrate.py

migrate-status: ## Show applied/pending migrations for DATABASE_URL
	uv run python scripts/migrate.py status

# ------------------------------------------------------------------ terraform (Render)
# Offline targets (no credentials): tf-fmt, tf-fmt-check, tf-validate, tf-test, tf-check.
# Online targets read RENDER_API_KEY / RENDER_OWNER_ID from .env.render.

tf-init: ## terraform init for ENV (default prod), using the committed lock file
	$(TF) init

tf-fmt: ## terraform fmt -recursive (writes files)
	terraform fmt -recursive terraform

tf-fmt-check: ## Fail if any .tf file is unformatted
	terraform fmt -check -recursive terraform

tf-validate: ## terraform validate every root (no credentials)
	@set -e; for d in $(TF_ROOTS); do \
	  echo "==> validate $$d"; \
	  terraform -chdir=$$d init -backend=false -input=false -lockfile=readonly >/dev/null; \
	  terraform -chdir=$$d validate -no-color; \
	done

tf-test: ## terraform test every root against a mocked provider (no credentials)
	@set -e; for d in $(TF_ROOTS); do \
	  echo "==> test $$d"; \
	  terraform -chdir=$$d init -backend=false -input=false -lockfile=readonly >/dev/null; \
	  terraform -chdir=$$d test -no-color; \
	done

tf-check: tf-fmt-check tf-validate tf-test ## Offline Terraform gate (what CI runs)

tf-plan: ## Plan ENV and save it to tfplan for review
	$(TF) plan

tf-apply: ## Apply the reviewed tfplan for ENV (never a fresh plan)
	$(TF) apply

tf-output: ## Show outputs for ENV (OUT=name for one raw value)
	@if [ -n "$(OUT)" ]; then $(TF) output -raw $(OUT); echo; else $(TF) output; fi

tf-lock: ## Refresh .terraform.lock.hcl for every platform the team uses (after a provider bump)
	@set -e; for d in $(TF_ROOTS); do \
	  terraform -chdir=$$d init -backend=false -input=false -upgrade >/dev/null; \
	  terraform -chdir=$$d providers lock -platform=linux_amd64 -platform=linux_arm64 \
	    -platform=darwin_amd64 -platform=darwin_arm64 -platform=windows_amd64; \
	done
