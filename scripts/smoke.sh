#!/usr/bin/env bash
# Post-deploy smoke test for Bluedev GrantWriter (backend + frontend probes).
#
# Usage:
#   ./scripts/smoke.sh
#   API_URL=https://staging-api.example.com FRONTEND_URL=https://staging.example.com ./scripts/smoke.sh
#   TIMEOUT=10 ./scripts/smoke.sh
#
# Sprint 4 Day 16 Aşama C deliverable. Exits 0 if all checks pass, 1 on any failure.

set -uo pipefail

API_URL="${API_URL:-https://grantwriter-api.onrender.com}"
FRONTEND_URL="${FRONTEND_URL:-https://grantwriter-gamma.vercel.app}"
TIMEOUT="${TIMEOUT:-60}"  # Render free tier cold-start can take ~50s; give it room.

TMP="/tmp/smoke.$$"
PASS=0
FAIL=0

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

check() {
  local label="$1"
  local url="$2"
  local expected_http="$3"
  local must_contain="$4"

  # NB: `curl -w "%{http_code}"` ALREADY writes "000" on timeout/network failure
  # AND exits non-zero. An `|| echo "000"` fallback would double up to "000000"
  # in the captured stdout. Without `set -e`, the assignment succeeds either way;
  # we just default to "000" if curl somehow wrote nothing.
  local http_code
  http_code=$(curl -sSL -m "$TIMEOUT" -o "$TMP" -w "%{http_code}" "$url" 2>/dev/null)
  http_code="${http_code:-000}"

  if [ "$http_code" = "000" ]; then
    echo "✗ $label [network error, no response] $url"
    FAIL=$((FAIL + 1))
    return
  fi

  if [ "$http_code" != "$expected_http" ]; then
    echo "✗ $label [HTTP $http_code, expected $expected_http] $url"
    FAIL=$((FAIL + 1))
    return
  fi

  if [ -n "$must_contain" ]; then
    if ! grep -q -- "$must_contain" "$TMP" 2>/dev/null; then
      echo "✗ $label [HTTP $http_code, body missing '$must_contain'] $url"
      FAIL=$((FAIL + 1))
      return
    fi
  fi

  echo "✓ $label [HTTP $http_code] $url"
  PASS=$((PASS + 1))
}

echo "Smoke test: API=$API_URL  FRONTEND=$FRONTEND_URL  TIMEOUT=${TIMEOUT}s"
echo "---"

check "backend /health"        "$API_URL/health"        "200" '"status":"ok"'
check "frontend /"             "$FRONTEND_URL/"         "200" "Bluedev"
check "frontend /icon.svg"     "$FRONTEND_URL/icon.svg" "200" "<svg"

echo "---"
TOTAL=$((PASS + FAIL))
echo "Summary: $PASS/$TOTAL passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
