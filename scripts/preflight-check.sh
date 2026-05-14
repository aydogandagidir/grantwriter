#!/usr/bin/env bash
# Sprint 4 Day 16 — production preflight env validator.
#
# Runs as the first step of Railway's start command. Reads
# apps/api/.env.production.example, walks every variable that appears
# under the "# ── REQUIRED ──" section, and asserts the matching
# environment variable is set + non-empty in the current process.
#
# Exit code 0 → all required vars present, hand off to uvicorn.
# Exit code 1 → at least one required var missing, prints the list to
# stderr and aborts. Railway sees the non-zero exit and rolls back to
# the previous deploy, so a misconfigured deploy can't take down prod.
#
# Parser contract (also enforced by apps/api/tests/test_preflight.py):
# - Lines starting with `#` or blank lines are skipped EXCEPT the
#   section-header lines that switch parse mode.
# - The required block is delimited by `# ── REQUIRED ──...` on top and
#   `# ── OPTIONAL ` (any continuation) below.
# - Inside the required block, every `KEY=...` line registers KEY as a
#   required variable. The `...` is the placeholder and never read.

set -euo pipefail

# Resolve the example file relative to this script's location so the
# command works whether Railway runs it from /app, /workspace, or
# wherever the Dockerfile lands.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXAMPLE_FILE="$REPO_ROOT/apps/api/.env.production.example"

if [[ ! -f "$EXAMPLE_FILE" ]]; then
  echo "preflight: cannot find env example at $EXAMPLE_FILE" >&2
  exit 2
fi

# Walk the example file, collect required keys.
required_keys=()
in_required_block=0

while IFS= read -r line || [[ -n "$line" ]]; do
  # Section headers — switch parse mode.
  if [[ "$line" =~ ^#[[:space:]]*──[[:space:]]*REQUIRED ]]; then
    in_required_block=1
    continue
  fi
  if [[ "$line" =~ ^#[[:space:]]*──[[:space:]]*OPTIONAL ]]; then
    in_required_block=0
    continue
  fi

  # Skip comments + blank lines.
  if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
    continue
  fi

  if (( in_required_block == 1 )); then
    # Match KEY=value; capture just the key.
    if [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)= ]]; then
      required_keys+=("${BASH_REMATCH[1]}")
    fi
  fi
done <"$EXAMPLE_FILE"

if (( ${#required_keys[@]} == 0 )); then
  echo "preflight: no required keys parsed from $EXAMPLE_FILE — example file malformed" >&2
  exit 2
fi

# Check each required key is set in the environment.
missing=()
for key in "${required_keys[@]}"; do
  # Use `-z "${VAR:-}"` to treat unset AND empty-string the same way.
  value="${!key:-}"
  if [[ -z "$value" ]]; then
    missing+=("$key")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "preflight: missing required production env vars (${#missing[@]} of ${#required_keys[@]}):" >&2
  for key in "${missing[@]}"; do
    echo "  - $key" >&2
  done
  echo "" >&2
  echo "preflight: set these in the Railway dashboard (or the equivalent host) and redeploy." >&2
  echo "preflight: full list of required vars lives in apps/api/.env.production.example" >&2
  exit 1
fi

echo "preflight: all ${#required_keys[@]} required production env vars set; handing off to uvicorn"
exit 0
