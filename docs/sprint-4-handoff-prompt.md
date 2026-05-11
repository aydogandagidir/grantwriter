# Sprint 4 — Yeni Oturum Açılış Prompt'u

> Bu dosyayı yeni Claude oturumuna başlangıçta yapıştır. Self-contained: Sprint 4 Day 16'ya başlamak için bilmen gereken her şey içinde.

---

## Bağlam (state)

**Repo:** https://github.com/aydogandagidir/grantwriter (origin/main güncel)

**Sprint 3 (Hafta 3) tamamlandı:**
- PR #6 (backend), #7→#9 (frontend), #8 (CI fix), #10 (closure docs), #14 (TICKET-001/002/003 root-cause fix) — hepsi main'e merge edildi
- Issue #11, #12, #13 (Sprint 3 known issues) → PR #14 ile kapatıldı

**main HEAD beklenen state:**
- Backend: 8 tenant route (BYOK / members / invitations / audit / usage / billing / me + 3 collaboration), Iyzico webhook + outbound, Hallucination Hunter LLM claim verifier, DNSH rule layer, Versioning, Comments, Resend lazy-init email, observability foundation (Sentry/Logtail lazy-init), rate-limit, audit log
- Frontend (Next.js 15 + Tailwind + shadcn + Supabase SSR + next-intl): auth, app shell with mobile drawer, dashboard, 8 settings pages, public invite accept, proposal-editor stub with Versions + Comments panels, TR/EN i18n, 24 Vitest + 10 jest-axe a11y smoke
- Tests: 515 pytest passed (0 failed), 24 vitest passed
- Pre-existing flaky markers TICKET-001/2 ile fix'lendi; CI -m "not flaky_pre_s3" artık yok
- TICKET-003 wiring done (release tracking + /health/sentry-test endpoint); production DSN secrets pending

**Sprint 4 hedefi** (`docs/sprint-roadmap.md` §4):
> Production'a deploy + 2 pilot müşteri onboarded + bug-free demo + ilk paying customer (Bluedev).
> Cuma demo: Production'da `app.bluedev.dev`; 2 dış pilot müşteri uçtan uca başvuru tamamladı; ödeme alındı; tüm metrikler dashboard'da.

**Sprint 4 Day 16 task'ları (sprint-roadmap.md §4 Day 16):**

| Saat | Aydoğan | Sn | Md |
|---|---|---|---|
| 09:00 | Sprint 4 planning + müşteri pilot listesi | | |
| 10:00 | Production env vars setup, secret rotation | Marketing site iskeleti (homepage + pricing) | Production deploy script test (staging) |
| 14:00 | DR drill (Supabase restore staging'e) | Onboarding flow (2 sayfa wizard) | Smoke test suite production targets |
| 16:00 | Sentry alerts + PostHog dashboards | Help docs iskeleti (Notion sync ya da MDX) | Better Stack uptime monitor |

---

## Önemli kararlar (Sprint 3'ten taşındı)

- **Payment provider: Iyzico** (Stripe Faz 2'ye ertelendi — kullanıcı kararı)
- **Email provider: Resend** (lazy-init, Sentry paterni)
- **Versioning restore: snapshot-as-new-current** (history append-only)
- **Comments: tek-seviye thread** (parent_id depth ≤ 1)
- **Iyzico outbound: pure-httpx, no SDK** (dep tree flat)
- **Backend test DB pattern: TEST_DATABASE_URL required** (TICKET-002 fix)
- **CI fast lane: bluedev_test (CI service), bluedev_demo (demo seed), bluedev (developer scratch)** — üçü ayrı

---

## Mevcut dokümantasyon (yeni oturumda OKUNACAK önemli sırada)

1. **`docs/sprint-roadmap.md`** §4 — Sprint 4 Day 16-20 plan
2. **`docs/sprint-4-prep.md`** — production deploy preconditions (Railway/Vercel/Supabase secret matrisleri, "ready for Day 16" checklist)
3. **`docs/sprint-3-retro.md`** — Sprint 3 ne yapıldı + sapmalar + metrikler
4. **`docs/sprint-3-demo-script.md`** — 10 adımlı multi-tenant demo akışı (Friday demo'da reuse edilecek)
5. **`docs/sprint-3-known-issues.md`** — TICKET-001/2/3 (hepsi fix'lendi ama context için)
6. **`CLAUDE.md`** — repo geneli geliştirme kuralları
7. **`docs/10-deployment-devops.md`** — production deploy mimarisi (Railway + Supabase + Vercel + Sentry)
8. **`docs/09-security-compliance.md`** — BYOK encryption + KVKK/GDPR

---

## Çevre / araçlar (zaten hazır)

- **Worktree path:** `C:/Users/adagidir/Desktop/Artificial Inteligence/adagidir_aiapps/grantwriter/.claude/worktrees/blissful-zhukovsky-21772f` (Sprint 3 closure burada işlendi; main güncel — yeni branch açabilirsin veya ana repo `C:/Users/adagidir/Desktop/Artificial Inteligence/adagidir_aiapps/grantwriter` kullan)
- **Docker:** `bluedev-postgres` (pgvector/pg16) + `bluedev-redis` (redis:7-alpine) healthy çalışıyor (`docker ps` ile doğrula)
- **Test DB:** `bluedev_test` (13 migration uygulanmış)
- **gh CLI:** authenticated (account `aydogandagidir`, token scopes: repo, workflow, gist, project, read:org)
- **Poetry env:** `apps/api`'de kurulu (`poetry run pytest` çalışıyor)
- **pnpm env:** `apps/web`'de kurulu (`pnpm test`, `pnpm build` çalışıyor)
- **Bu konuşmanın özeti:** `docs/sprint-3-retro.md` + bu dosya yeterli; geri kalan oturum sohbetini okumana gerek yok

---

## Sprint 4 Day 16 — alt-aşamalar

### Aşama A: Pre-deploy validation (Claude yapabilir)

1. main'in son halini fetch et, durumu doğrula
2. `apps/api/.env.production.example` oluştur — `docs/sprint-4-prep.md` §1'deki tüm secret'ları placeholder ile listele
3. `apps/web/.env.production.example` oluştur — `NEXT_PUBLIC_*` secrets
4. `infra/railway.json` ekle/güncelle — Railway deploy config (Dockerfile path, start command, healthcheck)
5. `infra/vercel.json` ekle/güncelle — Vercel deploy config (build/output, env vars list)
6. `scripts/preflight-check.sh` yaz — required env vars'ın hepsi set mi diye script
7. `scripts/dr-drill.sh` yaz — Supabase snapshot restore staging'e

### Aşama B: Kullanıcı action (Sn / Aydoğan oturum-dışı)

> ⚠️ Bu aşama Claude'un erişimi olmayan üretim sistemlerine kurar. Önce A bitsin, sonra kullanıcı şu çağrıları yapar:

1. **Supabase production projesi:** `bluedev-grantwriter-prod` (EU/Frankfurt). `supabase link --project-ref <ref>` + `supabase db push` ile 13 migration apply et. RLS'i doğrula.
2. **Resend domain verify:** `bluedev.dev` (veya seçilen sender) için DKIM/SPF/DMARC eklenir, ~1 saat bekle, test mail gönder.
3. **Iyzico merchant:** Prod merchant onayı + webhook URL = `https://api.bluedev.dev/api/v1/billing/iyzico-webhook` + signature secret kayıt
4. **Sentry org + project:** `bluedev` / `grantwriter-api` + DSN
5. **Logtail (Better Stack):** source `grantwriter-api-production` + token
6. **Railway:** Yeni project + secret matrisinin tamamı (`docs/sprint-4-prep.md` §1 tablosu). Dockerfile path = `apps/api/Dockerfile`. Start command: `SENTRY_RELEASE=$RAILWAY_GIT_COMMIT_SHA uvicorn src.main:app --host 0.0.0.0 --port $PORT`
7. **Vercel:** Repo bağla, root dir = `apps/web`, env = NEXT_PUBLIC_*. Custom domain = `app.bluedev.dev`.

### Aşama C: Post-deploy verification (Claude yapabilir)

1. `scripts/preflight-check.sh` ile Railway + Vercel env'inin tamamı set mi doğrula
2. `https://api.bluedev.dev/health` → 200 + version
3. `https://api.bluedev.dev/health/sentry-test` → 500 (event Sentry'de görülmeli, scrubbed canary)
4. `https://app.bluedev.dev` → Supabase signup test (yeni user yarat)
5. DR drill: Supabase snapshot → staging restore → migration consistency check
6. End-to-end smoke: signup → BYOK store → invite → accept → checkout (sandbox) → generate (kısa proposal) → export

### Aşama D: Monitoring + alerts (Claude yapabilir)

1. Sentry alert kuralları — error rate > X / minute → Slack ping
2. PostHog frontend entegrasyonu (`@bluedev/web` paketine `posthog-js`) — `NEXT_PUBLIC_POSTHOG_KEY` secret
3. Better Stack uptime monitor: `https://api.bluedev.dev/health`, `https://app.bluedev.dev` her 1 dakikada bir, fail → email + Slack
4. Sentry release tracking confirm — deploy sonrası first event'in `release` tag'i set mi

---

## Açık ucu kalan iş kalemleri (Sprint 4 backlog)

1. **Saga otomatik snapshot hook** — Sprint 4'te küçük PR. Saga complete → otomatik `POST /versions` (comment="auto-snapshot after generation"). Mevcut manual versioning'i tetikler.
2. **Onboarding flow (post-signup tenant provisioning)** — `(app)/onboarding/page.tsx` stub'ı zaten var; gerçek 2-sayfa wizard (workspace name + billing setup) yazılacak. Day 16 Sn track.
3. **Real proposal editor (TipTap)** — `/proposals/[id]` şu an stub; gerçek TipTap editör Sprint 5+ kapsamında. Provenance metadata her sentence'a yazılması gerek (docs/06 + docs/09 §4).
4. **Frontend Playwright E2E** — `pnpm test` şu an Vitest unit + a11y; user-flow E2E (login → BYOK → invite → checkout → generate) Sprint 4 backlog.
5. **Resend webhook (delivery + bounce)** — Faz 2'den Sprint 4'e çekilebilir if pilot'larda email reputation sorunu olursa.
6. **Vercel preview env config** — Preview deploy'lar şu an env yok, build fail oluyor. Day 16 fix.

---

## İlk adım (yeni oturumda yapılacak)

1. **State doğrulama (5 dk):**
   ```bash
   cd <worktree veya repo root>
   git fetch origin main && git log origin/main --oneline -5
   gh pr list --state open
   gh issue list --state open
   docker ps  # bluedev-postgres + bluedev-redis healthy olmalı
   ```

2. **Sprint 4 plan dosyası oluştur:** `docs/sprint-4-day-16-plan.md` — yukarıdaki Aşama A/B/C/D'yi atomic step'lere böl. Her step için kim yapacak (Claude vs user), tahmini süre, prerequisites.

3. **Aşama A'dan başla:** `apps/api/.env.production.example` + `apps/web/.env.production.example` + `infra/railway.json` + `infra/vercel.json` + `scripts/preflight-check.sh`. Tek PR'da topla. CI yeşil olduğunda kullanıcıya "Aşama B'de senin secret setup'ına ihtiyacım var, X/Y/Z platformlarda şunu şunu yap" diye sunabilir hale gel.

4. **Test:** `apps/api/pyproject.toml` `[tool.pytest.ini_options]` veya `apps/api/tests/test_preflight.py` ile pre-deploy validation testi. Sprint 4 Day 17'de production'a gitmeden önce CI'da koşmalı.

---

## Beni başlatmak için yazacağın prompt (yeni oturumda)

> Sprint 3 backend + frontend + closure tüm aşamaları tamamlandı (main güncel). 5 PR merge edildi (#6 backend, #9 frontend, #8 CI fix, #10 docs, #14 TICKET-001/002/003 root-cause fix). 3 issue (#11/12/13) PR #14 ile kapatıldı.
>
> Şimdi Sprint 4 Day 16'ya başlıyoruz: production deploy + 2 pilot müşteri + ilk paying customer (Bluedev). `docs/sprint-4-handoff-prompt.md`'de yeni-oturum açılış brief'i var; oradaki "İlk adım" bölümünden başla.
>
> Aşama A'yı (pre-deploy validation kod tarafı: env example, railway.json, vercel.json, preflight script) tek PR'da bitir. Kullanıcı action gerektiren Aşama B'yi (Railway/Vercel/Supabase/Iyzico/Resend/Sentry production secret setup) atlatma; bana hangi platformda hangi adımı atmam gerektiğini sırayla sor — secret'ları ben elden ekleyeceğim, koddan otomatik almaman beklenmiyor.
>
> İlk önce: state doğrula (git/gh/docker), sonra `docs/sprint-4-day-16-plan.md`'yi yaz, sonra Aşama A'yı tek PR'da gönder.

---

## Risk / dikkat noktaları

1. **PR #14 merge sonrası main CI'ının yeşil olduğunu kontrol et** — `flaky_pre_s3` deselect kaldırıldı; eğer eski markeri import eden başka test varsa CI patlar.
2. **Railway start command'de `SENTRY_RELEASE` injection** — `$RAILWAY_GIT_COMMIT_SHA` var ama platform-specific; Railway docs ile teyit edip alternatifle ($GIT_COMMIT, vs.) fallback yaz.
3. **Iyzico sandbox vs production** — `IYZICO_BASE_URL` doğru ayarla. Test deploy sandbox, prod deploy `https://api.iyzipay.com`.
4. **Supabase RLS migration order** — `supabase db push` lexical sırayla apply eder; migration timestamp'ler 20260508*'ten 20260510140000'a kadar; sırada hata olmamalı ama check et.
5. **Vercel preview env eksik** — Bu sorun şu an PR'da fail gösteriyor. Day 16'da çöz, Sprint 4 sonuna kadar yaşatma.
6. **Pilot tarihi sıkı** — Day 17 Bluedev'in kendi pilot'u + Day 18 Pilot 2 + Day 19 onboarding. Production deploy Day 16 EOD'a kadar çalışmıyorsa pilot'lar kayar.

---

## Bu dosya nereden geldi

Sprint 3 closure'un son adımı olarak (TICKET-001/002/003 fix sonrası), Aydoğan istedi:

> "Sonrasında bir sonraki aşamayı planlayalım." + "bu oturum yetersiz olabilir başka bir oturumda devam etmek için gerekli promptu adım adım düşünerek yaz"

Bu dosya o promptun kalıcı kaydı. Yeni Claude oturumu açıldığında bu dosya okunduğunda Sprint 4 Day 16'ya başlamak için ek soru sormaya gerek kalmamalı.
