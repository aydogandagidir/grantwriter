#!/usr/bin/env bash
# Production preflight env validator (CI / pre-push / manual use).
#
# NOTE: the AUTHORITATIVE production preflight is src/core/preflight.py,
# which runs inside the FastAPI lifespan on every boot regardless of host
# config. This bash script is the ops-side mirror — handy in CI, a
# pre-push hook, or a manual "is my env sane?" check — and is kept in
# PARITY with the Python version (same three checks below).
#
# Reads apps/api/.env.production.example, walks every variable under the
# "# ── REQUIRED ──" section, and validates the matching env var with
# three layered checks (mirrors src/core/preflight.py::check_env):
#   1. set + non-empty
#   2. no `<placeholder>` substring
#   3. DATABASE_URL host not bracketed unless a real IPv6 literal
#
# Exit code 0 → all required vars OK.
# Exit code 1 → ≥1 problem; prints the actionable list to stderr. A host
# that wires this into its start command sees the non-zero exit and can
# refuse the deploy.
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

# Validate each required key. Three layered checks per key, mirroring
# the Python preflight in src/core/preflight.py (kept in parity so CI /
# pre-push catches the same problems the in-process lifespan check does):
#   1. set + non-empty
#   2. no `<placeholder>` substring (e.g. an unsubstituted <ref>)
#   3. DATABASE_URL host not wrapped in [...] unless it's an IPv6 literal
# First failing check for a key short-circuits the rest for that key.
placeholder_re='<[^>]+>'
bracket_re='@\[([^]]+)\]'
problems=()
for key in "${required_keys[@]}"; do
  # `${VAR:-}` treats unset AND empty-string the same way.
  value="${!key:-}"

  if [[ -z "$value" ]]; then
    problems+=("$key: required but not set")
    continue
  fi

  if [[ "$value" =~ $placeholder_re ]]; then
    problems+=("$key: contains a placeholder ${BASH_REMATCH[0]} — substitute the real value")
    continue
  fi

  if [[ "$key" == "DATABASE_URL" && "$value" =~ $bracket_re ]]; then
    host="${BASH_REMATCH[1]}"
    # IPv6 literals contain ':' and legally require brackets; a bracketed
    # hostname (no ':') is the copy-paste artifact that crashes Python's
    # urlsplit. This ':' heuristic is the bash-friendly stand-in for the
    # Python side's exact ipaddress check.
    if [[ "$host" != *:* ]]; then
      problems+=("DATABASE_URL: host wrapped in [...] but '$host' is not an IPv6 literal — remove the brackets")
      continue
    fi
  fi
done

if (( ${#problems[@]} > 0 )); then
  echo "preflight: ${#problems[@]} env problem(s) of ${#required_keys[@]} required vars:" >&2
  for p in "${problems[@]}"; do
    echo "  - $p" >&2
  done
  echo "" >&2
  echo "preflight: fix these in your host's dashboard and redeploy." >&2
  echo "preflight: full list of required vars lives in apps/api/.env.production.example" >&2
  exit 1
fi

echo "preflight: all ${#required_keys[@]} required production env vars OK; handing off to uvicorn"
exit 0
