# Bluedev GrantWriter — Geliştirme Paketi v1.0

Bu paket, Bluedev GrantWriter ürününün dört haftalık MVP geliştirme sürecini başından sonuna planlayan dokümantasyon kitidir. Üç kişilik bir ekibin (founder/PM + 1 senior full-stack + 1 mid backend) bu paketi açıp doğrudan kod yazmaya başlayabilmesi hedeflenmiştir.

**Ürün:** AB ve Türkiye hibe programlarına başvuran KOBİ'ler için AI destekli, compliance onaylı, iki dilli (TR/EN) hibe yazımı SaaS'ı.

**Faz 1 kapsamı:** TÜBİTAK 1501, TÜBİTAK 1507, KOSGEB AR-GE, Horizon Europe RIA/IA, Cascade Funding + NLnet — beş program.

**Stack:** Next.js 15 (App Router) + Python FastAPI + PostgreSQL/pgvector + Redis + Celery + Claude API. Vercel + Railway + Supabase üzerinde host.

---

## Paket Yapısı

Toplam 13 dokümantasyon dosyası, 4 katmanda gruplanmış:

### Stratejik Katman
- **`docs/00-PRD.md`** — Ürün gereksinimleri, vizyon, persona, user story, başarı kriterleri, fiyatlandırma
- **`docs/01-CLAUDE.md`** — Claude Code'un master kontekst dosyası (proje köküne kopyalanacak)
- **`docs/02-architecture.md`** — Sistem mimarisi, servis listesi, data flow, performans hedefleri

### Veri Katmanı
- **`docs/03-database-schema.md`** — PostgreSQL DDL, RLS politikaları, RLS test suite, migration stratejisi
- **`docs/04-rag-strategy.md`** — RAG corpus tasarımı, citation grounding, halüsinasyon mitigation, distinctiveness scoring

### Uygulama Katmanı
- **`docs/05-api-contracts.md`** — REST endpoints, request/response schemas, SSE streaming, hata kodları
- **`docs/06-agent-architecture.md`** — 7 AI agent'ın detaylı tasarımı, prompts, orchestrator
- **`docs/07-program-modules.md`** — 5 program plugin sistemi (BaseProgramModule), DOCX/Excel template kararları
- **`docs/08-frontend-spec.md`** — Next.js sayfa hiyerarşisi, TipTap editör extensions, state stores, i18n

### Operasyonel Katman
- **`docs/09-security-compliance.md`** — KVKK + GDPR + EU AI Act + HE AI Disclosure, BYOK encryption, RLS, OWASP
- **`docs/10-deployment-devops.md`** — Vercel + Railway + Supabase setup, CI/CD, observability, DR

### Yürütme Katmanı
- **`sprints/sprint-roadmap.md`** — 4 haftalık sprint planı, gün-gün görev dağılımı, kişi atamaları, milestone'lar
- **`prompts/claude-code-prompts.md`** — Her sprint görevi için Claude Code'a verilebilecek hazır promptlar

---

## Başlangıç

Yeni repo açıp paketi içine kopyaladıktan sonra:

```bash
# 1. CLAUDE.md proje köküne taşınır
cp docs/01-CLAUDE.md ./CLAUDE.md

# 2. Claude Code başlatılır
claude
> /init
> Read CLAUDE.md, then docs/00-PRD.md and docs/02-architecture.md.
> Ready to start Sprint 1 Day 1 from sprints/sprint-roadmap.md.

# 3. Sprint 1 Day 1 görevleri prompts/claude-code-prompts.md'den sırayla çalıştırılır
# (S1.D1.T1 ile başla)
```

Önerilen okuma sırası (yeni katılan ekip üyesi için):
1. `docs/00-PRD.md` — ne yapıyoruz, neden
2. `docs/01-CLAUDE.md` — kod kuralları, mimari kararlar
3. `docs/02-architecture.md` — sistem yapısı
4. `docs/03-database-schema.md` — veri modeli
5. `docs/06-agent-architecture.md` — agent'lar
6. `docs/07-program-modules.md` — plugin sistem
7. `sprints/sprint-roadmap.md` — bu hafta ne yapıyoruz
8. Diğerleri ihtiyaç oldukça

---

## Faz 1 Başarı Kriterleri (Hafta 4 Sonu)

| Kriter | Hedef |
|---|---|
| Çalışan E2E demo | 5 program × en az 1 demo başvuru |
| Halüsinasyon oranı | <%5 fabricated citation |
| AI Disclosure compliance | %100 (HE Standard Application Form sayfa 32 otomatik) |
| Multi-tenant izolasyon | 0 cross-tenant data leak (RLS test suite yeşil) |
| Performans | <60 dakikada tam taslak (p95) |
| İlk paying customer | ≥1 (Bluedev pilot) |

5 metrikten 4'ü tutarsa Faz 2'ye geçilir. 3 tutarsa 1-2 hafta polish (Faz 1.5). 2 ya da daha az tutarsa kapsam küçültülüp hipotez tekrar test edilir.

---

## Bilinen Riskler ve Mitigation Yerleri

| Risk | Mitigation Dosyası |
|---|---|
| Halüsinasyon (fabricated citation) | `docs/04-rag-strategy.md` §3 |
| TÜBİTAK PRODİS / KOSGEB KBS API yok → manuel upload | `docs/07-program-modules.md` §3.3, §4.3 |
| HE AI Disclosure compliance | `docs/09-security-compliance.md` §2 |
| EC lump sum Excel template karmaşıklığı | `docs/07-program-modules.md` §3.5 |
| Multi-tenant data leak | `docs/03-database-schema.md` §4-5 + `docs/09-security-compliance.md` §6 |
| Claude API maliyet patlaması | `docs/06-agent-architecture.md` §7 (prompt caching, model routing) |
| Distinctiveness (Nature 2026 — AI proposals reject riski) | `docs/04-rag-strategy.md` §4 |

Her risk için mitigation kontrol noktası ilgili dosyada listelenmiştir; sprint roadmap bu kontrol noktalarına bağlanmıştır.

---

## Çekirdek Mimari Kararlar (Değiştirmeyin)

Bu kararlar tüm dokümantasyon boyunca tutarlı şekilde uygulanmıştır. Değişiklik gerekirse arch decision record (ADR) açılarak ekibe bildirilmelidir.

1. **LLM Router zorunlu.** Tüm LLM çağrıları `apps/api/src/llm/router.py` üzerinden geçer. Direkt SDK çağrısı business logic'te yasak. Gerekçe: BYOK desteği, cost tracking, fallback.
2. **Citation grounding zorunlu.** Her citation üretimden önce ya da sonra Crossref/OpenAlex ile doğrulanır. Doğrulanmamış citation export'u bloklar. Gerekçe: %14-95 halüsinasyon oranı, hibe değerlendirmesinde kariyer-bitirici.
3. **Provenance tracking zorunlu.** Her cümle `human` / `ai-generated` / `ai-edited` etiketli. AI disclosure (HE sayfa 32) bu metadata'dan otomatik üretilir. Gerekçe: EU AI Act + HE compliance.
4. **Multi-tenant RLS ile, application-level filtering ile değil.** SQL'de `WHERE tenant_id = ?` yazımı yasak. Gerekçe: defense in depth, RLS test suite ile sürekli doğrulanır.
5. **Long-running job → Celery, FastAPI BackgroundTasks değil.** Gerekçe: production reliability, retry, monitoring.
6. **Program modülleri plugin.** Yeni program eklemek için `apps/api/src/programs/` altına klasör + registry'ye tek satır. Çekirdek kodda program-spesifik if/else yasak. Gerekçe: Faz 2 genişlemesi (Eurostars, MSCA, EIC).
7. **Promptlar versiyonlu.** `prompts/{program}/{agent}/v1.md` yapısı, A/B test desteği. Gerekçe: regresyon, evaluation, deneyleme.
8. **Cost tracking per-tenant per-request.** Her LLM çağrı `tenant_usage_log`'a yazar. Gerekçe: kârlılık, alarm, BYOK doğrulama.
9. **Custom orchestrator.** ag2/AutoGen ve LangGraph reddedildi. Saga pattern + Celery + Redis pub/sub. Gerekçe: predictable, debug edilebilir, hafif.
10. **Embedding modeli OpenAI text-embedding-3-large.** Gerekçe: multilingual (TR+EN tek index), pgvector HNSW kalitesi, maliyet-fayda.

---

## Çalışma İlkeleri

1. **Production-grade kod, prototip değil.** Her commit deploy edilebilir.
2. **Test paritesi.** Her PR yeni özellik için ≥1 test ekler.
3. **Doc-as-code.** API contract / schema / prompt değişiyorsa ilgili `.md` aynı PR'da güncellenir.
4. **Code review zorunlu.** Self-merge yasak. Founder dahil herkes review alır.
5. **CI yeşilse merge.** RLS test ve type check özellikle kritik.
6. **Kullanıcı odaklı iter.** Pilot feedback hızlı işlenir.
7. **Maliyet farkındalığı.** LLM cost dashboard günlük kontrol.

---

## Lisans ve IP

Repo: özel mülk, Bluedev (Blue Robot Teknolojileri ve Ticaret Ltd. Şti.).
Etkilenen open source: `lewisExternal/AI-Grant-Writer-Tool` (MIT) referans alındı; doğrudan fork değil, sıfırdan yazılan ürün.

---

## Doküman Versiyonu

v1.0 — 2026-05-07. Faz 1 başlangıcı için onaylanmış.
Güncellemeler `CHANGELOG.md`'ye işlenir; her dosyanın altındaki "Sonraki dosya" zinciri kırılırsa README burada güncellenir.

---

**Bu paketi okumadan kod yazmaya başlamayın.** Her dosya birbirini referans alır; tek bir yerden başlayıp diğerlerini görmezden gelmek mimari tutarsızlığa yol açar. Sprint planlama Pazartesi sabahları yapılır; her sprint sonunda retro alınır; çekirdek mimari kararlar değişiyorsa ADR açılır.