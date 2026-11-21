SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV ?= prod
TF  := scripts/tf.sh $(ENV)
TF_ROOTS := terraform/modules/render-app terraform/modules/render-static-site terraform/envs/prod
LOCAL_DATABASE_URL := postgresql://team18:team18@localhost:5432/team18

.PHONY: help install edge-install edge-check test-live test-sandbox dev web-install web-dev web-check contract contract-check lint format typecheck test check migrate migrate-status db-up db-down db-reset \
        eval eval-baseline eval-creative eval-creative-baseline eval-replay eval-perception eval-record eval-rehearsal eval-video-fetch eval-video eval-pipeline demo-warmup \
        tf-init tf-fmt tf-fmt-check tf-validate tf-test tf-check tf-plan tf-apply tf-output tf-lock

help: ## Show this help
	@awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z0-9_-]+:.*## / { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ------------------------------------------------------------------ app

install: ## Create .venv with locked dev dependencies
	uv sync --extra dev

dev: ## Run the backend with reload on :8000 (HTTP + WebSockets)
	uv run uvicorn app.main:app --reload --port 8000

edge-install: ## Install the perception agent (edge/, Python 3.12) with dev tools
	cd edge && env -u VIRTUAL_ENV uv sync --extra dev

edge-check: ## edge/: ruff + format + mypy + tests (what CI runs)
	cd edge && env -u VIRTUAL_ENV uv run ruff check . && env -u VIRTUAL_ENV uv run ruff format --check . \
	  && env -u VIRTUAL_ENV uv run mypy agent && env -u VIRTUAL_ENV uv run pytest

test-sandbox: ## Real processes (backend + edge agent) against a local fake ElevenLabs; no credentials
	uv run pytest -m sandbox -o addopts="" -v tests/integration/test_sandbox_e2e.py

test-live: ## LIVE ElevenLabs tests (spend credits; needs ELEVENLABS_API_KEY)
	uv run pytest -m live -s tests/integration
	cd edge && env -u VIRTUAL_ENV uv run pytest -m live -s

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

typecheck: ## mypy over app/ and evals/
	uv run mypy app evals

test: ## Unit tests (no DB, no network)
	uv run pytest -q

check: lint typecheck test tf-check ## Everything CI runs, locally

# ------------------------------------------------------------------ evals (docs/evals.md)
# The product feedback loop: run the real loop, score it, ratchet the baseline.

EVAL_REC_OUT ?= evals/results/rehearsal.jsonl

eval: ## Scenarios + soak on a live backend -> evals/results/latest.md (fails on gate/regression)
	uv run python -m evals $(ARGS)

eval-baseline: ## Lock improved ratchet metrics into evals/baseline.json (refuses a failing run)
	uv run python -m evals --update-baseline

eval-creative: ## Creative generation eval: targeting, safety, quality, latency, cost (ARGS="--writer claude" spends)
	uv run python -m evals creative $(ARGS)

eval-creative-baseline: ## Lock improved creative ratchets into evals/creative/baseline.json
	uv run python -m evals creative --update-baseline $(ARGS)

eval-replay: ## Real-video perception recording -> live backend (REC=path.jsonl [SPEED=1])
	uv run python -m evals replay $(REC) --speed $(or $(SPEED),1)

eval-perception: ## Score the agent on a labelled clip (LABELS=labels.json REC=recording.jsonl)
	uv run python -m evals perception $(LABELS) $(REC)

eval-record: ## Record a live rehearsal from :8000 until Ctrl-C (OUT=path.jsonl)
	uv run python -m evals record --out $(or $(OUT),$(EVAL_REC_OUT))

eval-rehearsal: ## Score a recorded rehearsal (REC=path.jsonl)
	uv run python -m evals rehearsal $(REC)

eval-video-fetch: ## Download the CC stock clips in evals/video/clips.json (gitignored)
	uv run python -m evals.video.fetch

eval-video: ## Stock video -> edge agent -> live backend -> ad-fit report (ARGS="--perceiver standin --only ID --reuse")
	uv run python -m evals.video.run $(ARGS)

eval-pipeline: ## End-to-end ad pipeline journeys: stage waterfall + bottleneck (ARGS="--cold --copy-scale 2")
	uv run python -m evals pipeline $(ARGS)

demo-warmup: ## Demo day: warm AI copy + voice-over caches on the running backend (same daypart as the demo)
	uv run python -m evals warmup $(ARGS)

migrate: ## Apply pending SQL migrations to DATABASE_URL
	uv run python scripts/migrate.py

migrate-status: ## Show applied/pending migrations for DATABASE_URL
	uv run python scripts/migrate.py status

# Local Postgres goes through the same migrate.py ledger as Render's pre-deploy,
# so `make migrate` against it never re-runs an applied file.
db-up: ## Start local Postgres (docker compose) and apply migrations
	docker compose up -d --wait postgres
	DATABASE_URL=$(LOCAL_DATABASE_URL) uv run python scripts/migrate.py

db-down: ## Stop local Postgres (keeps the data volume)
	docker compose down

db-reset: ## Wipe the local Postgres volume and re-apply every migration
	docker compose down -v
	$(MAKE) db-up

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
