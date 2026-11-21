#!/usr/bin/env bash
# Run from a Terraform root (terraform/envs/<env>). Extra args go to init.
# Offline-first init with the committed lock file, and
# bounded retries only for provider *network* failures.
set -euo pipefail

if [[ ! -f .terraform.lock.hcl ]]; then
  echo 'ERROR: deployment requires a committed .terraform.lock.hcl.' >&2
  exit 1
fi

# A plugin cache still queries registry metadata. An explicit filesystem mirror
# avoids that dependency entirely when all locked packages are already installed.
# Terraform itself remains responsible for checking their locked checksums.
metadata=$(terraform version -json)
platform=$(jq -er '.platform' <<< "$metadata")
packages=$(jq -er '.provider_selections | to_entries | map(.key + "/" + .value) | .[]' <<< "$metadata")
provider_dir="${TF_DATA_DIR:-.terraform}/providers"
use_local=true
while IFS= read -r package; do
  found=false
  for binary in "$provider_dir/$package/$platform"/terraform-provider-*; do
    if [[ -f "$binary" && -x "$binary" ]]; then
      found=true
      break
    fi
  done
  if [[ "$found" != true ]]; then
    use_local=false
    break
  fi
done <<< "$packages"

if [[ "$use_local" == true ]]; then
  echo '==> Initializing Terraform with installed, locked providers (no registry discovery)...'
  provider_dir=$(cd "$provider_dir" && pwd -P)
  # Fail closed on a checksum/configuration error; never bypass it online.
  exec terraform init -input=false -lockfile=readonly -plugin-dir="$provider_dir" "$@"
fi

echo '==> Locked providers are not all installed; registry access is required.'
# -plugin-dir= clears the saved selection but still forces this invocation to
# search an empty local path on Terraform 1.8.x. Remove only that saved selection
# and omit the flag so init uses the configured/default installation sources.
rm -f -- "${TF_DATA_DIR:-.terraform}/plugin_path"
log=$(mktemp)
trap 'rm -f "$log"' EXIT
for attempt in 1 2 3; do
  if terraform init -input=false -lockfile=readonly -no-color "$@" 2>&1 | tee "$log"; then
    exit 0
  else
    status=$?
  fi
  # Only retry provider transport failures, never configuration, checksum,
  # backend authentication or state errors. No apply is performed by this helper.
  if ! grep -Eq 'Failed to query available provider packages|Failed to install provider' "$log" ||
     ! grep -Eqi 'i/o timeout|no such host|connection refused|connection reset|TLS handshake timeout|Client.Timeout|temporary failure|context deadline exceeded' "$log"; then
    exit "$status"
  fi
  if [[ "$attempt" == 3 ]]; then
    echo 'ERROR: provider downloads failed after 3 attempts. Restore DNS/network access to registry.terraform.io and releases.hashicorp.com, then retry. No apply was run by this initializer.' >&2
    exit "$status"
  fi
  echo "==> Provider network failure; retrying initialization ($attempt/3)..." >&2
  sleep 2
done
