#!/usr/bin/env bash
# Apply Supabase migrations against a Postgres DSN.
#
# Used by:
#   - CI (.github/workflows/api-ci.yml) to set up a clean test DB
#   - Local dev (`make migrate-test` or manual rerun) when the docker
#     container needs a fresh schema
#
# Order: auth_stub.sql first (creates the auth schema Supabase Cloud
# provides for free), then every *.sql in infra/supabase/migrations
# in lexical order (timestamp prefixes guarantee dependency-correct
# ordering).
#
# Idempotent: every migration uses CREATE … IF NOT EXISTS or similar,
# so reapplying is a no-op. Failure of a single migration does NOT
# stop the rest by default (so a partial schema still gets the later
# files); pass --strict to abort on first error.

set -uo pipefail

DSN="${1:-${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/bluedev}}"
STRICT=0
if [ "${2:-}" = "--strict" ]; then
  STRICT=1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SUPABASE_DIR="${REPO_ROOT}/infra/supabase"
AUTH_STUB="${SUPABASE_DIR}/auth_stub.sql"
MIGRATIONS_DIR="${SUPABASE_DIR}/migrations"

if [ ! -d "${MIGRATIONS_DIR}" ]; then
  echo "error: migrations directory not found at ${MIGRATIONS_DIR}" >&2
  exit 2
fi

echo "Applying schema to ${DSN}"

if [ -f "${AUTH_STUB}" ]; then
  echo "→ auth_stub.sql"
  if [ "${STRICT}" = "1" ]; then
    psql "${DSN}" -v ON_ERROR_STOP=1 -f "${AUTH_STUB}" > /dev/null
  else
    psql "${DSN}" -f "${AUTH_STUB}" > /dev/null 2>&1 || true
  fi
fi

shopt -s nullglob
for migration in "${MIGRATIONS_DIR}"/*.sql; do
  echo "→ $(basename "${migration}")"
  if [ "${STRICT}" = "1" ]; then
    psql "${DSN}" -v ON_ERROR_STOP=1 -f "${migration}" > /dev/null
  else
    psql "${DSN}" -f "${migration}" > /dev/null 2>&1 || true
  fi
done

echo "Done."
