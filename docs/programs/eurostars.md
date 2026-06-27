# Eurostars 3 (EUREKA) — Kaynak Analizi

> Faz 5: yeni program modülü.
> **Kritik:** Plan ilk taslağında Stage 1/2 ayrımı vardı — Eurostars **TEK aşamalı**. Stage ayrımı YOK.

## Resmi Kaynaklar

- **Ana program sayfası:** https://www.eurekanetwork.org/programmes-and-calls/eurostars/
- **Submission platform (eski `eurostars-eureka.eu` → `myeurekaproject.org`):** https://myeurekaproject.org/
- **Call 10 (Mart 2026):** https://myeurekaproject.org/competition/26/overview — deadline **19 March 2026 14:00 CET**
- **Call 11 (Eylül 2026):** https://www.eurekanetwork.org/programmes-and-calls/eurostars/eurostars-call-for-projects-september-2026/ — açılış 9 Temmuz 2026, deadline **10 September 2026 14:00 CET**
- **Resmi DOCX şablonu:** https://eurekanetwork.org/app/uploads/eurostars-application-form.docx
- **How-to-complete kılavuzu (PDF):** https://eurekanetwork.org/app/uploads/how-to-complete-eurostars-application.pdf
- **Türkiye (TÜBİTAK 1709) ana sayfası:** https://tubitak.gov.tr/en/funds/industrial/international-support-programs/1709-eureka-eurostars
- **TÜBİTAK 2026/1 ulusal çağrı (PDF):** https://tubitak.gov.tr/sites/default/files/2025-12/Eurostars_Koordinatorluk_Destegi_Programi_2026-1_Ulusal_Cagri_Duyurusu.pdf

## Program Yapısı

- **Tek aşamalı** (single-stage). Stage 1 / Stage 2 ayrımı **YOK** — tüm form bir kerede gönderilir.
- **Form yapısı (Excellence, Impact, Implementation):** Horizon Europe ile aynı 3-kriter mimarisi. Detaylı word/char limitleri portalda dinamik; resmi DOCX şablonu offline draft için.
- **Cycle:** Yılda **2 cut-off** (Mart + Eylül). Rolling değil.
- **Türkiye dahil mi?** EVET — TÜBİTAK 1709 programı altında, 37 üye ülkenin biri.
- **Online platform:** myeurekaproject.org. Türkiye için ek olarak ulusal pre-registration **TÜBİTAK PRODIS**.

## Eligibility

- **Konsorsiyum:** Min **2 bağımsız ortak**, min **2 farklı Eurostars ülkesi**.
- **Lider:** **Eurostars üyesi ülkeden innovative SME** koordinatör (üniversite/araştırma kurumu lider olamaz, ortak olarak katılabilir).
- **SME tanımı:** EU SME definition (<250 employee, ≤€50M revenue veya ≤€43M balance sheet) — SME self-assessment zorunlu annex.
- **Bütçe/finansal kısıtlar:**
  - **Hiçbir tek ortak veya tek ülke proje bütçesinin %70'inden fazlasını oluşturamaz.**
  - **Eurostars-ülke SME'lerinin payı (subcontracting hariç) ≥ %50.**
  - TÜBİTAK 2026/1 ulusal call üst limit: **€2.5M toplam proje bütçesi**.
  - Country-specific tipik destek: **€100k–€500k subsidy**.
- **Süre:** ≤ **36 ay**.
- **Coğrafya — 37 Eurostars üyesi:** Austria, Belgium, Bulgaria, Canada, Croatia, Cyprus, Czech Republic, Denmark, Estonia, Finland, France, Germany, Greece, Hungary, Iceland, Ireland, Israel, Italy, Latvia, Lithuania, Luxembourg, Malta, Netherlands, Norway, Poland, Portugal, Romania, Singapore, Slovakia, Slovenia, South Africa, South Korea, Spain, Sweden, Switzerland, **Türkiye**, United Kingdom.

## Türkiye (TÜBİTAK 1709) Spesifik Kuralları

- **Lider:** Türk SME (KOBİ) koordinatör; unilateral başvuru kabul edilmez.
- **Destek oranı:** SME için **%75**, büyük ölçekli için **%60**.
- **Non-capital institutions (üniversite, araştırma merkezi):** Bütçenin max **%50**'si.
- **Ulusal takvim (2026/1):** Pre-registration **30 Mart 2026**, ulusal pre-submission **2 Nisan 2026**, uluslararası deadline **19 Mart 2026**.
- **NCP iletişim:** eurostars@tubitak.gov.tr

## Brief Schema İçin Alanlar (BaseProgramModule)

**Tek aşama — Stage ayrımı yok:**

- **General/Administrative:**
  - Project acronym, title (max ~200 char), abstract
  - Consortium: her ortak için (legal name, country, SME status, role coordinator/partner, contribution %)
  - Duration (months, ≤36), start date hedefi
  - Total budget (€), per-partner budget
  - TRL start → TRL end (typical Eurostars: TRL 5→7-8)
- **Excellence:** Innovation, technical challenge, applied knowledge novelty, technical achievability + risk
- **Impact:** Market size & access, competitive advantage, commercialisation plan (route to market, IP strategy), economic/environmental/societal impact
- **Implementation (Quality & Efficiency):** Work plan (WP/Task breakdown), Gantt, partner roles, cooperation added value, cost reasonableness, risk management
- **TR-spesifik ek alanlar:** TÜBİTAK PRODIS no, PI bilgisi, ulusal eş-finansman onayı

**Zorunlu annexler:**
- SME self-assessment (tüm SME ortakları)
- Commitment & Signature form (tüm ortaklar)
- Son 2 yıllık finansal raporlar (veya <2 yıl ise full business plan)
- CV özetleri (key personnel)
- TR ek: KOBİ beyannamesi, vergi/SGK borç yoktur, imza sirküleri

## Scraping Stratejisi

- **API:** **Resmi public API yok.** `myeurekaproject.org/api` auth-gated (HTTP 403).
- **Birincil scrape kaynağı — HTML:**
  - `https://www.eurekanetwork.org/programmes-and-calls/eurostars/` — aktif call listesi.
  - Liste sayfa selector: aktif call URL pattern: `/eurostars/eurostars-<month>-<year>/`
  - Her call detay sayfasında: opening date, deadline ("14:00 CET" string'i), call number.
- **RSS/feed:** Resmi RSS yok; HTML diff veya scheduled Celery task (haftalık).
- **Tamamlayıcı kaynaklar:**
  - `myeurekaproject.org/competition/<id>/overview` — public read-only sayfa (user-agent header gerekli).
  - TÜBİTAK announcement RSS: `tubitak.gov.tr/en/announcement` — tek ülkelik karşılık.
- **Önerilen module: `EurostarsScraper(BaseScraper)`** — `httpx` async + `BeautifulSoup4`. User-agent rotation gerekebilir (403 yaygın).

## DOCX Template

- **Resmi indirme URL:** https://eurekanetwork.org/app/uploads/eurostars-application-form.docx (`httpx` ile user-agent header'la çekilebilir)
- **Format:** Tek konsolide DOCX; tüm sections + char limit hint'leri.
- **Page limit yok klasik anlamda:** Karakter sınırı kullanılıyor — portal plain-text saydırıyor. Auto-format bullet/numbering 5-10 ekstra char saydırılıyor.
- **Section structure:** General info → Innovation & Excellence → Market & Impact → Implementation (consortium, work plan, budget) → Ethics + Annexes checklist.

## Evaluation

- **International independent review panel.**
- **3 kriter, eşit ağırlıkta (1-5 scale):**
  1. **Excellence** — innovation degree, technical challenge, achievability
  2. **Impact** — market size/access, competitive advantage, commercialisation, broader impact
  3. **Quality & Efficiency of Implementation** — consortium quality, cooperation added value, project management, cost reasonableness
- **Stage 2 yok**, business plan separately istenmiyor — ama Impact section'da "commercialisation plan" zaten business plan equivalent. <2 yıl finansal geçmişi olan startup'lar için **detailed investor business plan annex** tavsiye edilir.
- **Tipik success rate:** %25-30 aralığı (Eurostars-2 verisi).

## Diğer Programlardan Farklılıklar

- **HE RIA/IA'dan farkı:** Tek aşamalı; SME coordinator zorunlu; decentralized funding (her ülke kendi); EU F&T Portal **kullanılmaz** (myeurekaproject.org).
- **TÜBİTAK 1501'den farkı:** International consortium zorunlu; lingua franca İngilizce; PRODIS sadece ulusal validation.
- **Provenance tracking:** Karakter limitleri sıkı — AI-generated content'in plain-text karakter sayımı doğrulanmalı (frontend live counter).
- **Citation grounding:** Excellence bölümünde "state of the art" claim'leri Crossref/OpenAlex ile verify edilmeli.

## Bluedev Modül Önerisi

- Yeni klasör: `apps/api/src/programs/eurostars/`
- `BaseProgramModule` impl: `call_parser` (HTML scrape), `brief_form` (Zod yukarıdaki alanlar), `template` (auto-download DOCX), `validators` (consortium ≥2 country, SME coordinator, budget %70/%50 kuralları, duration ≤36ay).
- 7 agent içinden Horizon Europe Excellence/Impact/Implementation writer'ları büyük ölçüde reusable.
- TR-spesifik wrapper: `eurostars_tr` sub-module — TÜBİTAK PRODIS pre-submission validator + ulusal annex generator.

## Belirsiz / Eksik Bilgi

- **Section-specific karakter limitleri** — resmi how-to PDF'i 403, DOCX şablonu indirilerek parse edilmeli (`python-docx`).
- **Eurostars-3 resmi success rate verisi** (Call 1-10) — Eureka Annual Report 2025 bekleniyor.
- **Evaluation kriterlerinin numerik weight'leri** — public dokümanda eşit ağırlıklı görünüyor, ancak tie-break veya threshold ≥3.0/5 tüm kriterlerde olabilir.
- **myeurekaproject.org API spec** — public olmayabilir; NCP'ye (TÜBİTAK) direkt başvuru gerekebilir.
- **Şablonun versiyon değişim sıklığı** — Call 10 ↔ Call 11 DOCX değişti mi (CI'da MD5 check şart).

## Kaynaklar
- https://www.eurekanetwork.org/programmes-and-calls/eurostars/
- https://myeurekaproject.org/competition/26/overview
- https://eurekanetwork.org/app/uploads/eurostars-application-form.docx
- https://tubitak.gov.tr/en/funds/industrial/international-support-programs/1709-eureka-eurostars
- https://tubitak.gov.tr/sites/default/files/2025-12/Eurostars_Koordinatorluk_Destegi_Programi_2026-1_Ulusal_Cagri_Duyurusu.pdf
- https://www.catalyze-group.com/handbook-eureka-eurostars-2025/
