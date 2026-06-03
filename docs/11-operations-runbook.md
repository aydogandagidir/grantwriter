# 11 — Operations Runbook (Production)

> **Amaç:** Production'da bir şey bozulduğunda ne yapılacağının adım-adım kılavuzu. `docs/10-deployment-devops.md` "nasıl deploy edilir"i anlatır; bu dosya "bozulunca ne yapılır"ı. Bu runbook'un büyük kısmı 2026-06 prod bring-up sırasında yaşanan gerçek incident zincirinden damıtıldı — her başlık bir kez gerçekten başımıza geldi.

> **Not (gerçek topoloji):** Faz 1 planı backend için Railway öngörüyordu (bkz. `10-deployment-devops.md`), ama production **Render**'da çalışıyor. Bu runbook deployed gerçeği yansıtır.

---

## 1. Production Topolojisi

| Katman | Servis | URL |
|---|---|---|
| Frontend | Vercel (`grantwriter` projesi) | `https://grantwriter-gamma.vercel.app` |
| Backend API | Render (`grantwriter-api`) | `https://grantwriter-api.onrender.com` |
| Database + Auth + Storage | Supabase (proje ref `amskzrdscxltcruqxaeb`) | `https://<ref>.supabase.co` |

- **Render free tier** kullanılıyor → instance idle'da spin-down olur; ilk istek cold-start tetikler (~10-50 sn). 503/timeout gördüğünüzde önce bunu eleyin.
- **Supabase free tier** → uzun inaktivitede proje **otomatik askıya alınır (paused)**; dashboard'dan manuel "Restore" gerekir.

---

## 2. Sağlık Probe'ları — Hangi Endpoint Neyi Söyler

İki ayrı health endpoint'i var; **farkları kritik**:

| Endpoint | Neyi doğrular | DB'ye dokunur mu |
|---|---|---|
| `GET /health` | Process ayakta, uvicorn cevap veriyor | ❌ Hayır |
| `GET /health/db` | DB pool açık + `SELECT 1` çalışıyor | ✅ Evet |

**Tanı için ikisini birlikte okuyun:**

| `/health` | `/health/db` | Yorum |
|---|---|---|
| 200 | 200 | Her şey sağlıklı. |
| 200 | 503 | Process ayakta ama DB ulaşılamıyor. `/health/db` gövdesindeki `init_error`/`runtime_error` nedeni söyler. → Bölüm 5. |
| 000 / timeout | — | Process hiç cevap vermiyor. Render cold-start (bekle) **veya** boot crash-loop **veya** suspended. → Bölüm 4. |

**Tek komutla tam tablo:**
```bash
bash scripts/smoke.sh
# backend /health, frontend /, frontend /icon.svg — üçü de ✓ olmalı
# DB durumu için ayrıca:
curl -s https://grantwriter-api.onrender.com/health/db | python -m json.tool
```

> `smoke.sh` env ile özelleştirilebilir: `API_URL=... FRONTEND_URL=... TIMEOUT=90 bash scripts/smoke.sh`.

---

## 3. İlk 60 Saniye — Hızlı Triyaj

1. `bash scripts/smoke.sh` çalıştır. Hangi katman kırmızı?
2. **Backend `/health` 000/timeout** ise → Bölüm 4 (process down).
3. **Backend `/health` 200 ama `/health/db` 503** ise → Bölüm 5 (DB down).
4. **Frontend kırmızı** ise → Vercel dashboard → son deployment durumu (READY/ERROR). Build log'a bak.
5. Her durumda Render **Logs** sekmesini aç; boot hatası buradadır.

---

## 4. Backend Process Cevap Vermiyor (`/health` → 000)

Olası nedenler, en olasıdan başlayarak:

### 4a. Render cold-start (en sık, çözüm: bekle)
Free tier idle sonrası ilk istek instance'ı uyandırır. **90 sn timeout ile** tek bir istek at:
```bash
curl -sSL -m 90 -o /dev/null -w "%{http_code} (%{time_total}s)\n" https://grantwriter-api.onrender.com/health
```
200 dönerse normaldi, bitti.

### 4b. Boot crash-loop (deploy "failed" veya sürekli restart)
Render → `grantwriter-api` → **Logs**. Aşağıdaki imzalar Bölüm 5'teki incident kataloğuna işaret eder. En sık üçü:

- `COPY ... .env.production.example ... not found` → **Docker build** patlıyor → 5d.
- `asyncpg.exceptions.*` / `ENOTFOUND tenant/user postgres.<ref>` → DB upstream → 5a/5b.
- `preflight: ... ERROR(s)` + `SystemExit` → strict preflight + eksik env → 5c.

### 4c. Render servisi suspended
Ardışık başarısız deploy veya uzun inaktivite sonrası Render servisi tamamen pasifleştirebilir → yeni commit'leri otomatik build etmez.
- Render → `grantwriter-api` → status badge **"Suspended"** ise → **"Resume Service"**.
- Veya **"Manual Deploy" → "Deploy latest commit"**.

---

## 5. Incident Kataloğu (gerçek yaşanmış failure modları)

> Bu beş madde, prod bring-up'ında art arda yaşandı. Her biri farklı bir katmanı vurur; çözümlerinin tamamı koda/altyapıya kalıcı olarak işlendi (PR #36–#39). Yine de belirtiyi tanımak için burada.

### 5a. `tenant/user postgres.<ref> not found` (Supabase paused VEYA yanlış ref)
**Belirti:** `/health/db` → 503, `init_error: "InternalServerError: (ENOTFOUND) tenant/user postgres.XXXX not found"`.
**Neden:** Supavisor (Supabase pooler) o ref'li projeyi tanımıyor. İki sebepten:
1. **Supabase projesi paused** (free tier auto-suspend). → Supabase dashboard → projeyi aç → **"Restore project"**.
2. **`DATABASE_URL` username'indeki `postgres.<ref>` ile `SUPABASE_URL`'deki ref farklı** (iki ayrı proje karışmış). → İkisinin ref'ini eşitle.
**Self-heal:** PR #39 sonrası, Supabase restore edildikten sonra **bir sonraki `/health/db` probe'u** pool'u otomatik yeniden kurar (30 sn cooldown'lı lazy retry). Manuel redeploy GEREKMEZ. Uptime monitor zaten periyodik probe ediyorsa kendiliğinden düzelir.

### 5b. `DATABASE_URL` host `[...]` içinde — urlsplit crash
**Belirti:** Boot'ta `ValueError: '...pooler.supabase.com' does not appear to be an IPv4 or IPv6 address`.
**Neden:** Supabase'in IPv6 "direct connection" şablonu host'u `@[...]:5432` köşeli parantezle sarar. Pooler hostname'i bu parantez içine yapıştırılırsa Python 3.11+ `urlsplit` onu IPv6 literali sanıp patlar.
**Çözüm:** `DATABASE_URL`'den host etrafındaki `[` `]`'yi sil. Doğru biçim:
```
postgresql://postgres.<ref>:<şifre>@aws-1-<region>.pooler.supabase.com:5432/postgres
```
**Kalıcı koruma:** `db.py::_normalize_dsn` yanlış parantezi otomatik ayıklar (gerçek IPv6 literallerini korur). Yine de doğru gir.

### 5c. `JWT verification not configured` / eksik env'ler
**Belirti (eski):** `/api/v1/me` veya onboarding → 503 `"JWT verification not configured ..."`. Veya strict preflight'ta boot `SystemExit`.
**Neden:** `SUPABASE_URL` **ve** `SUPABASE_JWT_SECRET` ikisi de boş; ya da diğer REQUIRED env'ler (bkz. Bölüm 6) eksik.
**Çözüm:** Render → Environment → eksik değişkenleri gir (Bölüm 6 matrisi). 
**Davranış:** Preflight artık **warn-by-default** (PR #36) — eksik env boot'u ÖLDÜRMEZ, sadece loglar. App ayakta kalır, ilgili route 503 verir. Tüm env'ler tamam olunca `PREFLIGHT_STRICT=true` ile sıkı kapıyı aç.

### 5d. Docker build: `.env.production.example not found`
**Belirti:** Render build `#21 [runtime] COPY ... .env.production.example ... not found` ile patlar; deploy hiç başlamaz.
**Neden:** `.dockerignore`'daki `.env.*` blanket exclude template dosyasını build context'ten siler; Dockerfile COPY onu bulamaz.
**Çözüm/koruma:** `apps/api/.dockerignore` içinde `!.env.production.example` exception var (PR #37). Bu satırı silmeyin.

### 5e. `<ref>` / `[YOUR-PASSWORD]` placeholder'ı bırakılmış
**Belirti:** Supavisor `tenant ... <ref> not found`, ya da auth hataları.
**Neden:** Secret matrisi şablondan kopyalanmış ama `<...>` placeholder'ları gerçek değerle değiştirilmemiş.
**Çözüm/koruma:** Preflight `<...>` substring'i yakalar ve (strict modda) net mesajla durur; (warn modda) loglar. Yine de Render Environment'ta hiçbir `<...>` kalmamalı.

---

## 6. Production Env Var Matrisi

Tam liste ve açıklamalar: `apps/api/.env.production.example` (tek doğruluk kaynağı; `test_preflight.py` Settings ile drift'i engeller).

**REQUIRED (preflight bunları izler):**
`APP_ENV=production`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `LLM_MASTER_ENCRYPTION_KEY`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `REDIS_URL`, `IYZICO_API_KEY`, `IYZICO_SECRET_KEY`, `IYZICO_WEBHOOK_SECRET`, `RESEND_API_KEY`, `SENTRY_DSN`, `LOGTAIL_TOKEN`.

**Onboarding'in çalışması için minimum:** `APP_ENV`, `SUPABASE_URL`, `SUPABASE_JWT_SECRET`, `DATABASE_URL`. (Auth + DB yeterli; LLM/Iyzico/Resend/Sentry sonraki dalga.)

**Preflight kontrol anahtarı:**
- `PREFLIGHT_STRICT` unset/`false` → **warn-only** (eksik env loglanır, boot devam eder). Faz'lı rollout için güvenli varsayılan.
- `PREFLIGHT_STRICT=true` → her eksik REQUIRED env boot'u durdurur. **Tüm matris tamamlandıktan sonra** açın (Sprint 4 Aşama D).

---

## 7. `DATABASE_URL` Doğru Biçim (Supabase)

```
postgresql://postgres.<proje-ref>:<DB-şifresi>@aws-1-<region>.pooler.supabase.com:5432/postgres
```

Kurallar:
- **Session pooler (port 5432)** kullanın — Render/IPv4 dostu. Direct connection (`db.<ref>.supabase.co`) IPv6-only; Render bağlanamaz.
- Host **`...pooler.supabase.com`** olmalı; etrafında **köşeli parantez OLMAMALI** (5b).
- `<proje-ref>` ve `<DB-şifresi>` gerçek değerler — hiç `<...>` kalmasın (5e).
- Şifrede özel karakter (`@ : / # %`) varsa **percent-encode** edin (`@`→`%40`).
- `<proje-ref>` = `SUPABASE_URL`'deki ref ile **aynı** olmalı (5a).
- En güvenlisi: Supabase Dashboard → **Connect → Session pooler** → URI'yi kopyalayıp sadece şifreyi doldurun.

---

## 8. Resilience Garantileri (ne otomatik, ne manuel)

Prod bring-up sonrası kazanılan davranışlar:

| Olay | Eski davranış | Şimdiki davranış |
|---|---|---|
| Eksik REQUIRED env | boot crash (strict) | warn-log + boot devam (warn-default) |
| DB upstream down (boot anında) | boot crash-loop | `db_pool=None` + app ayakta, `/health` 200, `/health/db` 503 |
| Supabase restore edildi | manuel redeploy gerek | `/health/db` probe'unda **otomatik self-heal** (30 sn cooldown'lı lazy retry) |
| `DATABASE_URL` yanlış parantez | urlsplit crash | `_normalize_dsn` otomatik ayıklar |
| Docker build template eksik | build fail | `.dockerignore` exception kalıcı |

**Hâlâ manuel gerektiren tek şey:** Supabase projesini **restore etmek** (free tier auto-suspend). Restore'dan sonrası otomatik.

---

## 9. Migration Uygulama

`DATABASE_URL` doğru ama tablolar yoksa (`relation "tenants" does not exist` / `type "vector" does not exist`):

**Önerilen — repo script'i (her CWD'den çalışır, CI ile aynı yol):**
```bash
# $1 = DSN, $2 = --strict (ilk hatada dur). auth_stub.sql + tüm
# infra/supabase/migrations/*.sql'i lexical sırada uygular. İdempotent.
bash apps/api/scripts/apply_migrations.sh "$DATABASE_URL" --strict
```
Bu, `.github/workflows/api-ci.yml`'in test DB'sini kurarken kullandığı script'in aynısı; çalıştığı bilinen yoldur.

**Alternatif — Supabase CLI** (production-paritetik, ama dikkat: bu repo'da `infra/supabase/config.toml` henüz commit'li DEĞİL, ve migration'lar default `supabase/` değil `infra/supabase/` altında — bu yüzden `cd infra` ve önce `supabase init` şart):
```bash
cd infra
supabase init                      # config.toml üretir (henüz repo'da yok)
supabase link --project-ref <ref>
supabase db reset                  # tüm migration'ları temiz DB'ye uygular
```
Detay: `infra/supabase/README.md`.

**Doğrulama:** Bölüm 2'deki public probe — `/api/v1/invitations/bogus-token` → **404** dönerse şema + JOIN tam. (503 = DB down, 500 = tablo yok.)

---

## 10. Deploy Sonrası Doğrulama Checklist

Her production deploy'dan sonra:
1. `bash scripts/smoke.sh` → 3/3 ✓.
2. `curl -s .../health/db` → `{"status":"ok","db":{"available":true}}`.
3. Public DB probe → `/api/v1/invitations/<bogus>` → 404 (full stack).
4. Render Logs → `db_pool_opened` veya warn-mode mesajı; `preflight` satırı.
5. (varsa) Sentry → yeni release tag'i göründü mü.

---

**Son güncelleme:** 2026-06. Prod bring-up incident zincirinden (PR #36–#39) damıtıldı. Yeni bir failure modu yaşanırsa bu kataloğa (Bölüm 5) ekleyin.
