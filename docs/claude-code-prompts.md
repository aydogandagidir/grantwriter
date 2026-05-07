# Claude Code Promptları — Sprint Görevleri

Bu dosyada her sprint görevi için Claude Code'a verilebilecek hazır promptlar var. Her bölüm bir gün/iş paketine karşılık geliyor. Promptu olduğu gibi kopyala, Claude Code terminalinde başlat, çıktıyı PR olarak aç.

## Genel Kullanım

Repo kök dizinindeyken:

```bash
claude
> /init
> Read CLAUDE.md to understand project context.
> Read docs/00-PRD.md sections 1-3.
> Read docs/02-architecture.md.
> Read sprints/sprint-roadmap.md for context.
> When ready, I'll give you the task prompt.
```

Sonra aşağıdaki promptlardan ilgili olanını kopyala-yapıştır.

**Kural:** Her prompt'un başında "Read docs/XX.md" yönlendirmesi var — Claude Code o dokümanı önce okumadan task'a başlamasın. Kalite için kritik.

---

## Sprint 1

### S1.D1.T1 — Repo init ve monorepo setup (Aydoğan)

```
You are setting up a new monorepo for the Bluedev GrantWriter project.

Read these first:
- CLAUDE.md (full)
- docs/02-architecture.md sections 2 and 3

Tasks:
1. Initialize a monorepo using pnpm + Turbo:
   - Root package.json with pnpm workspaces and Turbo config
   - apps/web (Next.js 15 placeholder)
   - apps/api (Python project — create pyproject.toml with Poetry)
   - packages/shared-types (TypeScript types shared between web and api)
2. Add .gitignore covering Python (.venv, __pycache__, *.pyc), Node (node_modules, .next, .turbo), env files (.env*), and OS files
3. Add Makefile with targets: dev, test, lint, migrate, seed (use the spec in docs/10-deployment-devops.md section 3)
4. Add .editorconfig and .prettierrc for consistent formatting
5. Add README.md at repo root with quick start instructions
6. Commit each logical chunk separately (e.g., "chore: init monorepo", "chore: add Makefile")

Don't install dependencies yet — that's the next task. Just scaffold the structure.

Show me the directory tree when done.
```

### S1.D1.T2 — FastAPI hello world (Md)

```
You are setting up the FastAPI backend skeleton.

Read:
- CLAUDE.md
- docs/01-CLAUDE.md sections "Tech Stack" and "Coding Standards"
- docs/02-architecture.md section 4 (component details)

Tasks in apps/api:
1. Configure pyproject.toml with these dependencies:
   - fastapi, uvicorn[standard], pydantic[email], pydantic-settings
   - asyncpg, sqlalchemy[asyncio], alembic (later use Supabase CLI but keep alembic optional)
   - anthropic, openai, httpx
   - celery[redis], redis
   - pytest, pytest-asyncio, ruff, mypy (dev deps)
2. Create src/main.py with a basic FastAPI app, /health endpoint returning {"status": "ok", "version": "0.1.0"}
3. Create src/core/config.py with Pydantic Settings — read from .env, validate required vars
4. Create src/core/logging.py with structured JSON logging
5. Add Dockerfile for production (multi-stage, slim image)
6. Add tests/test_health.py — verify /health returns 200
7. Run `poetry install` then `poetry run pytest` and confirm green

Use strict mypy config and ruff with sensible defaults. Don't add database or auth yet.
```

### S1.D2.T1 — Database migrations (Aydoğan)

```
You are creating the initial database schema for Bluedev GrantWriter.

Read:
- docs/03-database-schema.md (full — this is your source of truth)

Tasks:
1. In infra/supabase/migrations/, create migration files using the naming convention YYYYMMDDHHMMSS_descriptive.sql
2. Create migrations 001 through 008 covering:
   - 001 extensions (uuid-ossp, pgcrypto, vector, pg_trgm, btree_gin)
   - 002 tenants and users tables
   - 003 programmes and seed data (5 programs)
   - 004 calls and call_chunks
   - 005 proposals, proposal_provenance
   - 006 citations, proposal_versions, proposal_comments
   - 007 successful_proposals_corpus, successful_proposal_chunks, funder_guidelines, cordis_funded_projects
   - 008 tenant_usage_log, billing_events, audit_log
3. Each migration should be idempotent where possible (CREATE IF NOT EXISTS)
4. After running them locally with `supabase db reset`, verify all tables exist with `\dt`
5. Don't add RLS yet — that's a separate migration

Output: list of migration files created and a quick summary of what each does.
```

### S1.D2.T2 — RLS policies + test suite (Aydoğan, CRITICAL)

```
You are adding Row Level Security to the database. This is security-critical.

Read:
- docs/03-database-schema.md sections 4 and 5 (full)
- docs/09-security-compliance.md section 6

Tasks:
1. Create migration 009_rls_policies.sql with the policies from docs/03-database-schema.md section 4
2. Create migration 010_helper_functions.sql with auth.tenant_id() and auth.is_tenant_admin()
3. Create infra/supabase/tests/rls_test.sql with the test cases from docs/03-database-schema.md section 5
4. Run the test suite locally: `psql $DATABASE_URL -f infra/supabase/tests/rls_test.sql`
5. The output must end with "RLS tests PASSED". If any test fails, DO NOT proceed — fix policies first.

Then add additional edge case tests in apps/api/tests/security/test_rls.py:
- Service role bypass works for Celery workers
- Role escalation (member → admin) takes effect immediately
- Soft-deleted users cannot access data
- JWT signature mismatch returns 401

Run pytest tests/security/ and confirm green.

CRITICAL: do not move on until both SQL and pytest tests are green. RLS leaks are
the most expensive bug class in this project.
```

### S1.D3.T1 — LLM Router (Aydoğan)

```
You are implementing the LLM Router — the central abstraction for all LLM calls.

Read:
- docs/01-CLAUDE.md section "Critical Architectural Decisions" — point #1 about LLM Router
- docs/02-architecture.md section 4.1 (LLM Router details)
- docs/06-agent-architecture.md section 7 (cost optimization, model routing)

Tasks in apps/api/src/llm/:
1. base.py — abstract LLMProvider class, LLMRequest, LLMResponse Pydantic models
2. claude_provider.py — wraps anthropic SDK; supports streaming, prompt caching (cache_control), retries with exponential backoff (max 3)
3. openai_provider.py — wraps openai SDK; same interface
4. router.py — task → model mapping (per docs/06-agent-architecture.md table); resolves BYOK keys via key_vault.py
5. cost_tracker.py — writes to tenant_usage_log table after each call
6. key_vault.py — pgcrypto encrypt/decrypt for tenant API keys
7. tests covering: routing, fallback, cost tracking, retry logic, cache hit detection

Important rules:
- Direct anthropic.Anthropic() or openai.OpenAI() calls in router.py only — nowhere else in codebase
- Every call increments tenant_usage_log
- BYOK keys never appear in logs (use redacted logging)

Do not implement specific agents yet. Just the router.

Run pytest tests/llm/ and confirm green.
```

### S1.D4.T1 — CallAnalyst agent (Aydoğan)

```
You are implementing the first AI agent: Call Analyst.

Read:
- docs/06-agent-architecture.md section 4.1 (full)
- docs/01-CLAUDE.md section "Common Patterns" → LLM Call Pattern

Tasks:
1. apps/api/src/agents/base.py — BaseAgent abstract class per docs/06-agent-architecture.md section 3
2. apps/api/src/agents/prompts/_shared/call_analyst/v1.md — system prompt from docs/06-agent-architecture.md section 4.1
3. apps/api/src/agents/call_analyst.py — concrete implementation
4. apps/api/src/agents/__init__.py — register
5. tests/agents/test_call_analyst.py with at least:
   - one TÜBİTAK 1501 sample call text fixture → assert structured output matches schema
   - one Horizon Europe sample call text fixture → assert page_limit=45 extracted
   - eligibility issue case → assert user_eligibility_issues populated
6. Use claude-opus-4-7 with temperature=0 (deterministic for parsing)

Place sample call text fixtures in tests/fixtures/calls/.

Run pytest tests/agents/test_call_analyst.py and verify outputs are structured correctly.
```

### S1.D4.T2 — ExcellenceWriter for TÜBİTAK (Aydoğan)

```
You are implementing the Excellence Writer agent specifically for TÜBİTAK 1501.

Read:
- docs/06-agent-architecture.md section 4.2
- docs/04-rag-strategy.md section 5 (anti-hallucination prompt rules)
- docs/07-program-modules.md section 4 (TÜBİTAK 1501 specifics)

Tasks:
1. apps/api/src/agents/excellence_writer.py — implements BaseAgent, accepts program_id and selects appropriate prompt
2. apps/api/src/agents/prompts/tubitak_1501/excellence_writer/v1.md — Turkish-language prompt mirroring the spec in docs/06-agent-architecture.md section 4.2 but adapted for TÜBİTAK 1501 (B1-B4 subsections)
3. RAG retrieval is OPTIONAL for v1 — pass empty context if corpus is empty
4. Stream output via Server-Sent Events (use the BaseAgent.stream method)
5. Extract citations with regex; store as raw text for now (Hallucination Hunter verifies later)
6. Test: feed a sample TÜBİTAK 1501 brief, assert that:
   - Output contains B1, B2, B3, B4 headings
   - B2 has at least 800 words (TÜBİTAK requirement)
   - Output is in Turkish

Don't implement Hallucination Hunter, Compliance Reviewer, or other writers in this task.
```

### S1.D5.T1 — TÜBİTAK 1501 DOCX export (Aydoğan)

```
You are implementing DOCX export for TÜBİTAK 1501 (AGY100 form).

Read:
- docs/07-program-modules.md section 4 (full)

Tasks:
1. Create apps/api/src/programs/tubitak_1501/templates/agy100_2026.docx — a DOCX template you generate from scratch using python-docx, with appropriate headings for B1-B4, C1-C3, D1-D4 sections, and a budget table placeholder
2. apps/api/src/programs/tubitak_1501/__init__.py — TUBITAK1501Module implementing BaseProgramModule
3. The export_docx method should:
   - Open the template
   - Render the markdown sections (B1-B4 from excellence_md, C1-C3 from impact_md, D1-D4 from implementation_md) using python-docx (write a custom markdown→docx converter for headings, paragraphs, bold, italic, lists)
   - Insert the budget table from proposal["budget"]["by_category"]
   - Return bytes
4. Add an HTTP endpoint POST /api/v1/proposals/{id}/export that creates a Celery task
5. The Celery task uploads the DOCX to Supabase Storage, returns a signed URL
6. Test: integration test that creates a proposal, calls export, downloads the file, opens it with python-docx, asserts it has all expected sections

Markdown→DOCX converter: handle # / ## / ### headings, paragraphs, bold (**), italic (*), bullet lists (- and *), numbered lists. Don't try to handle tables in v1; tables come from structured data.
```

---

## Sprint 2

### S2.D6.T1 — Horizon Europe program module (Aydoğan)

```
You are implementing the Horizon Europe RIA/IA program module.

Read:
- docs/07-program-modules.md section 3 (full — this is the spec)
- docs/06-agent-architecture.md sections 4.2, 4.3, 4.4

Tasks:
1. apps/api/src/programs/horizon_eu_ria/__init__.py — HorizonEURIAModule implementing BaseProgramModule
2. Use the brief schema from docs/07-program-modules.md section 3.3
3. Create the prompt files:
   - prompts/horizon_eu/excellence_writer/v1.md
   - prompts/horizon_eu/impact_writer/v1.md
   - prompts/horizon_eu/implementation_writer/v1.md
4. Adapt the Excellence Writer prompt template from docs/06-agent-architecture.md section 4.2 to be HE-specific (subsections 1.1, 1.2, 1.3, 1.4)
5. Create a basic DOCX template apps/api/src/programs/horizon_eu_ria/templates/ria_part_b_2026.docx that approximates the EC Standard Application Form Part B structure (3 sections, AI disclosure section bookmark)
6. Implement validate_draft per docs/07-program-modules.md section 3.6
7. Test: end-to-end test with a fixture HE brief, generate all 3 sections (mock LLM responses where needed for speed), assert output structure

Don't implement Lump Sum Excel or distinctiveness yet — those are next tasks.
```

### S2.D6.T2 — RAG infrastructure (Md)

```
You are building the RAG retrieval pipeline.

Read:
- docs/04-rag-strategy.md (full)

Tasks in apps/api/src/rag/:
1. embedder.py — wraps OpenAI text-embedding-3-large; batch support; rate limit aware
2. chunker.py — section-aware chunking (per docs/04-rag-strategy.md section 2.3); 800-1200 tokens, 200 token overlap, paragraph boundaries respected
3. retriever.py — pgvector HNSW similarity search + LLM re-ranking (per section 2.4 and 2.5)
4. corpus_manager.py — methods to add/update successful proposals, with chunking and embedding
5. seed script: scripts/seed_corpus.py — loads 10 sample HE successful proposals (use placeholder data — actual EC publications later)
6. tests/rag/test_retriever.py — verify top-k retrieval, similarity scores, re-ranking output

Run the seed script after creating it. Verify chunks appear in successful_proposal_chunks table and have non-null embeddings.
```

### S2.D7.T1 — Citation Verifier (Md)

```
You are implementing the citation verification pipeline. This is anti-hallucination critical.

Read:
- docs/04-rag-strategy.md section 3 (full)

Tasks in apps/api/src/citations/:
1. verifier.py — CitationVerifier class implementing the 3-stage flow per section 3.2 (DOI direct → Crossref → OpenAlex)
2. cache.py — Redis cache wrapper, 30-day TTL, key derived from citation hash
3. extractors.py — regex-based citation extraction from markdown (handles common formats: [Smith 2023], (Smith et al., 2023), numbered [1])
4. agent: hallucination_hunter.py per docs/06-agent-architecture.md section 4.6
5. API endpoint POST /api/v1/citations/{id}/verify — triggers verification synchronously (used by frontend on demand)
6. Batch verification Celery task for full proposals
7. tests/citations/:
   - test verified DOI returns "verified"
   - test invalid DOI returns "fabricated"
   - test fuzzy match score > 0.85 returns "verified"
   - test cache hits work
   - mock Crossref/OpenAlex responses for deterministic tests

Use rapidfuzz for fuzzy matching. Use httpx.AsyncClient for HTTP calls. Add User-Agent header per Crossref polite pool guidelines (mailto:support@bluedev.dev).
```

### S2.D8.T1 — Distinctiveness Scorer (Md)

```
You are implementing the distinctiveness scoring pipeline.

Read:
- docs/04-rag-strategy.md section 4 (full)

Tasks:
1. scripts/load_cordis.py — downloads CORDIS funded projects CSV, parses, embeds abstracts, bulk inserts to cordis_funded_projects table. Use OpenAI text-embedding-3-large. Limit to last 3 years (~12K projects). Run with concurrency=10.
2. apps/api/src/compliance/distinctiveness.py — DistinctivenessScorer per section 4.1
3. apps/api/src/agents/distinctiveness_scorer.py — wraps the scorer in BaseAgent interface
4. API endpoint GET /api/v1/proposals/{id}/distinctiveness
5. tests:
   - mock cordis data, assert score returned correctly
   - assert level "distinctive" / "warning" / "critical" mapping per thresholds
   - test edge case: no comparable projects → returns "unknown"

After running scripts/load_cordis.py, verify the table has ~12K rows and HNSW index works (query plan shows Index Scan).
```

### S2.D9.T1 — Compliance Reviewer agent (Aydoğan)

```
You are implementing the Compliance Reviewer agent.

Read:
- docs/06-agent-architecture.md section 4.5 (full)
- docs/09-security-compliance.md section 2 (AI disclosure)

Tasks:
1. apps/api/src/agents/compliance_reviewer.py — implements BaseAgent
2. apps/api/src/agents/prompts/_shared/compliance_reviewer/v1.md — uses Sonnet 4.6 for cost
3. Implement these checks (some rule-based, some LLM-based):
   - Page limits per section (rule-based, simple character/word count)
   - Required subsections (rule-based, regex)
   - Required key_terms_to_use coverage (rule-based)
   - DNSH presence (LLM check, only for HE)
   - Gender dimension (LLM check, only for HE)
   - Open Science / DMP mention (LLM check, only for HE)
4. apps/api/src/compliance/ai_disclosure.py — generate_ai_disclosure function per docs/09-security-compliance.md section 2.2
5. Output ValidationIssue objects (per docs/07-program-modules.md section 2)
6. tests/compliance/:
   - feed sample drafts with intentional violations, assert correct issues raised
   - feed clean draft, assert empty issues list
   - test AI disclosure generation with sample provenance data

Run pytest tests/compliance/ green.
```

---

## Sprint 3

### S3.D11.T1 — BYOK encryption (Aydoğan)

```
You are implementing BYOK (Bring Your Own Key) — secure storage of customer API keys.

Read:
- docs/09-security-compliance.md section 5 (full)
- docs/01-CLAUDE.md section "Critical Architectural Decisions" → BYOK rules

Tasks:
1. Migration 011_byok_encryption.sql — adds pgp_sym_encrypt/decrypt functions, ensures pgcrypto extension, sets up master key reference (env var path, not stored in DB)
2. apps/api/src/llm/key_vault.py — encrypt_key, decrypt_key methods using pgcrypto via raw SQL
3. apps/api/src/api/routes/llm_config.py:
   - GET /api/v1/tenant/llm-config — returns booleans for set/not-set, never the keys
   - PUT /api/v1/tenant/llm-config — accepts plaintext keys, encrypts server-side
   - POST /api/v1/tenant/llm-config/test — runs a minimal Claude Sonnet test call (5 tokens), returns valid/invalid
4. Update LLMRouter to resolve BYOK keys via key_vault for non-managed plans
5. Add audit_log entries for key changes (key value NEVER logged)
6. tests/security/test_byok.py:
   - encrypt → decrypt round trip
   - test endpoint with valid key returns 200, invalid returns 401
   - assert keys never appear in any log output (use caplog fixture)

Master key (LLM_MASTER_ENCRYPTION_KEY) is a 32-byte random string in .env, set in Railway secrets only. Document rotation procedure in a comment.
```

### S3.D12.T1 — Stripe integration (Aydoğan)

```
You are implementing Stripe subscriptions and webhook handling.

Read:
- docs/05-api-contracts.md sections 3.11
- docs/00-PRD.md section 7 (pricing)

Tasks:
1. apps/api/src/billing/stripe_client.py — Stripe SDK wrapper
2. apps/api/src/api/routes/billing.py:
   - POST /api/v1/billing/checkout — creates Checkout Session, returns URL
   - POST /api/v1/billing/portal — creates Customer Portal session
   - POST /webhooks/stripe — verifies signature, handles events:
     - checkout.session.completed → upgrade tenant plan
     - customer.subscription.deleted → downgrade to free
     - invoice.paid → record billing_event
     - invoice.payment_failed → notify user
3. apps/api/src/services/billing_service.py — business logic for plan changes
4. Add Stripe price IDs to env config (one per plan: starter, pro, agency)
5. Update tenant.monthly_proposal_limit when plan changes
6. tests/billing/:
   - mock Stripe client, test happy path checkout → plan change
   - test webhook signature validation
   - test failed payment → email triggered (mock Resend)

Run Stripe CLI in dev: `stripe listen --forward-to localhost:8000/webhooks/stripe`. Test with Stripe test cards. Verify db state changes appropriately.
```

### S3.D13.T1 — Hallucination Hunter complete pass (Md)

```
You are completing the Hallucination Hunter agent — the final quality gate.

Read:
- docs/04-rag-strategy.md sections 3 and 6
- docs/06-agent-architecture.md section 4.6

Tasks:
1. Extend apps/api/src/agents/hallucination_hunter.py to:
   - Run citation verification on ALL citations in proposal (parallel batch)
   - Sample 10 random claims with citations and verify them via LLM (does the claim actually appear in the verified source?)
   - Generate HuntReport with recommendations (block_export / ok)
2. Update orchestrator to run Hallucination Hunter as final step
3. Update validate_proposal endpoint: if HuntReport.recommendation == "block_export", validation fails
4. Frontend integration: red badge + blocked export button when fabricated citations exist
5. tests:
   - feed sample with 1 fabricated DOI → assert blocked
   - feed sample with all verified → assert ok
   - claim verification edge case: paraphrased claim → assert handled gracefully

Run integration test with real Crossref API (mark as @pytest.mark.integration so it's skipped in fast CI).
```

---

## Sprint 4

### S4.D16.T1 — Production deploy setup (Aydoğan)

```
You are setting up the production deployment pipeline.

Read:
- docs/10-deployment-devops.md (full)

Tasks:
1. Create .github/workflows/test.yml per section 4 of docs/10
2. Create .github/workflows/deploy.yml per section 4 of docs/10
3. Configure Railway services: api (FastAPI), worker (Celery), scheduler (Celery beat) with appropriate resource limits
4. Configure Vercel project for apps/web with environment variables
5. Set up DNS via Cloudflare:
   - bluedev.dev → Vercel
   - app.bluedev.dev → Vercel
   - api.bluedev.dev → Railway
6. Configure secrets in each service (NEVER commit to git):
   - Use the env var list from docs/10-deployment-devops.md section 2
7. Run a deployment to staging, verify all services healthy via /health endpoints
8. Run smoke tests against staging
9. Document rollback procedure in docs/10-deployment-devops.md section 11 (verify it's complete)

Do NOT deploy to production yet — that's Day 17 after pilot 1 onboarding.
```

### S4.D17.T1 — Pilot onboarding flow (Sn)

```
You are creating the pilot user onboarding experience.

Read:
- docs/00-PRD.md section 3 (personas)
- docs/08-frontend-spec.md section 3.6

Tasks in apps/web:
1. Create /onboarding page with a 2-step wizard:
   Step 1: Welcome + key features explainer (with screenshots)
   Step 2: First-action prompt — "Browse open calls" or "Create your first proposal"
2. Add an onboarding_completed_at field to users table (migration 012)
3. Middleware redirects new users (onboarding_completed_at IS NULL) to /onboarding on first dashboard visit
4. Add "Skip onboarding" link
5. Track PostHog events: onboarding_started, onboarding_step_completed, onboarding_finished, onboarding_skipped
6. Create help docs in apps/web/content/help/*.mdx covering:
   - "How to write your first brief"
   - "Understanding citation verification"
   - "Setting up BYOK"
   - "Submitting your proposal"
7. Display help docs at /help via MDX rendering

Test: complete onboarding as a new user, verify PostHog events fire and middleware no longer redirects.
```

### S4.D20.T1 — Final smoke test ve release (Aydoğan)

```
You are doing the final pre-release smoke test and tagging v1.0.0.

Read:
- docs/10-deployment-devops.md section 12 (production checklist)

Tasks:
1. Run through every checkbox in section 12. Anything not green → don't release, fix first.
2. Run pen test (use OWASP ZAP baseline scan against staging if no external tester):
   `docker run -t owasp/zap2docker-stable zap-baseline.py -t https://staging.bluedev.dev -r zap_report.html`
3. Manual user journey test on production:
   - Sign up new user
   - Complete onboarding
   - Browse calls, select one
   - Fill brief
   - Generate draft
   - Verify citations
   - Export DOCX
   - Subscribe to Pro plan with test card
4. Verify Sentry, PostHog, Logtail all receiving events
5. Tag v1.0.0:
   `git tag -a v1.0.0 -m "Bluedev GrantWriter v1.0.0 — MVP Release"`
   `git push origin v1.0.0`
6. Create CHANGELOG.md entry
7. Send "MVP Live" email to pilot customers (template in docs)

If any failure on production smoke: rollback per docs/10 section 11, debug, retry.

This is the moment. Take your time, do it carefully.
```

---

## Genel Promptlama Stratejisi (CTO Notu)

Promptlar yazarken üç prensip uyguladık:

1. **Doğru dokümantasyon önce okutuluyor.** Claude Code'un task'a başlamadan önce `docs/`'tan ilgili bölümleri okuması zorunlu. Bu, task description'ın sömürülmesini engelliyor — Claude Code spec'i karıştırıp yanlış yöne gitmiyor.

2. **Çıktı kriterleri açık.** Her prompt sonunda "test green olmalı", "endpoint X dönecek", "PR ready" gibi somut başarı kriterleri var. Bu, Claude Code'un "tamamlandı" demesini doğrulanabilir hale getiriyor.

3. **Scope kapsayıcı değil sınırlı.** Her prompt tek bir mantıksal birim. "X yap, ama Y'ye dokunma" tarzı sınırlar var. Bu, küçük PR'lar üretmesini sağlıyor — review kolaylaşıyor.

Yeni promptlar yazarken aynı şablonu izleyin:

```
You are [task scope].

Read: [docs to read first]

Tasks:
1. [specific deliverable]
2. [specific deliverable]
...

[Constraints — what NOT to do]

[Verification — how do we know it's done]
```

Promptlardan birini kullandığınızda Claude Code'un çıktısı beklenen kalitede değilse:
- Önce docs eksikliği olabilir mi kontrol et
- Sonra prompt belirsiz olabilir mi kontrol et
- Son olarak Claude Code'a daha spesifik istek gönder

Claude Code'un model versiyonu önemli. Bu projede **Claude Opus 4.7** kullanılması zorunlu (claude-opus-4-7 model string). Sonnet ile çalışırken plan/Apply döngüsünde kayıp yaşanıyor.

---

**Sprint dışı promptlar (örnek genişletmeler):**

### Bug fix template

```
You are fixing a bug in [area].

Bug description:
[paste from issue]

Reproduction steps:
[paste]

Read: [relevant docs]

Tasks:
1. Reproduce locally — confirm bug exists in current main
2. Write a failing test that captures the bug
3. Fix the bug — make the test pass
4. Verify no regression — run full test suite
5. Update CHANGELOG.md

Show me the test and fix as separate commits.
```

### Refactor template

```
You are refactoring [module].

Current state: [describe code smell]
Desired state: [describe target architecture]

Read: docs/[relevant section]

Tasks:
1. Confirm refactor doesn't break existing behavior — read tests for this module
2. Make the refactor in small atomic commits
3. Don't add new features — pure refactor
4. All existing tests must still pass
5. Update docs if interface changes

Show me commit history when done.
```

---

Bu promptlar yaşayan dosya — sprint ilerledikçe yeni promptlar eklenecek. Pull request açılırken kullanılan prompt commit message'a referans olarak konacak (örn. "Implements S2.D6.T1").