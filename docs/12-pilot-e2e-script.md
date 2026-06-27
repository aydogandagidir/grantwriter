# 12 — Pilot E2E Script (Manuel Uçtan Uca Koşu)

> **Amaç:** İlk gerçek pilot başvurusunu üretirken izlenecek adım-adım senaryo: signup → onboarding → brief → generate → draft → DOCX export → indirme + koşu sonrası doğrulama. `docs/11-operations-runbook.md` "bozulunca ne yapılır"ı anlatır; bu dosya "mutlu yol nasıl sürülür + nasıl kanıtlanır"ı.

> **Kapsam notu:** Bu senaryo *operatör eşliğinde* ilk koşular içindir (Sprint 4 Day 17). UI'daki generate paneli job-polling fallback'i (PR-4) ile ilerlemeyi kendisi gösterir; buradaki `curl` adımları otoriter doğrulama içindir ve PR-4 öncesi/SSE sorunlarında tek görünürlük yoludur.

---

## 0. Ön Koşullar (koşudan ÖNCE tamamla)

| # | Koşul | Nasıl doğrulanır |
|---|---|---|
| 1 | Tüm altyapı probe'ları yeşil | `bash scripts/pilot-readiness.sh` → **7/7** (worker dahil) |
| 2 | LLM anahtarları **hem web hem worker**'da | Render → iki serviste de `ANTHROPIC_API_KEY` + `OPENAI_API_KEY` + `LLM_MASTER_ENCRYPTION_KEY` dolu (ayrı env matrisleri — web'e girmek worker'a GİRMEZ) |
| 3 | Supabase Storage `exports` bucket'ı var (**private**) | Supabase → Storage; yoksa oluştur — migration YOK, manuel ön koşul |
| 4 | Taze test e-postası hazır | Supabase free SMTP confirmation maillerini ~3-4/saat ile sınırlar — aynı saatte çok signup planlama |
| 5 | Quota bilinci | Yeni tenant = `starter` plan, **3 generate/ay**; başarısız saga hakkı İADE ETMEZ → taze tenant'ta en fazla 3 deneme bütçele |
| 6 | Merge dondurması | Koşu sırasında main'e merge YOK — her merge worker'ı SIGTERM'ler; öldürülen saga ~1 saat sonra yeniden koşar (çift LLM masrafı). Bkz. runbook §5w-b |

---

## 1. Koşu Adımları

### 1.1 Signup + e-posta onayı
1. Tarayıcıda `https://grantwriter-gamma.vercel.app` → Kayıt ol.
2. Confirmation e-postasındaki linke tıkla → `/auth/callback` üzerinden oturum açılır.
3. **Beklenen:** Onboarding sihirbazına düşersin.

### 1.2 Onboarding (tenant otomatik kurulur)
1. Çalışma alanı adı + slug gir → dil seç → "Alanı oluştur".
2. **Beklenen:** Dashboard açılır. Tenant arka planda `plan='starter'`, `monthly_proposal_limit=3` (kolon default'u) ile oluşur — seed gerekmez.

### 1.3 Organizasyon profili
1. Sol menü → Organization → firma bilgilerini doldur, kaydet.
2. (Eligibility kontrolleri bu profili okur; boş bırakılırsa uyarı üretir, blocker değildir.)

### 1.4 Proposal + brief
1. Dashboard → "New proposal" → programme seç (ör. TÜBİTAK 1501 veya HE RIA), başlık gir. **Call seçmek zorunlu değil** — proposal call'suz oluşturulabilir.
2. Brief formunu doldur (proje özeti, hedefler, konsorsiyum/bütçe alanları) → kaydet → "Mark brief ready".

### 1.5 Generate (saga'yı tetikle)
1. Proposal sayfası → **Generate** → beklenen: 202 + "Generation enqueued" toast'u.
2. UI'daki ilerleme paneli job-polling ile durumu gösterir (PR-4 sonrası). **Bilinen sorun:** SSE canlı akışı tarayıcıda boş kalabilir — `EventSource` bearer header taşıyamadığı için stream 403'lenir; bu kozmetiktir, üretim worker'da sürer. Otoriter ilerleme = aşağıdaki job poll + Render worker logları.

### 1.6 Job'ı izle (otoriter yol — curl)
1. **Token al:** DevTools → Application → Local Storage → `sb-<ref>-auth-token` → kopyala → içindeki `access_token` değerini al.
2. Generate cevabındaki `job_id` ile (UI toast'unda / Network sekmesinde görünür):
```bash
TOKEN="<access_token>"
API="https://grantwriter-api.onrender.com"
curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/jobs/<JOB_ID>" | python -m json.tool
```
3. **Beklenen durum akışı:** `queued` → `running` (saga başladı; `task_track_started` sayesinde görünür) → 5-15 dk sonra `completed`.
4. **Hemen poll et:** Celery result'ları 1 gün sonra düşer (`result_expires`) — 24 saatten eski job id'si sonsuza dek `queued` okur.
5. Paralelde Render → `grantwriter-worker` → Logs: agent'ların sırayla koştuğunu görürsün (`saga_started`, `agent_completed` …).

### 1.7 Draft'ı doğrula
1. Proposal sayfasını yenile → **3 draft bölümü** (Excellence / Impact / Implementation) düzenlenebilir halde render olmalı.
2. Durum rozeti: `draft_complete` veya `draft_complete_with_issues` (ikisi de başarı — ikincisi compliance uyarısı taşır).

### 1.8 DOCX export + indirme
1. Draft görünümünde **Export DOCX** → toast job id verir.
2. UI'daki indirme bağlantısı job tamamlanınca belirir (PR-4b). Otoriter yol yine curl:
```bash
curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/jobs/<EXPORT_JOB_ID>" | python -m json.tool
# completed → result.signed_url alanındaki URL'i tarayıcıda aç
```
3. DOCX'i **Word'de aç** — bölümler, tablolar, biçimlendirme yerinde mi?
4. Supabase → Storage → `exports` → `tenant/<tenant_id>/proposal/<proposal_id>/…docx` nesnesi duruyor mu?

---

## 2. Koşu Sonrası SQL Doğrulamaları (Supabase SQL Editor)

```sql
-- 1) Quota bir kez yandı mı? (beklenen: 1, 3, 'starter')
select monthly_proposals_used, monthly_proposal_limit, plan
  from tenants where id = '<TENANT_ID>';

-- 2) LLM kullanımı loglandı mı? (beklenen: >= 1; saga başına birkaç satır normal)
select count(*) from tenant_usage_log where tenant_id = '<TENANT_ID>';

-- 3) Proposal durumu (beklenen: draft_complete veya draft_complete_with_issues)
select status, word_count, llm_cost_usd
  from proposals where id = '<PROPOSAL_ID>';
```

`<TENANT_ID>`'yi bilmiyorsan: `select id, name from tenants order by created_at desc limit 5;`

---

## 3. Troubleshooting (belirti → ilk bakılacak yer)

| Belirti | Muhtemel neden | Aksiyon |
|---|---|---|
| Job sonsuza dek `queued` | Worker ölü / KV restart kuyruk sildi / 24h+ eski job id | `curl …/health/worker` → runbook §5w-a sırası; gerekirse Generate'i yeniden tetikle |
| Generate butonu → **402** | Aylık quota bitti (3/3) | Yeni tenant aç veya ayı bekle; başarısız denemeler de sayar |
| Job `failed` + `RuntimeError: SUPABASE_URL…` | Worker env eksik (web'den ayrı!) | Worker → Environment → eksiği tamamla (matris: `infra/render.yaml`) → yeniden tetikle |
| Job `failed` + `Bucket not found` | `exports` bucket'ı yok | Ön koşul 3'ü uygula → yeniden Export |
| Job `failed` + asyncpg/connection hatası | Supabase paused (worker self-heal'siz) | Supabase'i restore et → yeniden tetikle (runbook §5w-c) |
| SSE paneli boş ama job ilerliyor | Bilinen EventSource-auth kısıtı | Kozmetik; job poll / PR-4 fallback durumu gösterir |
| Confirmation maili gelmedi | Supabase SMTP saatlik limit | ~1 saat bekle veya farklı saat planla |

---

**Son güncelleme:** 2026-06. İlk başarılı koşudan sonra gerçek süre/maliyet gözlemlerini (saga süresi, `llm_cost_usd`) bu dosyaya ekleyin — hedef: TÜBİTAK <$3, HE <$15 (bkz. CLAUDE.md maliyet tabloları).
