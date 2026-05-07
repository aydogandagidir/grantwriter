# 10 — Deployment ve DevOps

## 1. Hosting Mimarisi (Niye Bu Stack?)

Üç parçaya ayırıyoruz: **Vercel** (frontend), **Railway** (backend + worker), **Supabase** (DB + auth + storage). Bu üçleme şu nedenlerle seçildi:

- **Vercel** Next.js'in resmi hosting'i, App Router ve edge runtime için en olgun. Hobby tier free, Pro $20/ay yeterli (Faz 1).
- **Railway** Python workload için Render'dan daha hızlı build, daha basit secret management, GitHub PR preview environment otomatik. Fly.io'ya tercih sebebi: build cache stabil, support hızlı.
- **Supabase** PostgreSQL + auth + storage + realtime'ı tek noktadan veriyor; pgvector built-in. RLS first-class. Self-hosted alternatifi Faz 3'te değerlendirilir (compliance hassas müşteriler için).

Kubernetes kararlı şekilde reddedildi — 4 haftalık MVP'de k8s setup'ı tek başına 1 hafta yer. Faz 3'te ölçek gerekirse (1000+ aktif kullanıcı) AWS EKS'e geçiş planı var, ama ona kadar Railway yeterli.

### Servis ayrımı

| Servis | Hosting | Plan | Fiyat (Faz 1) |
|---|---|---|---|
| `web` (Next.js) | Vercel | Pro | $20/ay |
| `api` (FastAPI) | Railway | Hobby → Developer | $5-20/ay |
| `worker` (Celery) | Railway (ayrı service) | Hobby → Developer | $5-20/ay |
| `scheduler` (Celery beat) | Railway | Hobby | $5/ay |
| `postgres + pgvector + auth + storage` | Supabase | Pro | $25/ay |
| `redis` | Railway addon | 256MB | $10/ay |
| **Toplam** | | | **~$95/ay** |

İlk 100 kullanıcıda bu yeterli. 500 kullanıcıda Railway Developer plana geçeriz, Supabase Team plana yükseltiriz, ~$300/ay. Bunun ötesinde maliyet/ölçek analizi tekrar yapılır.

---

## 2. Environment Stratejisi

Üç ortam: **local** (geliştirici makinesi), **staging** (Railway preview branch'lerden), **production** (main branch).

```
local           — docker-compose, hot reload
staging.bluedev.dev  — main'e merge öncesi PR preview
app.bluedev.dev      — production
```

PR açıldığında Railway otomatik preview environment yaratır (subdomain: `pr-{number}-bluedev-api.up.railway.app`). Vercel da PR preview yapar. Bu ikisi birbirini bulması için PR preview'da `NEXT_PUBLIC_API_URL` Railway preview URL'ine point eder (preview deploy hook).

### Environment variables (kategorize)

```bash
# === Common ===
NODE_ENV=production
LOG_LEVEL=info

# === Database ===
DATABASE_URL=postgresql://...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=...                # frontend safe
SUPABASE_SERVICE_ROLE_KEY=...        # backend only, RLS bypass
SUPABASE_JWT_SECRET=...              # FastAPI uses this to verify JWTs

# === Redis ===
REDIS_URL=redis://...
CELERY_BROKER_URL=redis://...
CELERY_RESULT_BACKEND=redis://...

# === LLM ===
ANTHROPIC_API_KEY=sk-ant-...         # Bluedev managed
OPENAI_API_KEY=sk-...
LLM_MASTER_ENCRYPTION_KEY=...        # for BYOK pgcrypto

# === External APIs ===
CROSSREF_USER_AGENT=Bluedev GrantWriter (mailto:support@bluedev.dev)
EU_FT_PORTAL_API_KEY=                # blank, public API

# === Billing ===
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_STARTER=price_xxx
STRIPE_PRICE_PRO=price_xxx
STRIPE_PRICE_AGENCY=price_xxx
IYZICO_API_KEY=...
IYZICO_SECRET_KEY=...

# === Email ===
RESEND_API_KEY=re_...
EMAIL_FROM=noreply@bluedev.dev

# === Observability ===
SENTRY_DSN=https://...@sentry.io/...
POSTHOG_API_KEY=phc_...
LOGTAIL_SOURCE_TOKEN=...

# === Frontend ===
NEXT_PUBLIC_API_URL=https://api.bluedev.dev
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
NEXT_PUBLIC_POSTHOG_KEY=...
NEXT_PUBLIC_SENTRY_DSN=...
```

`NEXT_PUBLIC_*` prefix'i Next.js'in client-side bundle'a koymak istediğimiz değişkenler için. Bunların güvenli olduğunu doğrulamak Tech Lead'in PR review sorumluluğu — yanlış bir şey buraya konursa public.

---

## 3. Local Development Setup

`infra/docker-compose.yml`:

```yaml
version: "3.9"
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: bluedev
      POSTGRES_USER: bluedev
      POSTGRES_PASSWORD: dev
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  mailhog:                    # local SMTP catch-all
    image: mailhog/mailhog
    ports: ["1025:1025", "8025:8025"]

volumes:
  pgdata:
```

Geliştirici komutları (Makefile):

```makefile
dev:
	docker compose -f infra/docker-compose.yml up -d
	cd apps/api && poetry run uvicorn src.main:app --reload --port 8000 &
	cd apps/api && poetry run celery -A src.worker worker --loglevel=info &
	cd apps/web && pnpm dev

test:
	cd apps/api && poetry run pytest
	cd apps/web && pnpm test

lint:
	cd apps/api && poetry run ruff check . && poetry run mypy src
	cd apps/web && pnpm lint && pnpm typecheck

migrate:
	supabase db reset --linked

seed:
	cd apps/api && poetry run python scripts/seed_dev_data.py
```

`make dev` ile dakikalar içinde dev ortam ayağa kalkıyor. Bu sürtünmeyi düşürmek hafta 1 için kritik — ekibin günde 3-5 kez restart yapması normal, her restart 5 dakika sürerse hafta sonunda 2 saat kayıp.

---

## 4. CI/CD Pipeline

GitHub Actions. İki ayrı workflow: `test.yml` (her PR), `deploy.yml` (main'e merge sonrası).

### test.yml

```yaml
name: Tests
on:
  pull_request:
    branches: [main]

jobs:
  api-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_PASSWORD: test }
        ports: ["5432:5432"]
      redis:
        image: redis:7
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - name: Install Poetry
        run: pipx install poetry
      - name: Install deps
        working-directory: apps/api
        run: poetry install --with dev
      - name: Lint
        working-directory: apps/api
        run: poetry run ruff check . && poetry run mypy src
      - name: Apply migrations
        run: |
          npx supabase@latest db push --db-url postgresql://postgres:test@localhost:5432/postgres
      - name: RLS tests (CRITICAL)
        run: psql "$DATABASE_URL" -f infra/supabase/tests/rls_test.sql
      - name: Pytest
        working-directory: apps/api
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/postgres
          REDIS_URL: redis://localhost:6379/0
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY_TEST }}
        run: poetry run pytest --cov=src --cov-report=xml

  web-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v3
      - uses: actions/setup-node@v4
        with: { node-version: "20", cache: pnpm }
      - run: pnpm install --frozen-lockfile
        working-directory: apps/web
      - run: pnpm typecheck
        working-directory: apps/web
      - run: pnpm lint
        working-directory: apps/web
      - run: pnpm test
        working-directory: apps/web
      - run: pnpm build
        working-directory: apps/web

  e2e-tests:
    runs-on: ubuntu-latest
    needs: [api-tests, web-tests]
    if: github.event.pull_request.draft == false   # only non-draft PRs
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f infra/docker-compose.test.yml up -d
      - run: pnpm install --frozen-lockfile
      - run: pnpm exec playwright install --with-deps chromium
      - run: pnpm test:e2e
```

RLS testlerini ayrıca öne çıkarıyoruz — bu testin pass etmesi yeşil çubuk için zorunlu, security-critical.

### deploy.yml

```yaml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  deploy-api:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: railway/cli@v3
        with: { token: ${{ secrets.RAILWAY_TOKEN }} }
      - run: railway up --service api --detach

  deploy-worker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: railway/cli@v3
        with: { token: ${{ secrets.RAILWAY_TOKEN }} }
      - run: railway up --service worker --detach

  deploy-web:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: amondnet/vercel-action@v25
        with:
          vercel-token: ${{ secrets.VERCEL_TOKEN }}
          vercel-org-id: ${{ secrets.VERCEL_ORG_ID }}
          vercel-project-id: ${{ secrets.VERCEL_PROJECT_ID }}
          vercel-args: "--prod"

  apply-migrations:
    runs-on: ubuntu-latest
    needs: [deploy-api]
    steps:
      - uses: actions/checkout@v4
      - run: npx supabase@latest db push --db-url ${{ secrets.SUPABASE_PROD_URL }}

  smoke-test:
    runs-on: ubuntu-latest
    needs: [deploy-api, deploy-web, apply-migrations]
    steps:
      - run: |
          curl -f https://api.bluedev.dev/health || exit 1
          curl -f https://app.bluedev.dev || exit 1
```

Migration'lar deploy sonrası uygulanıyor — riskli ama Faz 1 için kabul edilebilir, çünkü zero-downtime migration karmaşıklığını şu anda almıyoruz. Backwards-compatible migration yazmaya özen gösteriyoruz (kolon ekle, asla drop etme; rename yapma; default değer ver). Faz 2'de blue-green deploy planı.

---

## 5. Migration Stratejisi

Supabase CLI kullanıyoruz. Migration dosyaları `infra/supabase/migrations/` altında, isimlendirme: `YYYYMMDDHHMMSS_descriptive.sql`.

Sprint 1'de uygulanacak ilk migration'lar (sırayla):

```
20260507120000_extensions.sql
20260507120100_tenants.sql
20260507120200_users.sql
20260507120300_programmes_seed.sql
20260507120400_calls.sql
20260507120500_proposals.sql
20260507120600_provenance.sql
20260507120700_citations.sql
20260507120800_rag_corpus.sql
20260507120900_usage_billing.sql
20260507121000_audit_log.sql
20260507121100_triggers.sql
20260507121200_rls_policies.sql
```

Her migration test'i geçmeli (RLS test suite included). Geri alma stratejisi: her migration'ın tersi `infra/supabase/migrations_down/` klasöründe (manuel, opsiyonel — uygulanmaz, sadece dokümantasyon).

Production migration'ı uygulamadan önce **mutlaka staging'de test edilir**. Bu PR review checklist'inde maddedir.

---

## 6. SSL ve DNS

Domain: `bluedev.dev` (Aydoğan'da kayıtlı). Subdomain'ler:
- `bluedev.dev` → marketing site (Vercel)
- `app.bluedev.dev` → uygulama (Vercel)
- `api.bluedev.dev` → API (Railway)
- `staging.bluedev.dev` → staging app (Vercel preview)
- `staging-api.bluedev.dev` → staging API (Railway preview)

DNS Cloudflare üzerinden. SSL otomatik (Vercel ve Railway built-in Let's Encrypt). Cloudflare proxy `api.` ve `app.` için açık (DDoS koruması, WAF), `staging.` için kapalı (kolay debug).

---

## 7. Observability Stack

### 7.1 Sentry (errors + traces)

Frontend ve backend için iki ayrı Sentry projesi. Backend için `sentry-sdk[fastapi]`, frontend için `@sentry/nextjs`.

Source maps Vercel build sırasında otomatik upload. Backend trace context her HTTP request için + Celery task için manuel propagasyon.

Alert kuralları:
- Error rate > 1% (5dk pencere) → Slack #alerts
- New issue (production) → Slack #alerts
- p95 latency > 2x baseline → Slack #performance

### 7.2 PostHog (product analytics)

Frontend event'leri:
- `page_viewed`, `proposal_created`, `brief_completed`, `generation_started`, `generation_completed`
- `citation_verified`, `export_downloaded`
- `plan_upgraded`, `byok_configured`

Backend event'leri (server-side PostHog):
- `agent_completed` (agent_id, duration, cost, tokens)
- `validation_run` (passed/failed, blocker_count)

PostHog dashboard'larda:
- Funnel: Çağrı görüntüleme → Brief başla → Brief tamamla → Generate başlat → Draft tamamla → Export
- Cohort: Hangi programa başvuranlar daha hızlı paying olur
- Feature flags (Faz 2 deneyleri için altyapı)

### 7.3 Logtail (logs)

Yapılandırılmış JSON log'lar, Logtail'a gönderilir. FastAPI middleware her request için:

```json
{
  "timestamp": "2026-05-07T12:34:56Z",
  "level": "info",
  "message": "request_completed",
  "method": "POST",
  "path": "/api/v1/proposals/abc/generate",
  "status": 202,
  "duration_ms": 145,
  "user_id": "...",
  "tenant_id": "...",
  "trace_id": "..."
}
```

Sentry trace_id ile join edilebilir (cross-system trace).

### 7.4 Better Stack (uptime)

5 dakikada bir health check:
- `https://app.bluedev.dev` (frontend)
- `https://api.bluedev.dev/health` (backend)
- Critical user journey synthetic test (Faz 2)

Outage durumunda PagerDuty entegrasyonu yok (henüz) — Slack #alerts yeterli, on-call rotasyonu Faz 2.

---

## 8. Backup ve Recovery

Supabase Pro plan günlük backup yapıyor + 7 gün point-in-time recovery. Bu Faz 1 için yeterli ama paranoyak olduğumuz için ekstra haftalık `pg_dump` + S3-compatible storage'a (Cloudflare R2):

```yaml
# Cron job (Railway scheduler)
- name: weekly-backup
  schedule: "0 3 * * 0"   # Pazar 03:00 UTC
  command: |
    pg_dump $DATABASE_URL | gzip | aws s3 cp - s3://bluedev-backups/$(date +%Y%m%d).sql.gz \
      --endpoint-url=https://xxx.r2.cloudflarestorage.com
```

Recovery drill: ayda bir (her ayın ilk Pazartesi), staging'e restore + smoke test.

---

## 9. Cost Monitoring

İki tür maliyet: **infra** ve **LLM**.

**Infra:** Vercel + Railway + Supabase'in dashboard'ları yeterli, manual review aylık.

**LLM:** kritik. Her LLM çağrı `tenant_usage_log` tablosuna kayıt giriyor. Günlük cron şu raporu üretip Slack'e atar:

```python
# scripts/cost_report.py — daily cron
SELECT
  date(created_at) as day,
  resource as model,
  count(*) as calls,
  sum(input_tokens) as input,
  sum(output_tokens) as output,
  sum(cached_tokens) as cached,
  sum(cost_usd) as cost
FROM tenant_usage_log
WHERE created_at >= now() - interval '7 days'
GROUP BY 1, 2
ORDER BY 1 DESC, cost DESC;
```

Anomali alarm:
- Daily LLM cost > $50 → Slack alert (Faz 1 baseline ~$10/gün)
- Tek tenant tek günde > $20 harcıyorsa → o tenant için detay rapor

---

## 10. Secret Management

Production secrets sadece Railway/Vercel/Supabase environment'larında, kimsenin local'inde yok. Geliştirici test için Anthropic test key'i kullanır (limit'li, ayrı hesap).

`.env` dosyası git'e dahil edilmez, `.env.example` template var (boş değerlerle).

Secret rotasyon planı:
- Anthropic API key: 6 ayda bir
- Supabase service role: yıllık
- Stripe webhook secret: değiştirilmez (Stripe dashboard'dan rotation gerekirse)
- LLM master encryption key: 6 ayda bir (custom rotation script — eski anahtarla decrypt et, yeni anahtarla encrypt et)

---

## 11. Rollback Prosedürü

Production deploy fail ederse:

1. **Vercel:** dashboard'dan tek tıkla previous deployment'a dön (atomik)
2. **Railway:** önceki deployment'ı redeploy et (~2 dk downtime)
3. **Migration rollback:** mümkünse forward fix (daha güvenli), aksi halde `migrations_down/` klasöründeki ters migration'ı manuel uygula

Migration rollback kuralı: kolon eklemiyorsa rollback otomatik OK; kolon ekleyen migration'ı geri almak için önce uygulamayı eski hale getir, sonra column drop. Asla production'da `DROP COLUMN`'u doğrudan çalıştırma.

---

## 12. Faz 1 Sonu Production Hazırlık Checklist

Sprint 4 son günü:
- [ ] Tüm CI yeşil (test + lint + typecheck + RLS + e2e)
- [ ] Sentry alerts kuruldu
- [ ] PostHog tracking doğrulandı (manual user journey)
- [ ] Logtail log akışı çalışıyor
- [ ] Better Stack uptime monitor 5 dakika başarılı
- [ ] Pen test scheduled (ya da yapıldı)
- [ ] DR drill 1 kez başarıyla yapıldı
- [ ] Backup cron çalışıyor, ilk backup S3'te doğrulandı
- [ ] DNS records doğru (CAA, SPF, DMARC dahil)
- [ ] SSL grade A (SSL Labs)
- [ ] CSP header production'da aktif
- [ ] Rate limiting test edildi (yapay 429)
- [ ] Stripe webhook test edildi (Stripe CLI)
- [ ] Mobile responsive smoke test (iPhone + Android)
- [ ] WCAG 2.1 AA kritik sayfalar otomatik test (axe)

---

**Sonraki dosya:** `sprints/sprint-roadmap.md` — günlük sprint planı.