# EU Funding & Tenders Portal — Kaynak Analizi

> Faz 1: HE RIA (mevcut) + HE IA (yeni) scrape edilir.
> Faz 7: HE CSA, MSCA, ERC, EIC, Digital Europe, CEF, LIFE, Erasmus+, Creative Europe.

## API Durumu

### F&T Portal Search API (SEDIA)
- **Status:** AKTİF ve çalışıyor (2026-05-13 test edildi).
- **Endpoint:** `POST https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA&text=*&pageSize=N&pageNumber=N`
- **Auth:** `apiKey=SEDIA` query parametresi yeterli. OAuth/token YOK. Anonim, public scraper kullanımı için ideal.
- **Method:** Sadece `POST` (GET → 405). `Content-Type: application/x-www-form-urlencoded`. Body: `query` (ElasticSearch DSL) + `languages=en`.
- **Total corpus:** 644,023 result (tüm geçmiş çağrılar dahil).
- **Response shape (top-level):**
  ```
  { apiVersion, terms, responseTime, totalResults, pageNumber, pageSize, sort,
    groupByField, results: [...] }
  ```
- **Result item key fields** (`results[].metadata`):
  - `callIdentifier`, `callccm2Id`, `callTitle`
  - `frameworkProgramme` (HORIZON id = `43108390`, H2020 = `31045243`)
  - `type` (1 = topic), `typeOfMGAs`, `workProgrammepart`, `focusArea`
  - `keywords`, `es_SortDate`, `descriptionByte` (HTML)
  - `url` → topic-details JSON deep link
- **Pagination:** `pageSize` + `pageNumber` (max page size ~50 önerilir).
- **Rate limit:** Header'da görünür değil; deneyimsel olarak **dakikada ~60 istek güvenli**.

### Topic Details API
- **Endpoint:** `GET https://ec.europa.eu/info/funding-tenders/opportunities/data/topicDetails/{topic-id-lowercase}.json`
- **Önemli:** Identifier **lowercase** kullanılmalı (büyük harfli 404 verir). `HORIZON-CL4-2026-DIGITAL-EMERGING-01-12` → `horizon-cl4-2026-digital-emerging-01-12.json`.
- **JSON shape:** `TopicDetails.{ccm2Id, identifier, title, callIdentifier, frameworkProgramme{id,abbreviation}, programmeDivision[], topicMGAs[], keywords[], tags[], sme(bool), actions[{status{id,abbreviation}, types[{typeOfAction, typeOfMGA[]}], plannedOpeningDate, deadlineDates[], submissionProcedure}], latestInfos[]}`
- **Status codes:** `31094501` = Forthcoming, `31094502` = Open, `31094503` = Closed.
- **Type of action string:** `"HORIZON-RIA"`, `"HORIZON-IA"`, `"HORIZON-CSA"`, `"HORIZON-COFUND"` vs. (string regex ile sub-type ayrılabilir).

### CORDIS API
- **Public search API:** `cordis.europa.eu/api/search/europa?...` → 401 (login wall). Anonim API erişimi YOK.
- **Alternatif (önerilen):** Bulk dataset, anonim, JSON/XML/CSV/XLS:
  - https://data.europa.eu/data/datasets/cordis-eu-research-projects-under-horizon-europe-2021-2027 (aylık güncellenir)
- **SPARQL endpoint:** https://cordis.europa.eu/about/sparql (EURIO Knowledge Graph; distinctiveness scorer için kullanılabilir).
- **Tavsiye:** Distinctiveness scorer aylık güncellenen bulk JSON'u indirip pgvector'a embed etsin; arama runtime'ında local query yapsın.

## Scraping Stratejisi

- **API yeterli** — HTML fallback gerekmez. F&T Search API + Topic Details JSON tüm metadata + Specific Challenge/Scope/Expected Impact içerir.
- **Playwright KULLANMAYA GEREK YOK** birinci fazda. Tüm public bilgiler `httpx` async ile çekilebilir.
- **Frekans:** Günlük 1×. Calls günde birden fazla değişmez. Cron 06:00 UTC önerilir (Brüksel sabah update'leri yakalar).
- **Çekim akışı:**
  1. Search API → tüm `frameworkProgramme=43108390` + `status∈{31094501,31094502}` topic'lerin `identifier` listesi.
  2. Her identifier için topic-details JSON fetch (concurrency 5, exponential backoff 429/5xx).
  3. Diff'le local DB; yeni/değişen topic'leri queue'ya at, embedding + classification.
- **Edge case:** PDF ekler (work programme, application form) `latestInfos[].content` HTML içinde href olarak gelir; ayrı download worker.

## Programlar (İlk fazda dahil)

### Horizon Europe RIA *(mevcut, doğrulama)*
- **Type of action string:** `HORIZON-RIA`
- **Funding rate:** 100% (tüm eligible cost)
- **TRL:** 2-5 tipik
- **Part B (sections 1+2+3) page limit:** **45 sayfa** (lump sum topic'lerde 50)
- **Konsorsiyum:** min 3 bağımsız tüzel kişi, 3 farklı EU/Associated ülkeden, en az 1 EU üyesi
- **2026-2027 template güncel** (v5.0+, blind evaluation expansion)
- **programme_id:** `horizon_eu_ria`

### Horizon Europe IA — *(Faz 5 hedefi)*
- **Type of action string:** `HORIZON-IA`
- **Funding rate:** **70%** for-profit, **100%** non-profit entities
- **TRL band:** 5-8 (Faz başlangıç TRL 5-7 → bitiş TRL 8 tipik; gerçek değerler topic'te `descriptionByte` HTML içinde belirtilir, regex ile parse)
- **Bütçe (typical per project):**
  - Çoğu IA: **€4-8M**
  - Büyük data/AI IA topic'leri: **€12.5-25M** (örn. HORIZON-CL4-2026-04-DATA-06)
- **Süre:** 24-48 ay (36 ay medyan)
- **Konsorsiyum:** RIA ile aynı (min 3 ülke, 3 bağımsız entity). **SME zorunluluğu YOK** (ama çoğu IA topic'te beklenir; topic-specific `sme:bool` field topic JSON'da mevcut)
- **Application form:** **RIA ile AYNI template** — "HE Standard Application Form (HE RIA, IA)" v5.0 (4-Apr-2025)
  - PDF: https://ec.europa.eu/info/funding-tenders/opportunities/docs/2021-2027/horizon/temp-form/af/af_he-ria-ia_en.pdf
- **Part B page limit:** **45 sayfa** (lump sum topic'lerde **50**). RIA ile özdeş.
- **Bölümler:** Part A (sistem doldurur) + Part B (1. Excellence, 2. Impact, 3. Implementation, Section 4-6 page limit dışı: Members, Ethics, Security)
- **Evaluation:** 3 kriter (Excellence/Impact/Implementation), her biri 0-5, **threshold = 3** her birinde, **overall threshold = 10/15**. IA'da Impact ağırlığı tipik olarak diğerleriyle eşit (1.0/1.0/1.0).
- **AI Disclosure (page 32 HE SAF):** **RIA ile özdeş**.
- **Submission:** SEDIA portal, eIDAS dijital imza koordinatöre, partner'lar PIC (Participant Identification Code) ile
- **programme_id:** `horizon_eu_ia`

## Programlar (Faz 7'ye Bırakıldı)

- **HE CSA** (Coordination & Support Action): `HORIZON-CSA`, %100 funding, küçük bütçe (€1-3M), 30-sayfa Part B. **Template farklı** → ayrı modül.
- **MSCA** (Marie Curie): Doctoral Networks, Postdoctoral Fellowships, Staff Exchanges, COFUND. **Template tamamen farklı**, lump sum tabanlı.
- **ERC** (Starting/Consolidator/Advanced/Synergy): Single-PI, B1+B2 ayrı PDF, çok özel bölümler. Yüksek karmaşıklık.
- **EIC** (Accelerator + Pathfinder): Pathfinder → SAF benzeri. Accelerator → tamamen ayrı, AI-driven evaluation, business plan ağırlıklı.
- **Digital Europe Programme (DIGITAL):** F&T API'de aynı endpoint'te, farklı `frameworkProgramme.id` (`43152860` civarı)
- **CEF, LIFE, Erasmus+, Single Market, EU4Health, Citizens Equality Rights:** Hepsi aynı SEDIA endpoint'inde mevcut. Şablonlar çok farklı.

## Scraper Notları

- **Topic ID regex:** `^(HORIZON|H2020|DIGITAL|CEF|LIFE|ERASMUS|EU4H|CERV|SMP|CREA)-[A-Z0-9\-]+$` — uppercase storage, API call'da lowercase'e cast.
- **Type-of-action sub-type extraction** (IA filtresi için):
  ```python
  TOA_RE = re.compile(r"HORIZON-(RIA|IA|CSA|COFUND|EIC|ERC|MSCA)")
  ```
  `actions[*].types[*].typeOfAction` field'ından.
- **Application form PDF (RIA + IA shared):** 5.5 MB; `pdfplumber` öner.
- **Sürüm değişimi:** `latestInfos` içindeki `lastChangeDate`'i izle; PDF hash'i değiştiğinde re-extract.
- **Topic budget extraction:** `descriptionByte` HTML'inden `"€\s*[\d.,]+(?:\s*million|M)"` regex.
- **TRL extraction:** `descriptionByte`'da "TRL X" / "TRL X-Y" patternleri; topic-level structured field YOK, fallback narrative.
- **Two-stage detect:** `actions[]` array'inde 2+ entry varsa veya `callIdentifier` `-two-stage` suffix taşıyorsa.

## Belirsiz/Eksik Bilgi

- **F&T API rate limit:** Resmi olarak yayımlanmamış. Conservative 60 req/min + retry-after honor.
- **F&T API stability SLA:** Komisyon yayınlamıyor; production'da circuit-breaker zorunlu.
- **CORDIS public anonymous API:** YOK. data.europa.eu bulk + SPARQL.
- **HE IA için per-topic min SME zorunluluğu:** Topic-level (`sme:bool` flag). Cross-call genelleme yapılamaz.
- **Topic JSON schema versioning:** API versiyonu `2.146`; backward-compat ama silinme garantili değil. Pydantic strict + ignore-unknown öner.
- **Submission portal API:** YOK. Otomatik submit faz dışı.

## Kaynaklar
- https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/support/apis
- https://ec.europa.eu/info/funding-tenders/opportunities/docs/2021-2027/horizon/temp-form/af/af_he-ria-ia_en.pdf
- https://www.rvo.nl/sites/default/files/2025-04/HE%20Annotated%20Template%20RIA-IA%20%20(version%205.0)%204-4-2025%20FINAL.pdf
- https://data.europa.eu/data/datasets/cordis-eu-research-projects-under-horizon-europe-2021-2027
- https://cordis.europa.eu/about/sparql
- https://hadea.ec.europa.eu/news/horizon-europe-2026-industry-calls-now-published-2025-12-19_en
