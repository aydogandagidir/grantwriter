# CLAUDE.md — Bluedev GrantWriter

> Bu dosya proje köküne kopyalanmalı. Claude Code her oturumda bu dosyayı otomatik okur.

## Proje Tanımı

Bluedev GrantWriter, AB ve Türkiye hibe programlarına başvuran KOBİ'ler için AI-destekli, compliance-onaylı, iki dilli (TR/EN) hibe yazımı SaaS'ıdır. 5 program desteklenir: TÜBİTAK 1501, TÜBİTAK 1507, KOSGEB AR-GE, Horizon Europe RIA/IA, Cascade Funding + NLnet.

**Geliştirme sürecinin temel ilkesi:** Production-grade kod yaz, prototip değil. Her commit deploy edilebilir olmalı.

---

## Tech Stack (DEĞİŞTİRMEYİN)

### Frontend
- **Framework:** Next.js 15 (App Router)
- **Language:** TypeScript (strict mode)
- **Styling:** Tailwind CSS + shadcn/ui
- **State:** TanStack Query (server state) + Zustand (client state)
- **Editor:** TipTap (markdown editor)
- **Forms:** React Hook Form + Zod validation
- **i18n:** next-intl (TR/EN)

### Backend
- **Framework:** FastAPI (Python 3.11+)
- **LLM SDK:** `anthropic` (primary), `openai` (fallback)
- **Agent Framework:** Custom (NOT ag2/AutoGen — too heavy for our needs)
- **RAG:** LangChain + pgvector
- **Background jobs:** Celery + Redis
- **Document generation:** `python-docx` (DOCX), `openpyxl` (XLSX), `weasyprint` (PDF)
- **HTTP client:** `httpx` (async)
- **Validation:** Pydantic v2

### Database
- **Primary:** PostgreSQL 16 + pgvector
- **Cache/Queue:** Redis 7
- **Auth:** Supabase Auth (PostgreSQL RLS)

### Infrastructure
- **Frontend hosting:** Vercel
- **Backend hosting:** Railway (FastAPI + Celery worker)
- **Database:** Supabase (PostgreSQL + pgvector + Auth)
- **Storage:** Supabase Storage (DOCX, PDF outputs)
- **Monitoring:** Sentry (errors) + PostHog (product analytics) + Logtail (logs)
- **CI/CD:** GitHub Actions

### External APIs
- **LLM:** Anthropic Claude (Opus 4.7 primary, Sonnet 4.6 secondary)
- **Citations:** Crossref API + OpenAlex API
- **Calls:** EU Funding & Tenders Portal API + NLnet RSS + custom scrapers
- **Payments:** Stripe (EU) + Iyzico (TR)
- **Embeddings:** OpenAI `text-embedding-3-large` (3072 dim, multilingual)

---

## Repository Structure

```
bluedev-grantwriter/
├── apps/
│   ├── web/                    # Next.js 15 frontend
│   │   ├── src/
│   │   │   ├── app/            # App Router pages
│   │   │   ├── components/     # Shared components
│   │   │   ├── lib/            # Utilities, API client
│   │   │   └── i18n/           # TR/EN translations
│   │   ├── public/
│   │   ├── package.json
│   │   └── tsconfig.json
│   │
│   └── api/                    # FastAPI backend
│       ├── src/
│       │   ├── api/            # FastAPI routes
│       │   ├── agents/         # 7 AI agents
│       │   ├── llm/            # LLM provider abstraction
│       │   ├── programs/       # 5 program modules (plugin)
│       │   ├── rag/            # RAG corpus + retrieval
│       │   ├── citations/      # Crossref/OpenAlex grounding
│       │   ├── exports/        # DOCX/PDF/XLSX generators
│       │   ├── scrapers/       # Call discovery
│       │   ├── compliance/     # AI disclosure, distinctiveness
│       │   ├── billing/        # Stripe + Iyzico
│       │   └── core/           # config, db, auth, logging
│       ├── tests/
│       ├── pyproject.toml
│       └── Dockerfile
│
├── packages/
│   ├── shared-types/           # TypeScript types shared between web & api
│   └── ui-components/          # shadcn/ui-based components
│
├── infra/
│   ├── supabase/               # Migrations, RLS policies
│   ├── docker-compose.yml      # Local dev
│   └── render.yaml             # Production deploy (Render Blueprint)
│
├── docs/                       # All architecture docs (this package)
├── scripts/                    # Helper scripts (seed data, etc.)
├── .github/workflows/          # CI/CD
├── CLAUDE.md                   # This file
└── README.md
```

---

## Coding Standards

### Python (api/)
- **Formatter:** `ruff format` (replaces black)
- **Linter:** `ruff check`
- **Type checking:** `mypy --strict`
- **Test runner:** `pytest` + `pytest-asyncio`
- **Convention:** PEP 8, snake_case, type hints zorunlu
- **Imports:** absolute imports (`from src.agents import ...`)
- **Pydantic:** ALL inputs/outputs validated, no `dict[str, Any]` in public APIs

### TypeScript (web/)
- **Formatter:** Prettier
- **Linter:** ESLint + `@typescript-eslint`
- **Type checking:** `tsc --noEmit` in CI
- **Convention:** camelCase variables, PascalCase components, kebab-case files
- **Imports:** `@/` alias for `src/`
- **Component pattern:** functional components only, hooks for state

### Git
- **Branch naming:** `feature/<ticket>-<short-desc>`, `fix/<short-desc>`, `chore/<short-desc>`
- **Commit format:** Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`)
- **PR template:** description, screenshots (if UI), checklist (tests, types, docs)
- **Code review:** ≥1 approval, no self-merge for main

### General
- **NO TODO comments without ticket reference** — `# TODO(BD-123): description`
- **NO console.log / print in production code** — use structured logger
- **NO hardcoded secrets** — `.env` only, Pydantic Settings
- **NO global mutable state** — dependency injection via FastAPI Depends

---

## Critical Architectural Decisions (DON'T VIOLATE)

### 1. LLM Provider Abstraction
- **All LLM calls go through `src/llm/router.py`** (Python) or `lib/llm.ts` (TS).
- Direct `anthropic.messages.create()` calls in business logic are forbidden.
- **Reason:** BYOK support, cost tracking, fallback logic.

### 2. Citation Grounding is Mandatory
- **Every citation in any agent output MUST be verified** via Crossref or OpenAlex before being shown to user.
- Unverified citations are flagged in red and **block submission**.
- **Reason:** Hallucination rates 14-95%, hibe başvurusunda fabricated citation = ineligibility.

### 3. Provenance Tracking is Mandatory
- **Every sentence in editor has provenance metadata**: `human` / `ai-generated` / `ai-edited` / `imported`.
- This metadata is used to auto-fill HE AI disclosure (page 32).
- **Reason:** EU AI Act + Horizon Europe compliance.

### 4. Multi-Tenant via RLS, Not Application-Level Filtering
- **All tenant-scoped queries use Supabase RLS policies** with `auth.uid()`.
- No `WHERE tenant_id = ?` in application code.
- **Reason:** Defense in depth, prevents cross-tenant leaks via SQL injection.

### 5. Long-Running Jobs Use Celery, Not FastAPI BackgroundTasks
- **Any task >5 seconds uses Celery + Redis queue.**
- FastAPI endpoints return job ID, client polls or subscribes via SSE.
- **Reason:** Production reliability, retries, monitoring.

### 6. Program Modules are Plugins
- **Each program (TÜBİTAK 1501, KOSGEB, HE, etc.) implements `BaseProgramModule` interface.**
- Adding a new program = adding a new file in `programs/`, no core changes.
- **Reason:** Extensibility for Faz 2 (Eurostars, MSCA, etc.).

### 7. Prompts are Versioned
- **All agent prompts live in `agents/prompts/{program}/{agent}/{version}.md`.**
- Production version pinned in config; A/B testing supported.
- **Reason:** Reproducibility, evaluation, regression testing.

### 8. Costs are Tracked Per Tenant Per Request
- **Every LLM call increments `tenant_usage_log` table.**
- Stripe metered billing reads from this table.
- **Reason:** Profitability, budget alerts, BYOK validation.

---

## Common Patterns

### LLM Call Pattern
```python
# CORRECT
from src.llm.router import LLMRouter

async def write_excellence_section(brief: ProjectBrief) -> str:
    router = LLMRouter.for_task("excellence_draft", tenant_id=brief.tenant_id)
    response = await router.complete(
        system=load_prompt("horizon_eu/excellence_writer/v1"),
        messages=[{"role": "user", "content": brief.to_xml()}],
        cache_system=True,  # prompt caching
    )
    return response.text

# WRONG — direct SDK call
import anthropic
client = anthropic.Anthropic()  # NEVER DO THIS IN BUSINESS LOGIC
```

### Citation Verification Pattern
```python
# CORRECT
from src.citations.verifier import verify_citation

async def add_reference(draft_id: UUID, citation: Citation) -> CitationResult:
    result = await verify_citation(citation)  # Crossref + OpenAlex
    if result.status == "verified":
        await save_citation(draft_id, citation, verified=True)
    else:
        await save_citation(draft_id, citation, verified=False, flag="unverified")
    return result
```

### Multi-Tenant Query Pattern
```python
# CORRECT — RLS handles tenant isolation
async def list_proposals(user: User) -> list[Proposal]:
    # Supabase client automatically applies RLS via auth.uid()
    return await supabase.table("proposals").select("*").execute()

# WRONG — application-level filter (vulnerable)
async def list_proposals(user: User, tenant_id: UUID) -> list[Proposal]:
    return await db.fetch_all("SELECT * FROM proposals WHERE tenant_id = $1", tenant_id)
```

### Provenance Tracking Pattern
```typescript
// Frontend: every editor action sets provenance
const insertAIContent = (text: string, agent: string) => {
  editor.commands.insertContent({
    type: 'paragraph',
    attrs: { provenance: 'ai-generated', agent, timestamp: Date.now() },
    content: [{ type: 'text', text }]
  });
};
```

---

## Key Files to Read First

When starting work, read in this order:
1. `docs/00-PRD.md` — what we're building and why
2. `docs/02-architecture.md` — system design
3. `docs/03-database-schema.md` — data model
4. `docs/06-agent-architecture.md` — AI agents
5. `docs/07-program-modules.md` — program plugin system
6. `sprints/sprint-roadmap.md` — what to do today

---

## Common Tasks

### Add a New Agent
1. Create `apps/api/src/agents/{agent_name}.py` implementing `BaseAgent`
2. Create `apps/api/src/agents/prompts/{program}/{agent_name}/v1.md`
3. Register in `apps/api/src/agents/__init__.py`
4. Add to orchestration flow in `apps/api/src/orchestrator/flows.py`
5. Write tests in `apps/api/tests/agents/test_{agent_name}.py`

### Add a New Program
1. Create `apps/api/src/programs/{program_id}/` directory
2. Implement `BaseProgramModule` (call_parser, brief_form, template, validators)
3. Add DOCX template to `apps/api/src/programs/{program_id}/templates/`
4. Register in `apps/api/src/programs/__init__.py`
5. Add UI form to `apps/web/src/components/brief-forms/{program_id}.tsx`
6. Add e2e test in `apps/api/tests/programs/test_{program_id}.py`

### Run Locally
```bash
# Database + Redis
docker compose up -d

# Backend
cd apps/api && poetry install && poetry run uvicorn src.main:app --reload

# Frontend
cd apps/web && pnpm install && pnpm dev

# Worker
cd apps/api && poetry run celery -A src.worker worker --loglevel=info
```

---

## What NOT to Do

- ❌ Do NOT use ag2/AutoGen — we have a custom orchestrator (lighter, more controllable)
- ❌ Do NOT call LLM APIs directly — use `LLMRouter`
- ❌ Do NOT add new dependencies without discussion (PR comment from senior dev)
- ❌ Do NOT commit secrets, ever
- ❌ Do NOT write SQL queries that bypass RLS
- ❌ Do NOT skip citation verification — fabricated citations are a critical bug
- ❌ Do NOT hardcode program-specific logic outside `programs/` directory
- ❌ Do NOT use `any` in TypeScript or `Any` in Python type hints
- ❌ Do NOT push directly to main — always PR

---

## Communication

- **Architecture questions:** Read docs first, then ask in #engineering Slack
- **Bug reports:** GitHub Issues with reproduction steps
- **PRs:** Tag at least 1 reviewer; CI must pass before merge
- **Blockers:** Slack #blockers, 2-hour SLA

---

## Performance Targets

| Operation | p95 latency | Notes |
|---|---|---|
| Page load (web) | <1.5s | Vercel edge |
| API request (sync) | <500ms | Excluding LLM calls |
| LLM agent (1 section) | <30s | Excellence/Impact/Implementation |
| Full draft generation | <60min | Background job, e-mail when done |
| Citation verification | <2s per citation | Crossref + OpenAlex |
| DOCX export | <10s | python-docx |

---

## Cost Targets

| Metric | Target |
|---|---|
| Cost per draft (TÜBİTAK 1501) | <$3 (with prompt caching) |
| Cost per draft (HE RIA/IA) | <$15 (longer context) |
| Cost per citation verification | <$0.01 |
| Monthly infra (100 active users, 500 drafts) | <$500 |

---

## Security Reminders

- **API keys are encrypted at rest** in Supabase Vault (BYOK feature)
- **All API endpoints require auth** except `/health`, `/api/v1/calls/public`
- **Rate limiting:** 10 LLM calls/minute per user, 100/day per Starter plan
- **Audit log:** all `proposals.update()`, `citations.update()`, `compliance.*` events
- **PII:** project briefs may contain PII; never log full briefs

---

**Last updated:** 2026-05-07. Update this file when architectural decisions change.