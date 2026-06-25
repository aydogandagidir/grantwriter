# Playwright E2E smoke

Automates Aşama C step 4 from `docs/sprint-4-day-16-plan.md`:

1. **`signup-onboarding.spec.ts`** — fresh user signs up + walks the
   onboarding wizard to a green dashboard.
2. **`byok-flow.spec.ts`** — owner stores an Anthropic test key and
   the `/test` endpoint returns valid.
3. **`member-invitation.spec.ts`** — owner invites a teammate; the
   pending list shows the row; the public `/invitations/{token}`
   preview renders without auth.

The suite runs against a **deployed** environment (staging or prod
smoke), not a local `pnpm dev` server. The stack the tests assume is
running:

- Next.js FE at `E2E_BASE_URL`.
- FastAPI backend reachable from that FE (same domain or separate API
  host wired via `NEXT_PUBLIC_API_URL`).
- Supabase Auth with **email confirmation disabled** for the test
  project (otherwise signup blocks on a Resend email that the spec
  can't click).

## Required env vars

| Variable | Purpose |
|---|---|
| `E2E_BASE_URL` | The FE origin Playwright hits (`https://app-staging.bluedev.dev`). |
| `E2E_TEST_EMAIL` | Long-lived operator account; new signups append `+e2e-<rand>` to its local part so concurrent runs don't collide. |
| `E2E_TEST_PASSWORD` | Password for `E2E_TEST_EMAIL`. |
| `E2E_ANTHROPIC_TEST_KEY` | Optional. Skips the BYOK roundtrip spec when absent. |

When any of the first three are missing the specs `test.skip()` with
a clear reason — so `pnpm e2e` on a dev laptop without secrets stays
green.

Each spec reads env vars inline + calls `test.skip(condition, reason)`
when any required value is missing. No shared fixtures module — keeps
the specs self-contained at the cost of a small amount of duplication
across the three files, and side-steps a Playwright 1.49 quirk where
helper modules that imported from `@playwright/test` would crash
discovery with a `context.conditions?.includes` TypeError.

## Running locally

```powershell
# One-time: download the chromium binary (~120 MB).
pnpm e2e:install

# Run all specs headless.
$env:E2E_BASE_URL = 'https://app-staging.bluedev.dev'
$env:E2E_TEST_EMAIL = 'aydogan.dagidir@yahoo.com.tr'
$env:E2E_TEST_PASSWORD = '<staging-password>'
pnpm e2e

# Interactive runner — single spec, pause + step.
pnpm e2e:ui
```

The HTML report lands in `apps/web/playwright-report/`. Open
`index.html` after a run to see traces / screenshots for failures.

## CI integration

[`.github/workflows/e2e.yml`](../../../.github/workflows/e2e.yml) runs
the same suite on every PR + push to `main` that touches `apps/web`,
`packages/shared-types`, or the workflow itself. The job:

1. Installs pnpm + Node 20.
2. Caches the Playwright browser bundle keyed on `pnpm-lock.yaml`.
3. Runs `pnpm exec playwright test --reporter=html,line`.
4. On failure, uploads `playwright-report/` + `test-results/` as
   artifacts (14-day retention).

The skip-on-missing-env pattern means the workflow is green out of
the box. The operator can wire the real flows by setting the
following **repository secrets**:

| Secret | Maps to |
|---|---|
| `E2E_BASE_URL` | staging URL (e.g. `https://app-staging.bluedev.dev`) |
| `E2E_TEST_EMAIL` | long-lived e2e operator account |
| `E2E_TEST_PASSWORD` | password for that account |
| `E2E_ANTHROPIC_TEST_KEY` | optional — unlocks the BYOK spec |

Once those land, the same workflow exercises signup → onboarding,
BYOK, and member invitation against staging on every merge.

The future "wait for `deploy_staging` then run e2e" gate sits on top
of this workflow once Sprint 4 Aşama A's deploy job is live — see
`docs/sprint-4-day-16-plan.md` for the orchestration intent.
