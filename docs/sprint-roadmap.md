# Sprint Yol Haritası — 4 Hafta MVP

## 0. Ekip ve Kapasite Planlaması

Üç kişi, dört hafta, beş program. Bunu yapabileceğimizi düşünmemizin tek yolu **disiplinli bir scope yönetimi** ve **paralel geliştirme**. Aşağıda günlük plan paralelize edildi — her gün kim ne yapıyor net.

### Roller

- **Aydoğan (Founder/PM, ~%40 coding):** Backend ağırlıklı, mimari kararlar, plugin sistem, müşteri görüşmeleri, ürün kararları. Hafta başlarında çift saat sprint planning, hafta sonu retro.
- **Senior Full-Stack (Sn) — %100 coding:** Frontend lead. Editör, brief formları, validation UI. Backend'e gerektiğinde girer. Çift haftalık review yükü.
- **Mid Backend (Md) — %100 coding:** API endpoints, agent implementasyonu, RAG pipeline, scrapers. İlk hafta onboarding'i Aydoğan birebir.

### Kapasite

3 kişi × 4 hafta × 5 gün × 8 saat = **480 saat**. Realistic verim ~%75 (interrupt'lar, debugging, planlama) → **~360 etkili saat**. Bu rakamı aşağıdaki task'larda saat olarak (h) işaretleyeceğim.

### Çalışma ritmi

- **Pazartesi 09:00:** Sprint planning (1 saat)
- **Her gün 09:30:** Standup (15 dk, async Loom kabul)
- **Çarşamba 16:00:** Mid-sprint check-in (30 dk)
- **Cuma 16:00:** Sprint review + demo (1 saat)
- **Cuma 17:00:** Retro (30 dk)
- **Cumartesi/Pazar:** Off (sürdürülebilirlik)

### Risk yönetimi

Kritik bağımlılıklar (paralelize EDİLEMEZ):
1. DB schema önce — her şey buna bağlı (Day 1-2)
2. LLM Router önce — agent'lar buna bağlı (Day 3-4)
3. Program plugin interface önce — programlar buna bağlı (Day 4-5)
4. CallAnalyst önce — diğer agent'lar buna bağlı (Day 6-7)

Diğer her şey paralelize edilebilir.

---

## 1. Sprint 1 — Hafta 1: Temel Altyapı

**Hedef:** Çalışan auth + DB + 1 program için brief→generate→export uçtan uca demo.

**Demo (Cuma):** Tek bir TÜBİTAK 1501 başvurusu için brief gir → 1 agent (Excellence Writer) çalışsın → markdown çıktısı görünsün.

### Day 1 — Pazartesi

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:00 | Sprint planning (3 kişi) | | | Tüm hafta görevlerinin Linear/Jira'ya işlenmesi |
| 10:00 | Repo init, monorepo (Turbo+pnpm), CLAUDE.md kopyala | Vercel + Railway + Supabase hesap ayarı | Python `apps/api` poetry init, FastAPI hello world | Repo `main` branch'ine ilk commit |
| 14:00 | Supabase project oluştur, Auth setup | Next.js 15 init `apps/web`, shadcn/ui kurulum | Docker compose (postgres+redis) lokal up | `make dev` çalışıyor |
| 16:00 | Migration 001-002 (extensions, tenants, users) | Login sayfası (Supabase Auth) iskeleti | Pydantic Settings + base config | RLS olmadan basit auth çalışıyor |

**Day 1 sonu:** Repo + auth + DB connection. Senin canlıda görmen gereken: `app.bluedev.dev` localde açılıyor, login → dashboard skelet.

### Day 2 — Salı

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | Migration 003-008 (programmes, calls, proposals, ...) | shadcn/ui temel layout (Sidebar+TopBar) | Supabase Python client, JWT validation middleware | Tüm tablolar oluştu |
| 14:00 | RLS policies (Migration 012) | i18n setup (next-intl, tr.json + en.json scaffold) | `GET /api/v1/me`, `GET /api/v1/programmes` endpoint'leri | RLS test SQL çalışıyor |
| 16:00 | RLS test suite (CRITICAL — yeşil olmadan PR yok) | Programmes seed data UI'da listelenebiliyor | Programs registry stub, BaseProgramModule interface | `pytest tests/security/` yeşil |

**Day 2 sonu:** Multi-tenant ayağa kalktı, RLS test suite CI'da. Bu hafta hatasını burada yapmazsak güvenlik açığı yok.

### Day 3 — Çarşamba

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | LLM Router temel (claude_provider) + cost tracking | DynamicBriefForm component (RHF + Zod) | Calls CRUD endpoints | LLM Router unit test |
| 14:00 | TUBITAK1501 program modülü skeleti | Brief schema'yı backend'den çekip render etme | EU F&T Portal API client (read-only) | İlk Claude çağrısı API'den |
| 16:00 | Mid-sprint check-in (30dk) — blocker review | Calls listesi sayfası (filtre + pagination) | TÜBİTAK çağrı scraper iskeleti (manuel seed) | Çağrı listesi UI'da görünüyor |

### Day 4 — Perşembe

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | TUBITAK1501 brief schema + validation | Brief sayfası (`/proposals/[id]/brief`), auto-save | Proposals CRUD + state machine transitions | Brief kaydedebiliyoruz |
| 14:00 | CallAnalyst agent v1 (prompt + run) | Editör skeleti (TipTap kurulumu, basic) | Celery worker setup, `generate_draft_task` stub | Celery task tetiklenebiliyor |
| 16:00 | ExcellenceWriter agent v1 (TÜBİTAK için) | Generation Progress UI (SSE EventSource) | SSE endpoint `/proposals/{id}/stream` | Stream'den event geliyor |

### Day 5 — Cuma

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | TÜBİTAK 1501 DOCX export (basit AGY100) | Export sayfası ve indirme akışı | ProposalsService — orchestrator entegrasyon | DOCX indirilebiliyor |
| 14:00 | E2E test: brief→generate→export (sadece Excellence) | Editör'de DOCX preview (read-only) | Proposal status transitions test'i | E2E manuel olarak çalışıyor |
| 16:00 | **Sprint Review + Demo** (3 kişi) | | | Demo: TÜBİTAK 1501 başvurusu üretildi |
| 17:00 | **Retro** | | | Hafta 2 önceliği netleşti |

**Sprint 1 başarı kriteri:** Demo'da bir TÜBİTAK 1501 brief'i girilip Excellence Writer çalıştırılıp DOCX indirilebiliyor olmalı. Bu olmazsa Sprint 2'ye geçmiyoruz, ek 2 gün veriyoruz.

---

## 2. Sprint 2 — Hafta 2: Çoklu Program + RAG + Citations

**Hedef:** 5 programın hepsi temel seviyede çalışıyor + RAG aktif + citation verification çalışıyor.

**Demo (Cuma):** Horizon Europe RIA başvurusu; 4 agent (CallAnalyst, Excellence, Impact, Implementation) sırayla çalışıyor; citations Crossref ile doğrulanıyor; UI'da renkli rozetler.

### Day 6 — Pazartesi

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:00 | Sprint 2 planning | | | |
| 10:00 | HorizonEURIA program modülü (subsection_map, brief schema) | Brief formu HE için render testi | RAG infrastructure: chunker.py + embedder.py | HE brief formu render oluyor |
| 14:00 | EU F&T Portal scraper (real, daily Celery) | TipTap CitationMark extension | RAG retriever + pgvector HNSW index | Çağrılar otomatik scrape oluyor |
| 16:00 | ImpactWriter ve ImplementationWriter prompt'ları (HE) | TipTap ProvenanceMarker extension | Successful proposals corpus seed (10 HE örnek manuel) | Editör provenance gösteriyor |

### Day 7 — Salı

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | TUBITAK1507 + KOSGEB modülleri (TUBITAKBase miras) | Citations sidebar UI | CitationVerifier (Crossref + OpenAlex) | 1507 ve KOSGEB de çalışıyor |
| 14:00 | CascadeFunding/NLnet modülü | Citation badge tıklama → DOI açma | Redis citation cache (30 day TTL) | Citation verification çalışıyor |
| 16:00 | Orchestrator: paralel agent execution (Excellence + Impact eşzamanlı) | | Embedding generation script (CORDIS prep) | 4 agent paralel + sıralı çalışıyor |

### Day 8 — Çarşamba

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | HE Standard Application Form DOCX template (eski versiyondan adapte) | Markdown→DOCX render edge cases | CORDIS dataset download + parse + embed | Toplam 12K CORDIS proje yüklü |
| 14:00 | Mid-sprint review | DistinctivenessScorer skeleton UI | DistinctivenessScorer backend (cosine vs CORDIS) | Distinctiveness UI'da görünüyor |
| 16:00 | Lump sum Excel template (HE 2026) | Compliance sayfası iskeleti | Markdown→DOCX converter (custom) | Lump sum Excel indirilebiliyor |

### Day 9 — Perşembe

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | ComplianceReviewer agent (page limit, DNSH, gender) | Compliance sayfası: blocker/warning listesi | HallucinationHunter agent (final pass) | Compliance hesaplanıyor |
| 14:00 | TÜBİTAK 1501 PRODİS export sayfası (alan-alan kopyalama) | "Re-validate" butonu | Multi-citation batch verification | PRODİS export UI çalışıyor |
| 16:00 | Auto AI disclosure metni üretimi | AI disclosure preview UI | Provenance auto-update (TipTap transaction handler) | AI disclosure dolduruluyor |

### Day 10 — Cuma

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | E2E test: HE RIA full flow | Editör polish (citation hover tooltip, scroll-to) | RAG quality smoke test (5 örnek brief) | HE E2E manuel başarılı |
| 14:00 | Bug fix swarm — review tüm 5 program | Bug fix — UI/UX refinement | Bug fix — backend errors | Backlog azalmış |
| 16:00 | **Sprint Review + Demo** | | | Demo: HE RIA tam flow + citations + distinctiveness |
| 17:00 | **Retro** | | | Hafta 3 prioriteleri |

**Sprint 2 başarı kriteri:** 5 programın hepsi brief+generate üretebiliyor (kalite henüz mükemmel olmasa da). Citations doğrulanıyor. Distinctiveness skoru hesaplanıyor.

---

## 3. Sprint 3 — Hafta 3: Compliance, Quality, Multi-User

**Hedef:** Production-grade compliance (HE AI disclosure tam), multi-user/tenant tamamlanmış, BYOK çalışıyor, billing iskeleti hazır.

**Demo (Cuma):** İki tenant (Bluedev + Test Müşteri), her birinde 2 kullanıcı, BYOK setup, plan upgrade flow Stripe'la, çalışan başvuru üretimi + export.

### Day 11 — Pazartesi

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:00 | Sprint 3 planning | | | |
| 10:00 | BYOK encryption (pgcrypto) implementasyon | LLM Config sayfası (`/settings/llm-config`) | LLMRouter — BYOK key resolution | BYOK kaydedilebiliyor |
| 14:00 | Test endpoint (`/llm-config/test`) | BYOK test butonu UI | Cost tracker — usage_log dolduruyor | Anahtar testi UI'dan çalışıyor |
| 16:00 | Tenant invitations (DB + endpoint) | Tenant members sayfası (`/settings/members`) | Email invitations (Resend) | Davet flow çalışıyor |

### Day 12 — Salı

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | Stripe entegrasyon (subscription + portal) | Billing sayfası | Stripe webhook handler | Test ödeme alınabiliyor |
| 14:00 | Iyzico entegrasyon | Plan limit enforcement UI | Iyzico webhook handler | Iyzico de çalışıyor |
| 16:00 | Plan limit DB trigger doğrulama | Quota dolu tooltip UI | Usage report endpoint | Aşım engellenmiş oluyor |

### Day 13 — Çarşamba

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | DNSH agent rule + LLM hybrid | Validation page polish | Compliance report JSON schema | DNSH check %80 doğru |
| 14:00 | Mid-sprint check-in | Distinctiveness UI iyileştirme (similar projects list) | Hallucination Hunter genişletilmiş claim verification | Distinctiveness similar projects listeli |
| 16:00 | Versioning system (proposal_versions) | Versiyon geçmişi UI | Version snapshot Celery task | Eski versiyona dönüş çalışıyor |

### Day 14 — Perşembe

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | Comments system DB + endpoint | Comments UI (yorum ekle, çöz, thread) | Audit log — kritik event'ler | Yorumlar çalışıyor |
| 14:00 | Rate limiting (Redis sliding window) | Toast: rate limit notification | Audit log Sentry breadcrumb | Rate limit aktif |
| 16:00 | Email notifications (draft complete, member added) | Notification preferences UI | Resend templates (TR + EN) | Email tetiklenebiliyor |

### Day 15 — Cuma

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | E2E test: Multi-tenant izolasyon (2 tenant simüle) | A11y audit (axe-core) | Sentry frontend + backend ayar | A11y temel hatalar fixed |
| 14:00 | Bug fix swarm | Mobile responsive smoke | Performance: slow query analiz | Backlog azaldı |
| 16:00 | **Sprint Review + Demo** | | | Multi-tenant demo |
| 17:00 | **Retro** | | | Hafta 4 prioriteleri |

**Sprint 3 başarı kriteri:** Multi-tenant tamamen çalışıyor, BYOK encryption test edildi, Stripe checkout başarılı. AI disclosure compliance %100.

---

## 4. Sprint 4 — Hafta 4: Polish, Pilot, Production Deploy

**Hedef:** Production'a deploy + 2 pilot müşteri onboarded + bug-free demo + ilk paying customer (Bluedev).

**Demo (Cuma):** Production'da `app.bluedev.dev`; 2 dış pilot müşteri uçtan uca başvuru tamamladı; ödeme alındı; tüm metrikler dashboard'da.

### Day 16 — Pazartesi

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:00 | Sprint 4 planning + müşteri pilot listesi | | | |
| 10:00 | Production env vars setup, secret rotation | Marketing site iskeleti (homepage + pricing) | Production deploy script test (staging) | Staging tam çalışıyor |
| 14:00 | DR drill (Supabase restore staging'e) | Onboarding flow (2 sayfa wizard) | Smoke test suite production targets | DR drill başarılı |
| 16:00 | Sentry alerts + PostHog dashboards | Help docs iskeleti (Notion sync ya da MDX) | Better Stack uptime monitor | Monitoring aktif |

### Day 17 — Salı

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | Pilot 1 onboarding (Bluedev kendi başvurusu için) | Bug fix UI feedback | Pilot 1'in brief'i ile gerçek HE generate | Pilot 1 brief tamamlandı |
| 14:00 | Pilot 1 destekli üretim (manuel watch) | Editör polish (loading states, empty states) | Cost tracking dashboard'da görselleştir | İlk gerçek başvuru üretildi |
| 16:00 | Pilot 2 onboarding kontak | Mobile fixes | Email notification flow doğrulama | Pilot 2 hazır |

### Day 18 — Çarşamba

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | Pen test prep (target list, scope) | Final UX pass (all pages) | Performance: bundle analyzer + optimization | Frontend bundle <200KB |
| 14:00 | Mid-sprint check-in | Tooltip ve help text doluluğu | Backend p95 optimizasyon | API p95 <500ms |
| 16:00 | Stripe live mode aktif (Aydoğan kendi kartı test) | Pricing sayfası polish | LLM cost optimization (prompt caching kontrol) | Live mode çalışıyor |

### Day 19 — Perşembe

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | Pilot 2 başvurusu izle, feedback al | Loading states + error boundaries | Citation verification rate ölçüm | Pilot 2 draft tamamlandı |
| 14:00 | İlk paying customer transaction (Bluedev kendi pilotu için ödeme) | Skeleton screens, optimistic UI | DR drill production'da (read-only test) | İlk gerçek ödeme |
| 16:00 | Marketing email taslak (Türkiye SMM ağı) | About + Privacy pages | Logs review — production'da gözle | Outbound mail hazır |

### Day 20 — Cuma

| Saat | Aydoğan | Sn | Md | Çıktı |
|---|---|---|---|---|
| 09:30 | Final pen test (kendi içeride veya dışarıdan) | A11y final audit | Final perf benchmarks | Pen test raporu |
| 14:00 | Faz 2 hazırlık (sprint planning şablonu) | Polish son sıkıştırma | Release notes + changelog | v1.0.0 tag |
| 16:00 | **Sprint Review + Final Demo** (3 kişi + pilotlar invite) | | | MVP teslim |
| 17:00 | **Faz 1 Retro** | | | Faz 2 vs polish kararı |

**Sprint 4 başarı kriteri (Faz 1 final):**
- ✅ Production'da `app.bluedev.dev` çalışıyor
- ✅ 5 program × en az 1 demo başvuru tamamlandı
- ✅ 2 pilot dış müşteri başvuru üretti
- ✅ İlk paying customer (Bluedev internal — Stripe live mode'da ödeme alındı)
- ✅ Halüsinasyon oranı sample test'te <%5
- ✅ AI disclosure compliance %100 (HE pilot'unda doğrulandı)
- ✅ Multi-tenant izolasyon test edildi

---

## 5. Faz 1 Sonu Karar Matrisi

Hafta 4 retro'da bu metrikleri tablo halinde değerlendireceğiz:

| Metrik | Hedef | Tutarsa | Tutmazsa |
|---|---|---|---|
| Çalışan E2E (5 program) | 5/5 | Faz 2'ye gir | 4/5 → Faz 1.5 |
| Halüsinasyon oranı | <%5 | OK | <%10 → polish, <%5 düşürene kadar Faz 2 yok |
| Multi-tenant 0 leak | 0 | OK | varsa → kritik, deploy çek |
| İlk paying customer | 1 | OK | 0 → ürün-pazar uyumu sorgu |
| Performance (60dk taslak) | <60dk | OK | <90dk kabul edilebilir |

**Karar kuralı:** 5'te 4'ü tutuyorsa Faz 2'ye geç. 5'te 3'ü tutuyorsa 1-2 hafta polish (Faz 1.5). 5'te 2 ya da daha azı tutuyorsa scope'u 2 programa indir, sadece TR pazara odaklan, hipotezi test et.

---

## 6. Sonraki 6 Aylık Yol Haritası (Yüksek Seviye)

| Ay | Faz | Ana hedef |
|---|---|---|
| **Ay 2 (Haziran 2026)** | Faz 1.5 | Production iyileştirmeler, ilk 10 paying customer (€100-300/ay), customer success otomasyonu (Resend sequences), feedback toplayıp roadmap güncelle |
| **Ay 3 (Temmuz)** | Faz 2 — Genişleme | Eurostars + EIC Accelerator + MSCA programları ekle. Konsorsiyum partner matching motoru beta. Reviewer feedback (ESR) parser. Self-host EU model deneyi. |
| **Ay 4 (Ağustos)** | Faz 2 — Pazar | İtalyan ve İspanyol pazarlara açılım (lokalizasyon + pazarlama). Agency white-label tier. Stripe usage-based billing. |
| **Ay 5 (Eylül)** | Faz 2 — Olgunluk | Reviewer Simulation agent (skor öngörme). Mobile uygulamada read-only. WhatsApp bot entegrasyon (Bluedev'in mevcut KOBİ CRM altyapısı ile köprü). |
| **Ay 6 (Ekim)** | Faz 3 başlangıç | Self-hosted LLM (Mistral Large EU cloud) Enterprise plan için. Compliance audit (KVKK + GDPR). 50+ paying customer hedefi. |

Faz 2 ve sonrası bu sprint planının dışı, ancak bugün alınan mimari kararların hepsi bu yola dayanak verecek şekilde tasarlandı (plugin programs, BYOK, custom orchestrator, RLS multi-tenant).

---

## 7. Sprint Boyunca Daimi İlkeler

1. **Bugün'ün koduyla yarın deploy edebilmeli.** Her commit production-grade. "Sonra düzeltirim" yok.
2. **Test paritesi.** Her PR yeni özellik için en az 1 test ekler.
3. **Documentation as code.** API contract, schema, prompt değişiyorsa ilgili `.md` dosyası aynı PR'da güncellenir.
4. **Code review zorunlu.** Self-merge yok. Aydoğan'ın bile PR'ı review olmalı.
5. **CI yeşilse merge, kırmızıysa fix.** Geçici disable yok.
6. **Customer obsessed.** Pilot müşteriden gelen feedback hızlı iter. "Backlog'a ekleyip 3 ay sonraya bırak" değil.
7. **Maliyet farkındalığı.** LLM cost dashboard her gün kontrol edilir; anomali görülürse durdurulur.

---

## 8. Sprint 4 Day 16 — Gerçekleşen (Production Hardening Addendum)

> Plandaki Day 16 (Aşama A-D, production deploy) fiilen tamamlandı, ancak bring-up planlanandan daha çetin geçti: 5 ardışık config/altyapı failure'ı art arda çıktı. Hepsi koda/altyapıya kalıcı koruma olarak işlendi. Detaylı operasyon kılavuzu: **`docs/11-operations-runbook.md`** (incident kataloğu §5).

### Yaşanan failure zinciri → kalıcı çözüm

| # | Failure | Kalıcı koruma | PR |
|---|---|---|---|
| 1 | Frontend favicon 404 + date hydration crash | `app/icon.svg`, next-intl `timeZone`/`now` | #30 |
| 2 | Eksik prod env → `JWT not configured` 503 | (env wiring) + preflight | #33/#36 |
| 3 | `DATABASE_URL` host `[...]` → urlsplit crash | `db.py::_normalize_dsn` | #30 |
| 4 | Strict preflight → eksik env'de boot loop | warn-by-default + `PREFLIGHT_STRICT` opt-in | #36 |
| 5 | `.dockerignore` template'i siliyor → build fail | `!.env.production.example` exception | #37 |
| 6 | DB upstream down → boot crash-loop | resilient lifespan + `/health/db` | #38 |
| 7 | Supabase restore sonrası manuel redeploy gerek | `/health/db` lazy retry (self-heal) | #39 |

### Kazanılan kalıcı yetenekler

- **Frontend CI** (`web-ci.yml`): her PR'da typecheck + lint + vitest (eskiden yalnız Vercel build) — PR #32/#34.
- **Production preflight** (`core/preflight.py`): eksik env / `<placeholder>` / bozuk DSN'i boot'ta net mesajla yakalar; warn-default, strict opt-in — PR #33/#36.
- **Graceful degradation:** DB upstream herhangi bir sebepten düşse app ayakta kalır (`/health` 200), `/health/db` nedeni raporlar, upstream dönünce self-heal eder — PR #38/#39.
- **Smoke test** (`scripts/smoke.sh`): tek komutla backend + frontend + favicon doğrulama — PR #35.
- **Operations runbook** (`docs/11-operations-runbook.md`): incident kataloğu + recovery prosedürleri — PR #40.

### Daimi İlke #4 istisnası (kayıt için)
Bölüm 7 madde 4 "self-merge yok" der. Bu production-down recovery zinciri (PR #36-39) tek operatör tarafından, her PR CI-yeşil + atomik + adversarial-verified olarak self-merge edildi — incident aciliyeti gereği. Normal feature akışı code-review kuralına döner.

### Açık (opsiyonel) kuyruk
- `infra/render.yaml` IaC (mevcut `infra/railway.json` deployed gerçeği yansıtmıyor — Render kullanılıyor).
- `scripts/preflight-check.sh` (bash) ile `core/preflight.py` (Python) arasında placeholder/DSN kontrol paritesi.
- Custom domain `app.bluedev.dev` / `api.bluedev.dev` (şu an `*.vercel.app` / `*.onrender.com`).
- `PREFLIGHT_STRICT=true`'ya geçiş — tüm REQUIRED env matrisi tamamlandığında (Aşama D).

---

**Sonraki dosya:** `prompts/claude-code-prompts.md` — Her görevi Claude Code'a verecek hazır promptlar.