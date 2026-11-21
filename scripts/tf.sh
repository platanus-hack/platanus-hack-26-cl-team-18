#!/usr/bin/env bash
# Terraform wrapper for terraform/envs/<env>. Use it (or the make tf-* targets)
# instead of bare terraform so every operator gets the same guards:
#   - credentials from the gitignored .env.render, checked before any API call
#   - one plan/apply per env per machine (flock)
#   - apply only ever applies the plan you just reviewed (tfplan), never a fresh one
#   - tfplan is deleted after apply: it embeds state, including the DB password
#
# Usage: scripts/tf.sh <env> <init|plan|apply|output|test|validate|state|import|console> [args...]
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
ENV=${1:-}
CMD=${2:-}
shift 2 2>/dev/null || true

usage() {
  echo "usage: scripts/tf.sh <env> <init|plan|apply|output|test|validate|state|import|console> [args...]" >&2
  exit 2
}
[[ -n "$ENV" && -n "$CMD" ]] || usage

ENV_DIR="$ROOT_DIR/terraform/envs/$ENV"
[[ -d "$ENV_DIR" ]] || { echo "ERROR: no Terraform env at terraform/envs/$ENV" >&2; exit 2; }
cd "$ENV_DIR"

export TF_IN_AUTOMATION=1
export TF_INPUT=0

load_credentials() {
  local creds="$ROOT_DIR/.env.render"
  if [[ -f "$creds" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$creds"
    set +a
  fi
  local missing=0
  for name in RENDER_API_KEY RENDER_OWNER_ID; do
    if [[ -z "${!name:-}" ]]; then
      echo "ERROR: $name is not set. Copy .env.render.example to .env.render and fill it in." >&2
      missing=1
    fi
  done
  [[ "$missing" == 0 ]] || exit 1
}

acquire_lock() {
  # Terraform's local backend lock only covers one process. This makes a second
  # plan/apply for the same env fail fast with a clear message instead.
  if ! command -v flock >/dev/null 2>&1; then
    echo "WARN: flock not found (macOS: brew install flock); concurrent runs are not prevented." >&2
    return
  fi
  exec 9>"${TMPDIR:-/tmp}/team18-terraform-${ENV}.lock"
  if ! flock -n 9; then
    echo "ERROR: another Terraform run for '$ENV' is in progress on this machine." >&2
    exit 1
  fi
}

ensure_init() {
  if [[ ! -d .terraform ]]; then
    bash "$ROOT_DIR/scripts/terraform_init.sh"
  fi
}

run_terraform() {
  # Explain the one lock error people misread as corrupted state.
  local log status
  log=$(mktemp)
  set +e
  terraform "$@" 2>&1 | tee "$log"
  status=${PIPESTATUS[0]}
  set -e
  if [[ "$status" != 0 ]] && grep -q "Error acquiring the state lock" "$log"; then
    echo "" >&2
    echo "ERROR: another Terraform run holds the '$ENV' state lock (see Lock Info above)." >&2
    echo "  Wait for it. Only 'terraform force-unlock <ID>' once the holder is confirmed dead." >&2
  fi
  rm -f "$log"
  return "$status"
}

case "$CMD" in
  init)
    bash "$ROOT_DIR/scripts/terraform_init.sh" "$@"
    ;;
  validate)
    ensure_init
    terraform validate "$@"
    ;;
  test)
    ensure_init
    terraform test "$@"
    ;;
  plan)
    load_credentials
    acquire_lock
    ensure_init
    run_terraform plan -out=tfplan "$@"
    echo ""
    echo "==> Plan saved to terraform/envs/$ENV/tfplan. Read it, then: make tf-apply ENV=$ENV"
    if terraform show -json tfplan | jq -e '[.resource_changes[]? | select(.change.actions | index("delete"))] | length > 0' >/dev/null; then
      echo "WARN: this plan DELETES or REPLACES resources:" >&2
      terraform show -json tfplan | jq -r '.resource_changes[] | select(.change.actions | index("delete")) | "  - \(.address) \(.change.actions | join("/"))"' >&2
    fi
    ;;
  apply)
    load_credentials
    acquire_lock
    ensure_init
    if [[ ! -f tfplan ]]; then
      echo "ERROR: no saved plan. Run 'make tf-plan ENV=$ENV' and review it first." >&2
      exit 1
    fi
    # Terraform itself refuses a stale plan (state changed since it was made).
    status=0
    run_terraform apply tfplan "$@" || status=$?
    rm -f tfplan
    exit "$status"
    ;;
  output | state | import | console)
    load_credentials
    ensure_init
    terraform "$CMD" "$@"
    ;;
  *)
    usage
    ;;
esac
