# Program Source Index

> Bluedev GrantWriter — kaynak araştırması ve faz haritalaması.
> Bu klasör 2026-05-13 itibarıyla parallel web research ile derlendi.
> Her dosya bir kaynak (funder/aggregator) için scraper + program modülü geliştirmenin başlangıç haritasıdır.

## Kaynak listesi

| Kaynak | Dosya | Açık çağrı API | Web kazıma | Faz | Birincil personalar |
|---|---|---|---|---|---|
| **EU Funding & Tenders Portal** | [eu_ft_portal.md](./eu_ft_portal.md) | ✓ (SEDIA Search API) | gerekmiyor | F1 (RIA mevcut, IA bu fazda) | AB araştırmacı / KOBİ |
| **NLnet** | [nlnet.md](./nlnet.md) | RSS/Atom | hafif HTML scrape | F1 + F5 (yeni modül) | Bireysel açık-kaynak geliştirici |
| **Cascade Funding (Sploro hub)** | [cascade_funding.md](./cascade_funding.md) | yok | HTML scrape + Playwright fallback | F1 (mevcut modül genişletilir) | KOBİ |
| **TÜBİTAK** | [tubitak.md](./tubitak.md) | yok | HTML scrape (Drupal) | F1 + F5 (1601, 1512, 1071, 2244) | Türk KOBİ + üniversite |
| **KOSGEB** | [kosgeb.md](./kosgeb.md) | yok | HTML scrape (ASP.NET, statik) | F1 + F5 (Kapasite, Küresel Rekabetçilik, vd.) | Türk KOBİ |
| **Eurostars (EUREKA / TÜBİTAK 1709)** | [eurostars.md](./eurostars.md) | yok (myeurekaproject auth-gated) | HTML scrape | F5 (yeni modül) | KOBİ konsorsiyumu |
| **Schumann Associates** | [schumann.md](./schumann.md) | yok | RSS/HTML scrape (trend aggregator) | F7 (referans) | Trend araştırması |

## Faz Haritalaması

### Faz 1 (Call Discovery) — bu kaynaklardan scrape edilecek
- EU F&T Portal (HE RIA + IA + tüm açık framework topic'leri)
- NLnet (NGI0 Commons + NGI Taler + NGI Fediversity aktif)
- Cascade Funding (Sploro hub + Fundingbox cross-check)
- TÜBİTAK (1501, 1507, 1505)
- KOSGEB (yönlendirme aware: KOSGEB→TÜBİTAK redirect davranışı)

### Faz 5 (Yeni Program Modülleri) — bu kaynaklardan yeni `programs/<id>/` modülleri
- **NLnet** ayrı program modülü (`apps/api/src/programs/nlnet/`) — 3 alt-fund'ı tek modülde
- **Horizon Europe IA** (`apps/api/src/programs/horizon_eu_ia/`) — RIA modülünün kardeşi
- **Eurostars** (`apps/api/src/programs/eurostars/`) — tek aşamalı SME konsorsiyum
- **TÜBİTAK 1601** (kapasite/yenilik), **TÜBİTAK 1512** (BiGG), **TÜBİTAK 1505** (üniv-sanayi)
- **KOSGEB Kapasite Geliştirme** + **Küresel Rekabetçilik** (KOBİGEL halefi)
- **KOSGEB Stratejik Ürün**, **Dijital Dönüşüm**, **Yeşil Sanayi**

### Faz 7 (Long-tail) — sonra
- HE CSA, MSCA, ERC, EIC (Accelerator + Pathfinder)
- Digital Europe Programme, CEF, LIFE, Erasmus+
- TÜBİTAK 1071 (uluslararası), 2244 (sanayi doktora), 1709 (Eurostars koordinatörlük) — bunlar Eurostars modülüne ek/altta olabilir
- Schumann Associates trend aggregator
- Kurumsal vakıflar, regional development banks, vb.

## Kritik Bulgular (mimari etkili)

1. **KOSGEB AR-GE bağımsız değil**: Şu an "kosgeb_arge" REGISTRY entry'si gerçekte TÜBİTAK 1501/1507'ye yönlendiriyor. Bu kavramsal hata Faz 5'te düzeltilmeli — KOSGEB'in gerçek bağımsız programları: Kapasite Geliştirme, Küresel Rekabetçilik, vd.
2. **NLnet NGI0 Core/Entrust KAPALI** (Oct 2024 / Jan 2026). Aktif olan: NGI0 Commons, NGI Taler, NGI Fediversity — üçü de aynı `BaseProgramModule` altında ama `agency_id` ile ayrılır.
3. **Cascade Funding tek bir program değil** — HE FSTP (Financial Support to Third Parties) mekanizması. Sploro hub aggregator. Bizim modülümüz **router** — call metadata'sından runtime'da alt-template seçer.
4. **HE RIA + IA aynı template** kullanır (HE Standard Application Form v5.0, 45 sayfa Part B). Yazıcı ajanlar şablonu paylaşır; IA modülü RIA'dan inherit edebilir, sadece evaluation + funding rate farkı.
5. **Eurostars tek aşamalı** — Stage 1/2 yok. Plan ilk taslağında iki aşamalı yazılmıştı, düzeltilmeli.
6. **TÜBİTAK PDF'leri bazen tarama imajı** — OCR fallback (Tesseract Türkçe) gerekecek.

## Scraper Ortak Notları

- **User-Agent zorunlu** her HTTP isteğinde: `BluedevGrantWriter/1.0 (+contact@bluedev.io)`
- **Rate limit defensive**: 1 req/sec genel; EU F&T API biraz daha cömert (60/dakika önerilir)
- **Türkçe encoding**: PDF parse'da OCR + character set; HTML scrape'de UTF-8 zaten standart
- **PDF download → Supabase Storage**: orijinal kopyayı sakla, link rot'a karşı dayanıklılık
- **Robots.txt**: her kaynak için saygılı; commercial scraping engelleyici terimler için legal review
- **Caching**: `If-Modified-Since` / ETag (KOSGEB destekliyor) ile cost azalt
