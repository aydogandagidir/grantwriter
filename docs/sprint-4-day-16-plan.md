# Sprint 4 — Day 16 Plan

> Atomic step breakdown for production deploy. Builds on `docs/sprint-4-prep.md` (preconditions matrix) and `docs/sprint-4-handoff-prompt.md` (new-session brief). Targets Monday EOD: `api.bluedev.dev` + `app.bluedev.dev` live, smoke tests passing, monitoring wired, pilot onboarding cleared for Tuesday.

---

## Lane summary

| Lane | Owner | Blocks | Output |
|---|---|---|---|
| **Aşama A** — pre-deploy validation (code) | Claude | nothing | `infra/railway.json`, `infra/vercel.json`, `.env.production.example`, `scripts/preflight-check.sh`, preflight test |
| **Aşama B** — production secrets | Aydoğan + Sn | Aşama A merged | Railway / Vercel / Supabase / Iyzico / Resend / Sentry / Logtail values populated |
| **Aşama C** — post-deploy verification | Claude (driven by user smoke tests) | Aşama B done | Health probes passing, Sentry test event, E2E smoke, DR drill report |
| **Aşama D** — monitoring + alerts | Claude + Aydoğan | Aşama C done | Sentry rules, PostHog wired, Better Stack uptime monitor, release tracking confirmed |

---

## Aşama A — Pre-deploy validation (code-side)

Single PR, no production touchpoints. Lands the templates, deploy configs, and a CI guard that fails if a deploy is attempted against an under-configured environment.

### A1 — `apps/api/.env.production.example`

Mirror every variable in `docs/sprint-4-prep.md` §1, commented with intent. Two sections — **required** and **optional/kill-switches** — so an operator copying this file to Railway misses nothing.

### A2 — `apps/web/.env.production.example`

Mirror `docs/sprint-4-prep.md` §2. Only three `NEXT_PUBLIC_*` env vars — keep the file short and explicit.

### A3 — `infra/railway.json`

Railway project config (deploy section + health check). Dockerfile path = `apps/api/Dockerfile`. Start command bakes `SENTRY_RELEASE` from the Railway-injected `RAILWAY_GIT_COMMIT_SHA` so the deployed Sentry events tag correctly.

### A4 — `infra/vercel.json`

Vercel project config — root directory `apps/web`, framework `nextjs`, ignored paths (`apps/api/**`, `infra/**`, `docs/**`) so backend-only commits don't trigger redeploys.

### A5 — `scripts/preflight-check.sh`

Reads `.env.production.example`, walks every line marked `# REQUIRED`, asserts the matching env var is set + non-empty in the current shell. Exit code 1 on any missing. Runs as the first step of Railway's start command.

### A6 — `apps/api/tests/test_preflight.py`

Two unit-ish tests:

1. `test_env_example_lists_every_settings_field` — parses `.env.production.example` and asserts every `Settings` field with no default OR with `SecretStr | None = None` AND marked production-required shows up.
2. `test_preflight_check_passes_with_full_env` — uses `subprocess` to run `scripts/preflight-check.sh` with a fake env exporting every required var; asserts exit 0.

These two are cheap, CI-friendly, and stop drift between the template + the Settings class.

### A7 — Commit + push + PR

Single commit, single PR. Title: `feat(infra): production deploy preflight (Sprint 4 Day 16 Aşama A)`. Body lists what each file does and links back to this plan.

---

## Aşama B — Production secrets (user-side, sequential)

> Each step has a verify-it-worked check the user can run. Claude asks one platform at a time, waits for the user's "OK", moves to the next.

1. **Supabase production project** (~20 min)
   - Create `bluedev-grantwriter-prod` in EU/Frankfurt.
   - `supabase link --project-ref <ref>`.
   - `supabase db push` against `infra/supabase/migrations/`.
   - Verify: `supabase db inspect rls` lists policies on `tenant_invitations`, `tenant_llm_config`, `proposal_versions`, `proposal_comments`, `tenant_usage_log`, `billing_events`.
   - Collect: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.

2. **Resend** (~15 min + 1 h DNS propagation)
   - Add `bluedev.dev` sender domain.
   - Drop DKIM, SPF, DMARC records into Cloudflare / Namecheap.
   - Wait, then send a test mail from Resend playground.
   - Collect: `RESEND_API_KEY`.

3. **Iyzico production** (~30 min)
   - Confirm prod merchant account.
   - Configure webhook URL: `https://api.bluedev.dev/api/v1/billing/iyzico-webhook`.
   - Save the production signature secret.
   - Create plans `iyz_pro_monthly` ($49/mo, 50 proposals) and `iyz_enterprise_monthly` (custom).
   - Verify `apps/api/src/billing/plan_mapping.py` plan reference codes match.
   - Collect: `IYZICO_API_KEY`, `IYZICO_SECRET_KEY`, `IYZICO_WEBHOOK_SECRET`.

4. **Sentry org + project** (~10 min)
   - Org `bluedev`, project `grantwriter-api` (platform: python).
   - Collect: `SENTRY_DSN`.

5. **Logtail** (~10 min)
   - Better Stack → Logs → new source `grantwriter-api-production`.
   - Collect: `LOGTAIL_TOKEN`.

6. **Railway project** (~30 min)
   - New project, link to repo, root = `apps/api`.
   - Paste every secret matrix item from `docs/sprint-4-prep.md` §1.
   - Set start command: `SENTRY_RELEASE=$RAILWAY_GIT_COMMIT_SHA bash scripts/preflight-check.sh && uvicorn src.main:app --host 0.0.0.0 --port $PORT`
   - Custom domain: `api.bluedev.dev` (CNAME to Railway).
   - Trigger first deploy.

7. **Vercel project** (~20 min)
   - Link repo, root = `apps/web`.
   - Paste the three `NEXT_PUBLIC_*` env vars.
   - Custom domain: `app.bluedev.dev` (CNAME to Vercel).
   - Trigger first deploy.

---

## Aşama C — Post-deploy verification

Once Aşama B's deploys are live, Claude walks the user through:

1. `curl https://api.bluedev.dev/health` → 200 + `{"status":"ok","version":"..."}`.
2. `curl https://api.bluedev.dev/health/sentry-test` → 500 + Sentry shows one event tagged `release=<git sha>` with the BYOK canary redacted.
3. `https://app.bluedev.dev` opens → Supabase signup new user works → onboarding page renders.
4. **E2E smoke** (manual):
   - Sign up → confirm email → log in.
   - Settings → Members → invite `aydogan.dagidir@yahoo.com.tr`.
   - Verify Resend email arrives.
   - Settings → BYOK → store an Anthropic test key.
   - Billing → checkout → Iyzico sandbox / production redirect.
   - Dashboard → "New proposal" → quick brief → generate → wait for completion email.
   - Export DOCX → open in Word.
5. **DR drill** — snapshot the prod Supabase, restore into `bluedev-grantwriter-staging`, verify schema matches.
6. **Production smoke test summary** — Claude writes a short report into `docs/sprint-4-day-16-smoke-report.md` and commits.

---

## Aşama D — Monitoring + alerts

1. **Sentry alert rules**
   - Error rate > 1 per minute → Slack `#alerts`.
   - New issue → Slack `#alerts` (immediate).
   - Performance issue (p95 > 5s on `/api/v1/proposals/generate`) → Slack `#alerts`.

2. **PostHog frontend instrumentation**
   - Add `posthog-js` to `apps/web` (dep) → init in root layout with `NEXT_PUBLIC_POSTHOG_KEY`.
   - Wire `posthog.identify()` to the Supabase user on auth state change.
   - Standard events: `proposal_created`, `proposal_generated`, `byok_configured`, `member_invited`.

3. **Better Stack uptime monitor**
   - Two monitors at 1-minute interval: `https://api.bluedev.dev/health`, `https://app.bluedev.dev`.
   - Fail → email + Slack.

4. **Sentry release tracking**
   - First post-deploy event's `release` tag matches `RAILWAY_GIT_COMMIT_SHA`.
   - Confirm Sentry "Releases" page lists the SHA.

---

## Definition of done — Day 16

- [ ] PR for Aşama A merged to main, CI green.
- [ ] Production Supabase, Railway, Vercel, Resend, Iyzico, Sentry, Logtail all configured (Aşama B complete).
- [ ] `https://api.bluedev.dev/health` + `/health/sentry-test` return correct shapes.
- [ ] `https://app.bluedev.dev` signup → onboarding → settings flows work end-to-end.
- [ ] Manual E2E smoke from Aşama C step 4 completes.
- [ ] DR drill report committed.
- [ ] Monitoring + alerts active (Aşama D).
- [ ] Day 17 ready: Bluedev's own pilot brief input ready.

---

## Carry-over to Day 17+

- **Onboarding flow (Sn)** — proper 2-page wizard replacing the current stub at `apps/web/src/app/(app)/onboarding/page.tsx`.
- **TipTap editor (Aydoğan)** — real proposal editor replacing `/proposals/[id]` stub, with provenance metadata per sentence (docs/06 + docs/09 §4).
- **Playwright E2E (Claude/Sn)** — convert the manual Aşama C step 4 into Playwright scripts so the next deploy is gated by them.
- **Saga auto-snapshot** — small PR, post-saga `/versions` POST with `comment="auto-snapshot after generation"`.
- **Resend bounce/delivery webhook** — pull from Faz 2 to Sprint 4 if pilots flag reputation issues.

---

## Risk gates

1. **Aşama B sırası kritik.** Supabase → Resend → Iyzico → Sentry/Logtail → Railway → Vercel sırası bilinçli; Railway prod deploy `DATABASE_URL` ister, o da Supabase'siz olmaz. Sırayı değiştirme.
2. **Iyzico sandbox → prod geçişinde** `IYZICO_BASE_URL`'i unutma. Prod merchant'a sandbox URL ile gidersen 401 alırsın.
3. **Vercel custom domain** TLS sertifikasının propagation'ı 5-30 dk sürebilir. İlk deploy sonrasında `https://app.bluedev.dev` direkt çalışmazsa, 20 dk bekle, sonra debug.
4. **DR drill** prod Supabase'i degradasyon riski olmadan snapshot'tan staging'e restore eder — drill ASLA prod'a uygulanmaz. Komut çıktısını user-side confirm et.
5. **Sentry `release` tag set olmazsa**, Railway start command'da `SENTRY_RELEASE=$RAILWAY_GIT_COMMIT_SHA` var mı doğrula. Eksikse Sentry "release: unknown" gösterir, ilk regression bulunması zor olur.
