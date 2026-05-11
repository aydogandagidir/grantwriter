# Sprint 4 Preparation

Pre-conditions for Sprint 4 (`docs/sprint-roadmap.md` §4) — "Production deploy + 2 pilot müşteri + ilk paying customer (Bluedev)". Everything below must land before the Day 16 kickoff (Monday 09:00) or Day 16 will burn on setup instead of pilot work.

---

## 1. Production secrets — Railway

### Required
| Variable | Source | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic console | Platform-managed key; BYOK tenants override per-tenant |
| `OPENAI_API_KEY` | OpenAI console | Fallback provider + embeddings |
| `LLM_MASTER_ENCRYPTION_KEY` | `openssl rand -base64 32` | **Do NOT regenerate** without the rotation runbook (see `infra/supabase/migrations/20260510120000_byok_hardening.sql` header) |
| `DATABASE_URL` | Supabase prod project | Connection pooling URL preferred |
| `REDIS_URL` | Upstash / Railway Redis | Used for SSE pub-sub + rate limiting + citation cache |
| `SUPABASE_URL` | Supabase prod project | `https://<project>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase prod project | Storage uploads + admin writes only — never exposed client-side |
| `SUPABASE_JWT_SECRET` | Supabase prod project → Settings → API → JWT secret | Backend uses HS256 to verify Supabase JWTs |
| `IYZICO_API_KEY` | Iyzico merchant panel (prod) | Public/API key |
| `IYZICO_SECRET_KEY` | Iyzico merchant panel (prod) | Outbound signing |
| `IYZICO_BASE_URL` | `https://api.iyzipay.com` | Prod; sandbox is `https://sandbox-api.iyzipay.com` |
| `IYZICO_WEBHOOK_SECRET` | Iyzico merchant panel → Webhooks → Signature secret | HMAC-SHA256 |
| `IYZICO_CALLBACK_URL` | `https://app.bluedev.dev/billing/return` | FE return URL after hosted checkout |
| `RESEND_API_KEY` | Resend dashboard | `re_*` prefix |
| `EMAIL_FROM` | `Bluedev GrantWriter <noreply@bluedev.dev>` | Must match verified Resend domain |
| `APP_URL` | `https://app.bluedev.dev` | Invite accept URL composition |
| `SENTRY_DSN` | Sentry org `bluedev` → project `grantwriter-api` | TICKET-003 |
| `SENTRY_ENVIRONMENT` | `production` | |
| `LOGTAIL_TOKEN` | Better Stack / Logtail source token | TICKET-003 |

### Optional but recommended
| Variable | Default | Notes |
|---|---|---|
| `OBSERVABILITY_ENABLED` | `true` | Kill-switch for Sentry + Logtail in case of init regression |
| `EMAIL_ENABLED` | `true` | Kill-switch for Resend send (independent of API key presence) |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.05` | 5% traces in production; 0 = errors-only |

### Action
- Day 16 (Monday) morning: Aydoğan adds all of the above to the Railway project's "Variables" tab. Use `railway variables set KEY=VAL --env production`.
- Verify with `railway run -- env | grep -E "(ANTHROPIC|IYZICO|RESEND|SENTRY)"` — should print all values masked.

---

## 2. Production secrets — Vercel

### Required (NEXT_PUBLIC_* are baked into the browser bundle by design)

| Variable | Source | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Same as backend `SUPABASE_URL` | |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase prod → Settings → API → anon public key | Browser auth client uses this + cookies |
| `NEXT_PUBLIC_API_URL` | `https://api.bluedev.dev` | Backend public URL |

### Action
- Day 16: Sn creates a Vercel project linked to the GitHub repo + `apps/web` root directory + the three envs above. Trigger first deploy.
- Custom domain: `app.bluedev.dev` CNAME → Vercel.

---

## 3. Supabase production project

### Provisioning
- New project named `bluedev-grantwriter-prod` in the EU (Frankfurt) region.
- Connect via Supabase CLI: `supabase link --project-ref <ref>`.
- Apply migrations: `supabase db push` (all 13 SQL files in `infra/supabase/migrations/`).
- Verify RLS policies live: `supabase db inspect rls`.

### Settings
- **Auth providers:** Email/password enabled. Magic link off (security trade-off; revisit). OAuth providers off.
- **JWT expiry:** 1 hour access, 30 day refresh.
- **Custom domain (optional):** `auth.bluedev.dev`.
- **Storage bucket:** `exports` (private, read via signed URLs only).

### Backups
- Daily snapshot enabled (default in paid plans).
- Day 16: DR drill — restore last night's snapshot into a `bluedev-grantwriter-staging` project and verify migrations apply cleanly.

---

## 4. Resend domain verification

- Add `bluedev.dev` (or whichever sender domain) in Resend dashboard.
- Add the DKIM, SPF, and DMARC records to the DNS zone.
- Wait ~1 hour for verification.
- Send a test email from Resend playground to `aydogan.dagidir@yahoo.com.tr` to confirm delivery.

---

## 5. Iyzico merchant setup

- Confirm production merchant account (separate from sandbox).
- Configure webhook URL: `https://api.bluedev.dev/api/v1/billing/iyzico-webhook`.
- Save the production `Signature secret` → goes into `IYZICO_WEBHOOK_SECRET`.
- Create two pricing plans:
  - `iyz_pro_monthly` — $49/month, 50 proposals/month
  - `iyz_enterprise_monthly` — custom (contact)
- Verify `apps/api/src/billing/plan_mapping.py` matches these reference codes.

---

## 6. Sentry + Logtail wiring

See `docs/sprint-3-known-issues.md` TICKET-003 for the full checklist. Short version:

1. Provision Sentry org + project, copy DSN to Railway.
2. Provision Logtail source, copy token to Railway.
3. Trigger `/health/sentry-test` (we add this endpoint Day 16) → expect one event scrubbed of any BYOK shape.

---

## 7. Pilot customer pipeline

| Customer | Status | Sprint 4 day |
|---|---|---|
| Bluedev (internal — Aydoğan) | Confirmed | Day 17 onboarding |
| Pilot 2 | TBD by Day 16 EOD | Day 19 onboarding |
| Pilot 3 (stretch) | Optional | Day 20 |

### Pilot brief template
Send each pilot a brief request 48 hours before their onboarding day:
- Target programme (HE / TÜBİTAK / KOSGEB / Cascade)
- Call/topic ID
- Brief paragraph in their voice + key project assets to upload
- Schedule of 30-min onboarding call

---

## 8. Open follow-up items

### Closure-merge dependencies

- [ ] PR #8 — CI fix (ruff + flaky marker) — must merge before main is green
- [ ] PR #9 — Sprint 3 FE → main (rebased from PR #6 base)
- [ ] PR #10 — Sprint 3 closure docs (this batch)

After all three merge, main contains:
- Full Sprint 3 backend (already there)
- Full Sprint 3 frontend
- Closure documentation + a11y/mobile additions
- Green CI

### Tickets opened from closure

- TICKET-001 — pgvector rerank determinism
- TICKET-002 — distinctiveness integration fixture isolation
- TICKET-003 — Sentry/Logtail wiring

---

## Definition of "ready for Sprint 4 Day 16"

- [ ] All Sprint 3 PRs merged
- [ ] main CI green for 24 hours
- [ ] Railway prod env vars filled in + smoke deploy successful
- [ ] Vercel prod env vars filled in + smoke deploy successful
- [ ] Supabase prod project provisioned + migrations applied + DR drill passed
- [ ] Resend domain verified
- [ ] Iyzico prod merchant account confirmed + webhook URL configured
- [ ] Sentry + Logtail receiving events from staging
- [ ] Pilot 1 (Bluedev) brief ready
- [ ] Pilot 2 confirmed
