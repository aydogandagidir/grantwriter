# Bluedev GrantWriter

> Çağrı linkini ver, taslak başvuruyu al — kaynaklı, compliance-onaylı, distinctive.

AB ve Türkiye hibe programlarına başvuran KOBİ'ler için **AI destekli, compliance onaylı, iki dilli (TR/EN) hibe yazımı SaaS'ı**.

**Faz 1 (4 hafta):** TÜBİTAK 1501, TÜBİTAK 1507, KOSGEB AR-GE, Horizon Europe RIA/IA, Cascade Funding + NLnet — 5 program.

---

## Stack

- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui — `apps/web`
- **Backend:** FastAPI (Python 3.11+) + Celery + Redis — `apps/api`
- **Database:** PostgreSQL 16 + pgvector + Supabase Auth (RLS)
- **LLM:** Anthropic Claude (Opus 4.7 primary, Sonnet 4.6 secondary), OpenAI fallback
- **Infra:** Vercel (web) + Railway (api/worker) + Supabase (db/auth/storage)

Detay: [`CLAUDE.md`](CLAUDE.md), [`docs/02-architecture.md`](docs/02-architecture.md).

---

## Ön Koşullar

- **Node.js** ≥ 20.0.0
- **pnpm** ≥ 9.0.0 (`corepack enable && corepack prepare pnpm@9.15.0 --activate`)
- **Python** ≥ 3.11
- **Poetry** ≥ 1.8 (`pipx install poetry`)
- **Docker** (lokal Postgres + Redis + Mailhog için)
- **Supabase CLI** (migrate komutu için, sonraki sprint'te)

---

## Hızlı Başlangıç

```bash
# 1. Bağımlılıkları yükle (S1.D1.T2 sonrası çalışır)
pnpm install
cd apps/api && poetry install && cd ../..

# 2. Lokal servisleri başlat (Postgres + Redis + Mailhog + API + Worker + Web)
make dev
# Web      → http://localhost:3000
# API      → http://localhost:8000
# Mailhog  → http://localhost:8025

# 3. Migration ve seed (S1.D2 sonrası)
make migrate
make seed

# 4. Test ve lint
make test
make lint
```

Detay: [`docs/10-deployment-devops.md`](docs/10-deployment-devops.md) §3.

---

## Repository Yapısı

```
bluedev-grantwriter/
├── apps/
│   ├── web/                 # Next.js 15 frontend
│   └── api/                 # FastAPI backend (7 agents, RAG, citations, exports)
├── packages/
│   └── shared-types/        # Web ↔ API arasında paylaşılan TS tipleri
├── infra/                   # Docker compose, Supabase migrations, Railway config
├── scripts/                 # Seed data, cost reports, vb.
├── docs/                    # 13 dosyalık dokümantasyon paketi
├── CLAUDE.md                # Claude Code master context (tek doğruluk kaynağı)
├── Makefile                 # dev / test / lint / migrate / seed
└── README.md
```

---

## Dokümantasyon Yol Haritası

| Katman | Dosya | İçerik |
|---|---|---|
| Stratejik | [`docs/00-PRD.md`](docs/00-PRD.md) | Vizyon, persona, başarı kriterleri, fiyatlandırma |
| Stratejik | [`docs/01-CLAUDE.md`](docs/01-CLAUDE.md) | Master context (`CLAUDE.md`'ye kopyalandı) |
| Stratejik | [`docs/02-architecture.md`](docs/02-architecture.md) | Sistem mimarisi, servis listesi, data flow |
| Veri | [`docs/03-database-schema.md`](docs/03-database-schema.md) | DDL, RLS politikaları, migration stratejisi |
| Veri | [`docs/04-rag-strategy.md`](docs/04-rag-strategy.md) | RAG corpus, citation grounding, hallucination |
| Uygulama | [`docs/05-api-contracts.md`](docs/05-api-contracts.md) | REST endpoints, SSE streaming, hata kodları |
| Uygulama | [`docs/06-agent-architecture.md`](docs/06-agent-architecture.md) | 7 AI agent, prompts, orchestrator |
| Uygulama | [`docs/07-program-modules.md`](docs/07-program-modules.md) | 5 program plugin sistemi |
| Uygulama | [`docs/08-frontend-spec.md`](docs/08-frontend-spec.md) | Next.js sayfa hiyerarşisi, state, i18n |
| Operasyonel | [`docs/09-security-compliance.md`](docs/09-security-compliance.md) | KVKK + GDPR + EU AI Act |
| Operasyonel | [`docs/10-deployment-devops.md`](docs/10-deployment-devops.md) | Vercel + Railway + Supabase, CI/CD |
| Yürütme | [`docs/sprint-roadmap.md`](docs/sprint-roadmap.md) | 4 haftalık sprint planı |
| Yürütme | [`docs/claude-code-prompts.md`](docs/claude-code-prompts.md) | Her sprint görevi için hazır promptlar |

---

## Geliştirme Akışı

- **Branch adlandırma:** `feature/<ticket>-<short-desc>`, `fix/<short-desc>`, `chore/<short-desc>`
- **Commit format:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`)
- **PR review:** ≥1 onay, no self-merge to `main`
- **Sprint promptları:** [`docs/claude-code-prompts.md`](docs/claude-code-prompts.md) — Claude Code ile her görev tek prompt

Detaylı kurallar: [`CLAUDE.md`](CLAUDE.md) "Coding Standards" ve "Critical Architectural Decisions".

---

## Lisans

Proprietary — © Bluedev. Tüm hakları saklıdır.
