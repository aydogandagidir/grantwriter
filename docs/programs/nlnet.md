# NLnet — Kaynak Analizi

> Faz 1: scraper. Faz 5: ayrı program modülü.
> **Kritik:** NGI0 Core ve NGI0 Entrust **KAPALI**. Aktif: NGI0 Commons, NGI Taler, NGI Fediversity.

## Resmi Kaynaklar

- **Ana site:** https://nlnet.nl/
- **Atom feed (haberler + call duyuruları + sonuçlar):** https://nlnet.nl/feed.atom (geçerli Atom 1.0)
- **Başvuru portali:** https://nlnet.nl/propose/ (tek-sayfa online form, hangi fund hedeflendiği dropdown ile)
- **Haber URL pattern:** `/news/YYYY/YYYYMMDD-{slug}.html` (örn. `20260409-announce-commons-fund.html`, `20260401-call.html`)

## Sub-programlar (agency_id haritası)

| agency_id | name | budget_band (EUR) | cycle | individual_eligible | status (May 2026) |
|---|---|---|---|---|---|
| `nlnet_ngi0_core` | NGI0 Core | 5.000–50.000 (scale-up mümkün) | 2 ayda bir, rolling | Evet | **KAPALI** — yeni başvuru alınmıyor |
| `nlnet_ngi0_entrust` | NGI0 Entrust | aynı 5k–50k bandı | aynı 2 aylık ritim | Evet | **KAPALI** — Aug 2022–Jan 2026, 242 proje fonlandı |
| `nlnet_ngi0_commons` | NGI0 Commons Fund | İlk başvuru max **50.000**, per-proposal max **150.000**, lifetime max 3rd party **500.000**. Program €21.6M (2024-01-01 → 2027-06-30) | 2 aylık rolling; **1 June 2026, 12:00 CEST** (13. call). Geçmiş: Feb 1, April 1, June 1, Aug 1, Oct 1, Dec 1 | Evet (bireyler, şirketler, NGO'lar, akademi) | **AÇIK** |
| `nlnet_ngi_taler` | NGI Taler | 5.000–50.000 | 2 aylık rolling, **1 June 2026** (13. call) | Evet | **AÇIK** (pilot) |
| `nlnet_ngi_fediversity` | NGI Fediversity | 5.000–50.000 | 2 aylık rolling, **1 June 2026** (11. call) | Evet | **AÇIK** (pilot) |
| `nlnet_ngi0_review` | NGI0 Review | hizmet vouchers (cash değil) | **31 July 2026** | Hayır — mevcut NGI grantee'ler | **AÇIK** (support service) |
| `nlnet_ngi_search` | NGI Search | — | — | — | nlnet.nl üstünden yönetilmiyor; ayrı modül |
| `nlnet_ngi_sargasso` | NGI Sargasso | — | — | — | EU-US işbirliği consortium; ayrı modül |

## Eligibility (3 aktif fund için tutarlı)

- "Clear European Dimension" zorunlu — kesin EU-only değil ama AB'ye yarar göstermek lazım
- Birey/şirket/NGO/üniversite/topluluk tümü uygun
- Tüm software/hardware **tanınmış open-source lisansla** yayınlanmak zorunda; bilimsel çıktılar open-access

## Application Format (single-page form, /propose endpoint)

Bölümler:
1. Contact info (ad, e-posta, telefon, organizasyon, ülke)
2. Proje (ad, URL, **abstract — projenin tamamı ve beklenen çıktılar**, prior experience, technical challenges, ecosystem & engagement)
3. Finans (talep edilen tutar EUR, kullanım, geçmiş/mevcut funding, task breakdown + effort tahminleri)
4. Call selection dropdown: NGI0 Commons / NGI Taler / NGI Fediversity / R&HE Tech Fund / Open Call / Other
5. Generative AI disclosure (kullanılan model bilgileri dahil — bizim provenance tracking ile birebir uyumlu)
6. Privacy ack, opsiyonel PGP key, max 50 MB ek dosya

**10 sayfa yok**; serbest-metin tek-form. Karakter limiti döküman çıkmıyor; submitter'a "abstract = the whole project" deniyor.

## Evaluation

İki aşamalı, ağırlıklı skor (geçer eşik: 7 üzerinden 5.0+):
- Technical excellence/feasibility — **%30**
- Relevance/Impact/Strategic potential — **%40**
- Cost effectiveness / value for money — **%30**

Aşama 2: expert questioning + independent verification → independent review committee onayı. Resmi karar duyurusu ~6-8 hafta sonra `/news/YYYY/YYYYMMDD-announce-*.html` patern'inde yayımlanır.

## Scraping Stratejisi

- **Primary:** `https://nlnet.nl/feed.atom` poll → `20\d{6}-call.html` ve `20\d{6}-announce-*.html` slug'lı entry'leri parse et. Call ilanı = yeni deadline; announce = funded list.
- **Secondary:** `/commonsfund/`, `/taler/`, `/fediversity/` sayfaları → HTML scrape (deadline + call-number extraction). Static HTML, BeautifulSoup yeterli.
- **Tertiary:** Funded projects listesi her announcement HTML içine nested. Her 2 ayda full crawl gerekli.

## Brief Schema Notu

Form yaklaşık 8 alan, 4-6 sayfa serbest metin. NLnet modülü brief form'unda şu alanlar:
- `project_name`
- `project_url`
- `abstract` (markdown, max ~5000 chars)
- `prior_experience`
- `technical_challenges`
- `ecosystem_engagement`
- `requested_amount_eur`
- `budget_breakdown` (task list with effort)
- `funding_history`
- `ai_disclosure` (model + usage — provenance log otomatik üretir)
- `target_fund` (enum: commons/taler/fediversity)

## Belirsiz/Eksik Bilgi

- **NGI Search & NGI Sargasso ayrıntıları:** NLnet altında değil, NGI consortium-yönetimli. Modüller `ngi.eu/opencalls/` üstünden ayrıca araştırılmalı.
- **NLnet cycle takvimi tam liste:** Sadece 13. call'un 1 June 2026 olduğu doğrulandı; 1 Feb / 1 April / 1 Aug / 1 Oct / 1 Dec varsayımsal — feed historical entry'leri ile validate edilmeli.

## Kaynaklar
- https://nlnet.nl/
- https://nlnet.nl/propose/
- https://nlnet.nl/commonsfund/guideforapplicants/
- https://nlnet.nl/feed.atom
- https://nlnet.nl/core/ (closed)
