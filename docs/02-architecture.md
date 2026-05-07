# 02 — Sistem Mimarisi

## 1. Yüksek Seviye Mimari

```
┌─────────────────────────────────────────────────────────────────────┐
│                         KULLANICI (Web Tarayıcı)                     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    NEXT.JS 15 FRONTEND (Vercel)                      │
│  - App Router, Server Components, TypeScript                         │
│  - shadcn/ui + Tailwind, TipTap editor                               │
│  - Supabase Auth (cookie-based session)                              │
└─────────────┬─────────────────────────────────────┬─────────────────┘
              │ REST + SSE                          │ Direct (Supabase JS SDK)
              ▼                                     ▼
┌────────────────────────────────┐    ┌──────────────────────────────┐
│   FASTAPI BACKEND (Railway)    │    │   SUPABASE (PostgreSQL)      │
│  ┌──────────────────────────┐  │    │  - Auth (users, sessions)    │
│  │ API Layer                │  │    │  - Database (RLS enforced)   │
│  │  /api/v1/proposals       │  │    │  - Storage (DOCX/PDF files)  │
│  │  /api/v1/calls           │  │    │  - Realtime (live updates)   │
│  │  /api/v1/citations       │  │    └──────────────┬───────────────┘
│  │  /api/v1/exports         │  │                   │
│  │  /api/v1/billing         │  │                   │
│  └────────────┬─────────────┘  │                   │
│               │                │    ┌──────────────▼───────────────┐
│  ┌────────────▼─────────────┐  │    │   PGVECTOR (RAG store)       │
│  │ Service Layer            │  │    │  - proposal_chunks           │
│  │  - Orchestrator          │◄─┼────┤  - call_chunks               │
│  │  - 7 AI Agents           │  │    │  - successful_proposals      │
│  │  - Program Modules       │  │    │  HNSW index, cosine sim      │
│  │  - Citation Verifier     │  │    └──────────────────────────────┘
│  │  - Compliance Engine     │  │
│  │  - Distinctiveness Scorer│  │    ┌──────────────────────────────┐
│  └────────────┬─────────────┘  │    │   REDIS (Cache + Queue)      │
│               │                │◄───┤  - Celery broker             │
│  ┌────────────▼─────────────┐  │    │  - Session cache             │
│  │ LLM Router               │  │    │  - Rate limiting (sliding)   │
│  │  - Claude (Opus/Sonnet)  │  │    │  - Citation cache (TTL 30d)  │
│  │  - OpenAI (fallback)     │  │    └──────────────────────────────┘
│  │  - BYOK key resolution   │  │
│  │  - Cost tracking         │  │
│  └────────────┬─────────────┘  │
└───────────────┼────────────────┘
                │
       ┌────────┼────────┬───────────────┐
       ▼        ▼        ▼               ▼
┌──────────┐ ┌──────┐ ┌──────────┐ ┌──────────────┐
│Anthropic │ │OpenAI│ │ Crossref │ │  EU F&T API  │
│   API    │ │ API  │ │/OpenAlex │ │ NLnet RSS    │
│          │ │      │ │   API    │ │ Cascade Scrap│
└──────────┘ └──────┘ └──────────┘ └──────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                  CELERY WORKERS (Railway, separate)                  │
│  - Long-running agent tasks (60min jobs)                             │
│  - Nightly call scraping                                             │
│  - DOCX/PDF/XLSX generation                                          │
│  - Citation batch verification                                       │
│  - Distinctiveness scoring (CORDIS comparison)                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         OBSERVABILITY                                │
│  Sentry (errors)  +  PostHog (analytics)  +  Logtail (logs)         │
│  Better Stack uptime monitoring (5 min interval)                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Servis Listesi (Production)

| Servis | Teknoloji | Hosting | Sorumluluk |
|---|---|---|---|
| `web` | Next.js 15 | Vercel (Hobby → Pro) | Frontend SSR/CSR |
| `api` | FastAPI + uvicorn | Railway (1 vCPU, 2GB) | REST + SSE |
| `worker` | Celery + Python | Railway (1 vCPU, 2GB) | Background jobs |
| `scheduler` | Celery beat | Railway (0.5 vCPU, 512MB) | Cron tasks |
| `postgres` | PostgreSQL 16 + pgvector | Supabase Pro | Primary DB + RAG |
| `redis` | Redis 7 | Railway addon (256MB) | Cache + queue |
| `storage` | Supabase Storage | Supabase | Generated files |

**Tahmini aylık altyapı maliyeti (100 aktif kullanıcı):** $250-400

---

## 3. Data Flow — "Yeni Başvuru Oluştur" Senaryosu

```
1. Kullanıcı /calls/[id] sayfasında "Bu çağrıya başvur" butonuna tıklar
   └─> Web: POST /api/v1/proposals (call_id, program_id)
       └─> API: proposals tablosuna kayıt (status='draft')
           └─> Web: redirect /proposals/[new_id]/brief

2. Kullanıcı brief formunu doldurur (program-spesifik)
   └─> Web: PATCH /api/v1/proposals/[id] (brief: {...})
       └─> API: brief field'ı update
           └─> Web: "Generate Draft" butonu aktif

3. Kullanıcı "Generate Draft" tıklar
   └─> Web: POST /api/v1/proposals/[id]/generate
       └─> API: Celery task enqueue (generate_draft_task)
           └─> API: SSE endpoint açar /api/v1/proposals/[id]/stream
               └─> Web: EventSource bağlantısı kurar

4. Celery worker generate_draft_task'ı işler:
   a. CallAnalyst agent çağrılır
      └─> LLM Router → Claude Opus 4.7 (call text + brief input)
      └─> Result: {eligibility, scope, sections_required, ...}
      └─> SSE event: {agent: "call_analyst", status: "completed", output_preview}

   b. ExcellenceWriter agent çağrılır
      └─> RAG: pgvector similarity search (top 5 successful proposals)
      └─> LLM Router → Claude Opus 4.7 (brief + RAG context + system prompt)
      └─> Citation extraction (regex)
      └─> Each citation → CitationVerifier (Crossref + OpenAlex)
      └─> Save draft.excellence_md + bibliography_jsonb
      └─> SSE event: {agent: "excellence_writer", status: "completed"}

   c. ImpactWriter agent (parallel after Excellence done)
   d. ImplementationWriter agent (after Impact)
   e. ComplianceReviewer agent (final pass)
      └─> AI disclosure auto-generation
      └─> DNSH check
      └─> Page limit check
   f. HallucinationHunter agent
      └─> Re-verify all citations
      └─> Cross-check claims vs RAG sources
   g. DistinctivenessScorer
      └─> Compare draft to CORDIS funded projects (last 3 years)
      └─> Cosine similarity score

5. Final state: proposal.status = 'draft_complete'
   └─> Email notification via Resend
   └─> SSE event: {status: "completed", proposal_url}
   └─> Web: redirect to editor
```

**Tipik süre:** 25-45 dakika (5 program ortalaması, HE en uzun, NLnet en kısa)
**Tipik maliyet:** $2-15 LLM (Bluedev managed) veya $0 (BYOK)

---

## 4. Komponent Detayları

### 4.1 LLM Router (`apps/api/src/llm/`)
- **Sorumluluk:** Tüm LLM çağrılarını tek noktadan yönetir
- **Özellikler:**
  - Provider abstraction (Claude, OpenAI)
  - Model routing (task → model mapping)
  - Prompt caching (Claude system prompt blocks)
  - BYOK key resolution (tenant config → API key)
  - Cost tracking (per request → `tenant_usage_log`)
  - Retry logic (exponential backoff, max 3 attempts)
  - Fallback (Claude error → OpenAI)
- **Dosyalar:**
  - `base.py` — `LLMProvider` ABC, `LLMRequest`, `LLMResponse`
  - `claude_provider.py` — Anthropic SDK wrapper
  - `openai_provider.py` — OpenAI SDK wrapper
  - `router.py` — Task routing logic
  - `cost_tracker.py` — Usage logging
  - `key_vault.py` — BYOK key encryption/decryption

### 4.2 Agent Orchestrator (`apps/api/src/orchestrator/`)
- **Sorumluluk:** 7 agent'ı doğru sırada çalıştırır
- **Özellikler:**
  - Sequential + parallel agent execution
  - State management (Redis)
  - SSE event publishing
  - Error recovery (agent fails → retry or skip)
- **Pattern:** Saga (compensating transactions on failure)

### 4.3 Program Modules (`apps/api/src/programs/`)
- **Sorumluluk:** Her hibe programının özel mantığı
- **Interface:**
  ```python
  class BaseProgramModule(ABC):
      program_id: str
      language: Literal["tr", "en"]

      @abstractmethod
      def parse_call(self, call_text: str) -> CallMetadata: ...

      @abstractmethod
      def get_brief_schema(self) -> BriefSchema: ...

      @abstractmethod
      def get_template(self) -> ProposalTemplate: ...

      @abstractmethod
      def validate_draft(self, draft: Draft) -> list[ValidationIssue]: ...

      @abstractmethod
      def export_docx(self, draft: Draft) -> bytes: ...
  ```

### 4.4 RAG Subsystem (`apps/api/src/rag/`)
- **Sorumluluk:** Embed + retrieve from corpus
- **Components:**
  - `embedder.py` — OpenAI text-embedding-3-large wrapper
  - `chunker.py` — Section-aware chunking (preserves semantic boundaries)
  - `retriever.py` — pgvector similarity search + re-ranking
  - `corpus_manager.py` — Add/update successful proposals
- **Corpus types:**
  - `successful_proposals` — anonymized winning proposals (seed: 50+ HE samples from EC publications)
  - `call_texts` — current and historical call documents
  - `funder_guidelines` — work programmes, evaluation guides

### 4.5 Citation Subsystem (`apps/api/src/citations/`)
- **Sorumluluk:** Halüsinasyon prevention via grounding
- **Flow:**
  ```
  Citation → DOI present? 
    ├─ YES → doi.org HEAD request → ✓
    └─ NO  → Crossref API (title + authors fuzzy match)
              ├─ Match >0.85 → ✓ (extract DOI)
              └─ No match    → OpenAlex API
                                ├─ Match >0.85 → ✓
                                └─ No match    → ✗ FLAG (unverified)
  ```
- **Cache:** Redis 30-day TTL (DOI → metadata)
- **Batch verification:** Celery task, parallel httpx requests

### 4.6 Compliance Engine (`apps/api/src/compliance/`)
- **AI Disclosure Generator:**
  - Reads `provenance` metadata from all draft sentences
  - Generates HE Standard Application Form page 32 text
  - Lists tools used, sources, limitations
- **DNSH Checker:** rule-based + LLM hybrid
- **Page Limit Validator:** counts characters/words per section
- **Distinctiveness Scorer:**
  - Embed user's draft Excellence section
  - Cosine similarity vs CORDIS funded projects (last 3 years, same call topic)
  - Score: <0.85 = distinctive, 0.85-0.92 = warning, >0.92 = problematic

### 4.7 Scrapers (`apps/api/src/scrapers/`)
- **EU F&T Portal:** Official REST API (`api.tech.ec.europa.eu/search/`)
  - Daily sync
  - Topic ID, deadline, budget, eligibility extraction
- **NLnet:** RSS + HTML scraping
- **Cascade Funding:** Custom scrapers per FSTP project portal (10+ scripts)
- **TÜBİTAK:** RSS + HTML (no API)
- **KOSGEB:** Manual curation (no API, no RSS)

---

## 5. State Machine (Proposal Lifecycle)

```
draft (created, brief incomplete)
   │ user fills brief
   ▼
brief_complete
   │ user clicks "generate"
   ▼
generating (Celery task running)
   │ all agents complete
   ▼
draft_complete
   │ user edits in editor
   ▼
in_review (user-triggered "ready for review")
   │ user clicks "validate compliance"
   ▼
validated (compliance passed, citations verified)
   │ user exports DOCX
   ▼
exported
   │ (out of system) user submits to portal
   │ user marks as submitted
   ▼
submitted
   │ user updates result
   ▼
funded | rejected
```

State transitions enforced by `apps/api/src/services/proposal_service.py::transition()`.

---

## 6. Performance & Scaling

### 6.1 Bottlenecks
1. **LLM latency:** 30s/agent × 7 agents = 3.5 dk minimum sequentially
   - Mitigation: parallel execution where possible (Excellence | Impact independent of each other after Call Analyst)
2. **Citation verification:** ~1s/citation × 50 citations = 50s
   - Mitigation: parallel httpx + Redis cache
3. **CORDIS comparison:** download all funded projects in topic = ~5 MB
   - Mitigation: pre-computed embeddings stored in pgvector
4. **DOCX generation:** ~5s for HE template (45 pages)
   - Mitigation: stream to client, async

### 6.2 Capacity (Faz 1)
- 100 concurrent users
- 500 drafts/month total
- 50 simultaneous draft generations (Celery workers)
- Database: 50 GB (RAG corpus + tenant data)

### 6.3 Rate Limits
| Resource | Limit | Window |
|---|---|---|
| EU F&T API | 100 req/min | external imposed |
| Crossref API | 50 req/sec | external (polite pool) |
| OpenAlex API | 10 req/sec | external (no email auth) |
| Anthropic API | depends on tier | 5000 RPM Tier 4 |
| User: LLM calls | 10/min | per user (our limit) |
| User: Drafts | 3/Starter, 15/Pro per month | plan-based |

---

## 7. Security Boundaries

```
┌──────────────────────────────────────────────────────────┐
│ Public Internet                                          │
└─────────────────┬────────────────────────────────────────┘
                  │ HTTPS, Cloudflare WAF (Vercel built-in)
                  ▼
┌──────────────────────────────────────────────────────────┐
│ Edge (Vercel)                                            │
│  - Static assets, SSR, ISR                               │
│  - Cookie auth (Supabase JWT)                            │
└─────────────────┬────────────────────────────────────────┘
                  │ HTTPS + Bearer token
                  ▼
┌──────────────────────────────────────────────────────────┐
│ API (Railway)                                            │
│  - JWT validation (Supabase)                             │
│  - Rate limiting (Redis sliding window)                  │
│  - CORS (origin allowlist)                               │
│  - Input validation (Pydantic)                           │
└─────────────────┬────────────────────────────────────────┘
                  │ Postgres connection (TLS)
                  ▼
┌──────────────────────────────────────────────────────────┐
│ Database (Supabase)                                      │
│  - RLS policies (tenant isolation)                       │
│  - Encrypted at rest                                     │
│  - Backup: daily, 7-day retention (Pro)                  │
└──────────────────────────────────────────────────────────┘
```

**Tehdit modeli:** `09-security-compliance.md` dosyasına bak.

---

## 8. Disaster Recovery

| Senaryo | RTO | RPO | Aksiyon |
|---|---|---|---|
| Vercel down | 15 min | 0 | Static fallback page (CDN) |
| Railway down | 30 min | 0 | Restore from latest deploy snapshot |
| Supabase DB corrupt | 1 hour | 24 hour | Point-in-time recovery |
| Anthropic API down | 5 min | 0 | OpenAI fallback automatic |
| Stripe webhook fails | 1 hour | 0 | Retry queue, manual reconciliation |

---

## 9. Open Architectural Questions (Hafta 1'de cevaplanacak)

1. **Hangi orchestration framework?**
   - Karar: Custom Python orchestrator (ag2 too heavy, LangGraph too new)
   - Reasoning: Tek Saga pattern, predictable, debug edilebilir
2. **Frontend state — Zustand mı RTK mı?**
   - Karar: Zustand (lighter, MVP için yeterli)
3. **i18n — next-intl mı next-i18next mi?**
   - Karar: next-intl (App Router native)
4. **DOCX library — python-docx mi docxtemplater mi?**
   - Karar: python-docx (more control, official microsoft alternative)

---

**Sonraki dosya:** `03-database-schema.md`