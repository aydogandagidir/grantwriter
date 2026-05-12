# Sprint 3 Retrospective

**Sprint window:** S3.D1–S3.D15 (backend) + S3.FE.F1–S3.closure (frontend)
**Demo date:** Friday of Sprint 3 (16:00 review, 17:00 retro per `sprint-roadmap.md` §3)

This retro covers what shipped, what slipped, what got rerouted, and the punch list the next sprint inherits.

---

## DoD vs. delivered

| Sprint roadmap line item | Status | Notes |
|---|---|---|
| BYOK encryption (pgcrypto) | ✅ Shipped | `tenant_llm_config` pgcrypto envelope, master-key rotation runbook + script |
| BYOK test endpoint + UI | ✅ Shipped | `POST /api/v1/tenant/llm-config/test` + LlmConfigCard with per-provider test buttons |
| LLMRouter BYOK key resolution | ✅ Shipped | `key_vault.py` round-trip, BYOK-vs-platform-key cost tracking |
| Tenant invitations (DB + endpoint) | ✅ Shipped | Two routers (tenant CRUD + public preview/accept), token-once secret, audit-logged |
| Members CRUD + role updates | ✅ Shipped | Last-owner guard + soft-delete |
| Email invitations (Resend) | ✅ Shipped | Lazy-init, 3 TR/EN templates, scrubber over wire |
| **Stripe subscription + portal** | 🔄 **Rerouted to Iyzico** | User decision — Stripe deferred to Faz 2 |
| Iyzico webhook handler | ✅ Shipped | HMAC-SHA256 signature verify + idempotent billing_events persistence |
| Iyzico outbound (checkout + cancel) | ✅ Shipped | Pure-httpx PKI client, no SDK dep; new endpoints + `tenants.iyzico_subscription_reference` |
| Plan limit DB trigger + quota | ✅ Shipped | Monthly proposal counter, 402 gate at Starter cap |
| Usage report endpoint + UI | ✅ Shipped | Admin-only with KPI cards + by-period table |
| DNSH agent rule + LLM hybrid | ✅ Shipped | 3 deterministic codes stack with LLM `dnsh_inadequate`; HE-only gating |
| Hallucination Hunter claim verification | ✅ Shipped (agent) → **S3.D13.T1 closed in follow-up PR** | Sample-10 LLM verdicts + `<0.6` pass rate blocks export + `validate_proposal` endpoint now runs Hunter alongside Compliance + FE `ValidationPanel` disables export button when blocked + real Crossref `@pytest.mark.integration` smoke. Cost-capped at ~$0.01/draft. |
| Versioning (proposal_versions) | ✅ Shipped | Snapshot-as-new-current restore, history never loses entries |
| Comments DB + endpoint | ✅ Shipped | Single-level threading, author/admin guards, app-level cascade |
| Comments UI (add, resolve, thread) | ✅ Shipped | CommentsPanel in stub proposal editor |
| Rate limiting (Redis sliding window) | ✅ Shipped | Lua-atomic sliding-log, fail-open on Redis outage |
| Audit log — critical events | ✅ Shipped | 12+ action codes, `_validate_diff` secret-leak guard |
| Email notifications (draft complete, member added) | ✅ Shipped | Wired into saga complete + invitation accept; PII-scrubbed failure logs |
| E2E multi-tenant isolation | ✅ Shipped | 10-step named flow asserts BYOK/invite/member/audit/usage/quota/billing/versions/comments isolation |
| **A11y audit (axe-core)** | ✅ Shipped (closure) | 10 jest-axe smoke tests; 2 violations surfaced + fixed |
| **Mobile responsive smoke** | ✅ Shipped (closure) | Dialog-based hamburger drawer reusing sidebar nav items |
| Sentry frontend + backend | ⏸ Wiring in Sprint 4 | Lazy-init code shipped; DSN config waits on prod env |
| Frontend pages (8 settings + auth + dashboard + collaboration panels) | ✅ Shipped | TR/EN bilingual, 24 Vitest tests, `pnpm build` clean (26 prerendered routes) |

**Sprint 3 başarı kriteri** (from roadmap): "Multi-tenant tamamen çalışıyor, BYOK encryption test edildi, Stripe checkout başarılı. AI disclosure compliance %100."

Reword for what we actually shipped:

> Multi-tenant fully working (E2E test asserts 10 isolation dimensions), BYOK encryption tested end-to-end (security suite passes), **Iyzico checkout** working (sandbox flow verified manually), AI disclosure auto-generated for every HE proposal that has provenance rows.

---

## Decisions log

1. **Stripe → Iyzico (S3.D11)** — User chose Iyzico as the sole payment provider for TR-led pilot phase; Stripe deferred to Faz 2. Webhook + outbound client both pure-httpx (no SDK), keeping the dep tree flat.
2. **Resend SDK lazy-init (S3.D11)** — Mirrors the Sentry/Logtail pattern in `core/observability.py`: try-import, no-op when key absent OR package missing, so dev laptops boot cleanly without the optional dep.
3. **Versioning restore strategy: snapshot-as-new-current** — Restoring v(n) inserts a new row and points `proposals.draft` at the old snapshot; the original v(n) row is never mutated. History is append-only.
4. **Comments threading depth ≤ 1** — Single-level replies; refusing 2-level nesting on POST keeps the UI simple and matches how reviewers actually thread real comments.
5. **Hallucination Hunter claim verifier opt-in (S3.D13)** — `router: LLMRouter | None = None`; existing tests pass with router=None so the upgrade is backward-compatible. Sample cap of 10 keeps cost predictable.
6. **DNSH rule layer alongside LLM (S3.D14)** — Three deterministic codes (`dnsh_too_short`, `dnsh_objective_missing`, `dnsh_phrase_missing`) stack with the existing LLM `dnsh_inadequate`; rule layer is the deterministic gate, LLM the qualitative check.
7. **No `comment.edited` / `comment.deleted` audit codes** — Over-instrumentation; the comment table doesn't track history and audit would add noise. Reopen if a customer asks.

---

## Sapmalar (deviations from plan)

| Plan said | What happened | Why |
|---|---|---|
| Stripe checkout | Iyzico checkout | User business decision |
| FE done by Friday | FE done over the weekend (Sprint 3 closure) | PR sequencing — backend shipped first, FE branch then rebased against post-merge main |
| Saga auto-snapshot on generate complete | Manual snapshot only | Scope cut to keep B3 atomic; tracked for Sprint 4 |
| Resend delivery / bounce webhook | Not built | Faz 2 (mailbox provider reputation tracking is a Sprint 4+ concern) |
| Onboarding flow (post-signup tenant provisioning) | Stub `/onboarding` page only | Sprint 4 Day 16 |

---

## Metrics

- **Backend commits:** 9 (S3.D1 → S3.D15)
- **Frontend commits:** 5 (S3.FE.F1 → S3.FE.F13) + 1 closure (a11y + mobile)
- **Closure commits:** 2 PRs (#8 CI fix, #10 docs)
- **Pytest:** 507 passed / 7 deselected (`flaky_pre_s3`) / 0 failed against the CI Postgres+Redis stack
- **Vitest:** 24 passed across 4 suites (cn, API client, Button, a11y smoke)
- **Next.js build:** 13 routes × 2 locales = 26 prerendered HTML pages, middleware 107 kB
- **mypy --strict:** 115 source files, 0 errors
- **ruff:** 0 errors on `apps/api`

## Per-day rough estimates

| Day | Output |
|---|---|
| D1–D3 | Tenant/audit/observability/rate-limit foundation (2 commits) |
| D4–D9 | 8 tenant routes + Iyzico webhook + quota + CI scaffolding (1 commit) |
| D11 | Resend email service + Iyzico outbound checkout (2 commits) |
| D12 | Versioning + Comments APIs (2 commits) |
| D13 | Hallucination Hunter LLM verifier (1 commit) — agent only; gate+FE+integration shipped post-closure as "S3.D13.T1 complete pass" follow-up PR |
| D14 | DNSH rule layer (1 commit) |
| D15 | Multi-tenant E2E test + fixups (2 commits) |
| FE F1–F13 | Tailwind + shadcn + Supabase + i18n + 8 pages + tests (5 commits) |
| Closure | CI ruff fix + flaky marker + a11y + mobile + docs (3 commits, 3 PRs) |
| **S3.D13.T1 carry-over** | `validate_proposal` runs Hunter + `ValidationReport` response + FE `ValidationPanel` (badge + export-button gate) + `tests/citations/test_verifier_integration.py` with `@pytest.mark.integration` + 2 extra agent tests (paraphrased verdict + malformed JSON). Closes the "complete pass" prompt that the Sprint 3 D13 task definition demanded. |

---

## Sprint 4 prereqs (handoff)

See [`docs/sprint-4-prep.md`](sprint-4-prep.md) for the full list. Headlines:

- Railway production project + secrets (ANTHROPIC_API_KEY, OPENAI_API_KEY, LLM_MASTER_ENCRYPTION_KEY, IYZICO_*, RESEND_API_KEY, SENTRY_DSN, LOGTAIL_TOKEN, SUPABASE_*)
- Vercel production project + secrets (NEXT_PUBLIC_SUPABASE_*, NEXT_PUBLIC_API_URL)
- Supabase production project — RLS migrations sync, custom domain
- Resend domain verify (DKIM/SPF for `noreply@bluedev.dev`)
- Iyzico merchant + webhook URL (prod)
- DR drill: snapshot restore staging → production (Day 16)

---

## What worked

- **Resend lazy-init pattern reuse** — Saved time and gave us PII scrubbing for free by mirroring the Sentry/Logtail shape.
- **Pure-httpx Iyzico** — Avoiding the iyzipay SDK kept the deploy footprint clean and let us unit-test PKI auth headers deterministically.
- **`flaky_pre_s3` marker** — Surgical opt-out for the 6 pre-existing flaky tests without losing the rest of pytest's CI signal.
- **Single E2E flow over many small isolation tests** — The 10-step named flow surfaces cross-step bugs ("after A burns quota, B's first proposal still works") that per-endpoint tests would miss.

## What hurt

- **PR #7 merged into PR #6 branch, not main** — Stacked-PR mistake; required a closure-time rebase + new PR (#9). Sprint 4 will use base-branch protections.
- **Local DB pollution** — Two unrelated test suites depend on a fresh DB schema; on developer laptops the cumulative state breaks one of them. Tracked as TICKET-002.
- **Vercel preview without env** — Preview deploy fails every PR because Supabase env vars aren't set. Cosmetic but noisy; setup deferred to Sprint 4 Day 16.
- **Frontend integration testing gap** — Vitest covers primitives + a11y smoke but not the user flows (login → invite → BYOK → checkout). Playwright is on Sprint 4's backlog.

## What's next

- Sprint 4 — production deploy + 2 pilot customers (`sprint-roadmap.md` §4)
- TICKET-001 — pgvector rerank determinism
- TICKET-002 — distinctiveness integration fixture isolation
- TICKET-003 — Sentry/Logtail wiring once prod DSNs land
