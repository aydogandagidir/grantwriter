#!/usr/bin/env bash
# Pilot go-live readiness check (Sprint 4 Day 17).
#
# Deeper than scripts/smoke.sh (which is a fast liveness probe). This
# verifies the FULL stack a pilot's end-to-end run depends on:
#   - process up           (/health 200)
#   - DB reachable          (/health/db 200 — generate/export need it)
#   - schema migrated       (public DB-bound probe → 404, not 503/500)
#   - auth actually gates    (protected endpoint without a token → 401/403)
#   - frontend served        (/ 200, /icon.svg 200)
#
# What it CANNOT check (needs real secrets the operator wires in the
# dashboard): the LLM keys that the draft-generation saga calls. Those
# only exercise on a real authenticated generate request — see the
# reminder this script prints at the end.
#
# Usage:
#   bash scripts/pilot-readiness.sh
#   API_URL=... FRONTEND_URL=... TIMEOUT=90 bash scripts/pilot-readiness.sh
#
# Exit 0 = every checkable precondition green. Exit 1 = at least one gap.

set -uo pipefail

API_URL="${API_URL:-https://grantwriter-api.onrender.com}"
FRONTEND_URL="${FRONTEND_URL:-https://grantwriter-gamma.vercel.app}"
TIMEOUT="${TIMEOUT:-60}"  # Render free-tier cold start headroom.

TMP="/tmp/pilot-readiness.$$"
PASS=0
FAIL=0
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

# http_code <url>  → echoes the status (000 on network failure).
http_code() {
  local code
  code=$(curl -sSL -m "$TIMEOUT" -o "$TMP" -w "%{http_code}" "$1" 2>/dev/null)
  echo "${code:-000}"
}

# pass/fail reporters keep the ✓/✗ + tally logic in one place.
ok()   { echo "✓ $1"; PASS=$((PASS + 1)); }
bad()  { echo "✗ $1"; FAIL=$((FAIL + 1)); }

# expect_status <label> <url> <expected>  (single expected code)
expect_status() {
  local label="$1" url="$2" want="$3" got
  got=$(http_code "$url")
  if [ "$got" = "$want" ]; then ok "$label [HTTP $got]"; else bad "$label [HTTP $got, expected $want] $url"; fi
}

# expect_one_of <label> <url> <code1> <code2>  (either acceptable)
expect_one_of() {
  local label="$1" url="$2" a="$3" b="$4" got
  got=$(http_code "$url")
  if [ "$got" = "$a" ] || [ "$got" = "$b" ]; then ok "$label [HTTP $got]"; else bad "$label [HTTP $got, expected $a/$b] $url"; fi
}

# expect_body_contains <label> <url> <want_status> <substr>
expect_body_contains() {
  local label="$1" url="$2" want="$3" substr="$4" got
  got=$(http_code "$url")
  if [ "$got" != "$want" ]; then bad "$label [HTTP $got, expected $want] $url"; return; fi
  if grep -q -- "$substr" "$TMP" 2>/dev/null; then ok "$label [HTTP $got, body ok]"; else bad "$label [HTTP $got but body missing '$substr']"; fi
}

echo "Pilot readiness: API=$API_URL  FRONTEND=$FRONTEND_URL  TIMEOUT=${TIMEOUT}s"
echo "------------------------------------------------------------"

# 1. Process liveness (also wakes a spun-down Render free instance).
expect_body_contains "backend process  /health"        "$API_URL/health"        "200" '"status":"ok"'

# 2. DB reachable — generate + export + onboarding all need this.
expect_body_contains "database         /health/db"      "$API_URL/health/db"     "200" '"available":true'

# 3. Schema migrated — a public DB-bound route hitting a 3-table JOIN.
#    404 (token not found) proves pool + schema + JOIN; 503=DB down, 500=no table.
expect_status        "schema (probe)   /invitations"    "$API_URL/api/v1/invitations/pilot-readiness-bogus-token" "404"

# 4. Auth actually gates — a protected route WITHOUT a token must be
#    refused (401 or 403 depending on the bearer scheme), never 200.
expect_one_of        "auth gate        /me (no token)"  "$API_URL/api/v1/me" "401" "403"

# 5. Frontend served + favicon (the original console-404 fix).
expect_body_contains "frontend         /"               "$FRONTEND_URL/"         "200" "Bluedev"
expect_status        "frontend favicon /icon.svg"       "$FRONTEND_URL/icon.svg" "200"

echo "------------------------------------------------------------"
TOTAL=$((PASS + FAIL))
echo "Readiness: $PASS/$TOTAL checks passed."

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "NOT READY — resolve the ✗ rows above. See docs/11-operations-runbook.md."
  exit 1
fi

echo ""
echo "All checkable preconditions GREEN."
echo ""
echo "⚠ Reminder — not verifiable from here, do before the pilot run:"
echo "  • LLM keys (ANTHROPIC_API_KEY, OPENAI_API_KEY) set in Render —"
echo "    the draft-generation saga calls them on a real generate request."
echo "  • Once every REQUIRED var is wired, set PREFLIGHT_STRICT=true."
echo "  • Then drive the live pilot E2E: signup → onboarding → org profile"
echo "    → call → brief → generate → draft → export DOCX."
exit 0
