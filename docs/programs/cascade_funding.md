# Cascade Funding — Kaynak Analizi

> **Kritik:** "Cascade funding" tek bir program **değil** — Horizon Europe altında **FSTP (Financial Support to Third Parties, Article 204)** mekanizması. Her HE project istediği formu, eligibility'yi, deadline'ı kendi kuruyor.

## Resmi Kaynaklar

- **Ana hub:** https://cascadefunding.eu/ — operatör **Sploro** (sploro.eu)
- **Açık çağrılar:** https://cascadefunding.eu/open-calls/
- **Aylık özetler:** `https://cascadefunding.eu/cascade-funding-{month}-{year}/` (örn. `/cascade-funding-march-2026/`)
- **Alternatif aggregator'lar (cross-validation):**
  - https://kaila.eu/blog/cascade-funding-calls-calendar/
  - https://eucalls.net
  - https://fundingbox.com/spaces/open-calls (FundingBox ayrı — Sploro değil)
  - https://eudis.europa.eu/eudis-tracks/cascade-funding_en
- **NGI sub-set primary source:** https://ngi.eu/opencalls/

## Alt-program haritası (FSTP via Horizon Europe)

Sploro hub yüzlerce sub-call'u aggregating ediyor:

| Cluster | Örnek programlar | Tipik budget/proje | Application platform |
|---|---|---|---|
| **NGI cascade** | NGI Sargasso, NGI Search, NGI TrustChain, NGI Mobifree, NGI Enrichers, NGI Zero Commons (NLnet) | €5k–€150k | Her project kendi platformu (FundingBox, ngi.eu, NLnet için /propose) |
| **EIT KIC cascade** | EIT Digital, EIT Climate-KIC, EIT Health, EIT Urban Mobility, EIT Manufacturing | €10k–€100k | KIC kendi portali |
| **EUREKA Cluster cascade** | ITEA, CELTIC-NEXT, Xecs, SMART | €25k–€500k consortium | EUREKA kendi portali |
| **DIH cascade** | European Digital Innovation Hubs network | €10k–€60k voucher | DIH bölgesel portal |
| **EUDIS cascade** | EU Defence Innovation Scheme | değişken | EUDIS portal |
| **Ad-hoc HE projects** | Open Horizons, EVEN-CLOSER, JARVIS, AIoD | €25k–€150k | Her biri ayrı, FundingBox yaygın |

**Standart EUSurvey yok** — Sploro/FundingBox/her-project-portal'a göre değişiyor.

## Eligibility (genel patern)

- %90+ **SME odaklı** (HE FSTP kuralı: tek-firma desteği)
- Birey nadir kabul edilir (NGI sub-call'ları istisna)
- Coğrafya: HE associated countries + AB-27 (**Türkiye dahil** — HE associated)
- Bütçe bandı: tipik **€25k–€150k**, equity-free grant

## Frequency

Her cascade kendi cycle'ı; merkezi takvim yok. Sploro hub aylık özet derliyor. Ortalama herhangi bir ayda 15-25 açık sub-call.

## Scraping Stratejisi

- Sploro `/open-calls/` server-rendered HTML kartları; her kart bir `<article>` veya `<div class="call-card">` (Sploro JS hydration kullanabilir, ihtiyatlı ol)
- **API yok** (Sploro yayımlamadı; ne JSON endpoint ne sitemap public)
- Aylık özet sayfaları (`/cascade-funding-{month}-{year}/`) statik HTML — daha kararlı entry-point
- **Fallback aggregator'lar:** Kaila, eucalls.net, FundingBox API (UI scrape gerekli)
- **En sağlam çoklu-kaynak yaklaşım:** Sploro + Kaila + ngi.eu/opencalls + eudis.europa.eu ayrı scraper'lar; tek normalized `CascadeCall` modeline merge

## Brief Schema (meta-platform yaklaşım)

Standart yok — Cascade Funding modülü **router**: kullanıcı bir cascade call seçer, sistem call'un parent project'e göre sub-template render eder.

**Generic alanlar:**
- `project_title`
- `problem`
- `solution`
- `team`
- `budget_breakdown`
- `timeline`
- `sme_profile` (TRL, employees, revenue)
- `expected_impact`

**Sub-call-specific custom fields** call metadata'sından gelmeli (`/open-calls/{slug}` sayfası kazınıp form alanları çıkarılmalı).

## Cascade Meta-Platform Mimari Önerisi

- Tek `cascade_funding` program modülü, **alt-modülleri runtime'da call metadata'sından discover et** (NLnet patern'i gibi her sub-program statik dosya değil)
- Brief form'unu call-by-call dinamik render et — call detay sayfasında "application format" alanını parse edip Zod schema'ya dönüştür
- Citation/provenance grounding hâlâ zorunlu

## Belirsiz/Eksik Bilgi

1. **Sploro/Cascade Funding Hub HTML stability:** JS-rendered olup olmadığı `curl` ile test edilmeli; SSR değilse Playwright gerekli.
2. **FundingBox vs Sploro:** İki ayrı kuruluş; FundingBox kendi platformunda da yüzlerce HE FSTP call host ediyor. Cascade modülümüz **her ikisini de** kapsamalı.
3. **EUREKA Clusters cascade gerçekten FSTP mi:** ITEA/CELTIC çoğunlukla konsorsiyum çağrıları; "cascade" terimi yanlış kullanılıyor olabilir. **Eurostars ayrı program modülü** (Faz 5), karıştırılmamalı.
4. **TR eligibility per call:** Cascade sub-call'ları tek tek HE Associated Country listesi check ediyor — programatik olarak her call için doğrulama gerekli; default ON varsayma.
5. **Application form length per cascade sub-call:** Tipik 8-15 sayfa ama Sploro sayfalarında metadata standardı yok; brief form generation runtime introspection ister.

## Kaynaklar
- https://cascadefunding.eu/
- https://cascadefunding.eu/open-calls/
- https://ngi.eu/opencalls/
- https://kaila.eu/blog/cascade-funding-calls-calendar/
- https://eudis.europa.eu/eudis-tracks/cascade-funding_en
- https://eucalls.net/blog/fund-innovation-cascade-funding-calls
