# TÜBİTAK Programları — Kaynak Analizi

> Faz 1: 1501, 1507, 1505 scraper hedefi.
> Faz 5: 1601, 1512, 1071, 2244 ayrı modüller.

## Resmi Kaynaklar

- **Ana destek sayfası:** https://tubitak.gov.tr/tr/destekler/
- **Sanayi (TEYDEB) ulusal destek listesi:** https://tubitak.gov.tr/tr/destekler/sanayi/ulusal-destek-programlari
- **Akademik (ARDEB) listesi:** https://tubitak.gov.tr/tr/destekler/akademik/
- **Açık çağrılar listesi:** https://tubitak.gov.tr/tr/acik-cagrilar (TR) / https://tubitak.gov.tr/en/open-calls (EN)
- **Duyurular feed'i:** https://tubitak.gov.tr/tr/duyurular
- **Başvuru portalları:**
  - **PRODİS** (TEYDEB — 15xx/16xx/17xx/18xx): https://eteydeb.tubitak.gov.tr/
  - **ARBİS / ARDEB-PBS** (10xx/1071): https://ardeb-pbs.tubitak.gov.tr/
  - **UİDB-PBS** (uluslararası): https://uidb-pbs.tubitak.gov.tr/
  - **E-BİDEB** (burs/girişim): https://e-bideb.tubitak.gov.tr/
- **Duyuru PDF konvansiyonu:** `tubitak.gov.tr/sites/default/files/{YYYY-MM}/{program}-{cagri}-DuyuruMetni-{tarih}.pdf` (ör. `2026-02/1501-2026-1-DuyuruMetni-26.12.2025-v2.pdf`)
- **AGY100-101 form kılavuzu:** https://tubitak.gov.tr/sites/default/files/21566/proje_oneri_bilgileri_formu_hazirlama_kilavuzu_agy100-101.pdf

## Web Scraping Stratejisi

- **Açık çağrı listesi:** `/tr/acik-cagrilar` URL'inde HTML **card-list pattern** olarak listelenir (tablo değil). Her kart: program kodu (1501, 1707…), çağrı adı, son tarih (DD MMM YYYY), kategori. Detay sayfası URL şablonu: `/tr/destekler/{kategori}/{altkategori}/{program-kodu}-{slug}`.
- **JSON API yok**: TÜBİTAK public REST/JSON endpoint sunmuyor. HTML scraping zorunlu. Drupal CMS tabanlı.
- **Anti-bot**: 403 görülen bazı sayfalar var (`1512-girisimcilik-destek-programi-bigg` 403 döndü). User-Agent header şart, Cloudflare benzeri rate-limit muhtemel — **0.5–1 req/sec** ile sınırla, retry/backoff koy.
- **Periodicity**: TEYDEB ana programları (1501, 1507) **yılda 2 dönem** (Ocak–Mart, Temmuz–Eylül). 1505 ve BiGG (1612) **sürekli açık**. ARDEB 1071 çağrı-bazlı (yılda 5–10 çağrı, farklı uluslararası fonlara bağlı).
- **PDF duyurular ana veri kaynağı**: HTML sayfalar özet, **tam parametreler PDF duyurularında** (bütçe, süre, ön kayıt tarihi). PDF'ler bazen tarama imajı (1507 PDF binary) → **Tesseract OCR (Türkçe dil paketi) fallback** şart.

## Program Detay

### TEYDEB 1501 — Sanayi Ar-Ge Destek Programı *(mevcut)*
- **Eligibility:** Türkiye'de yerleşik **sermaye şirketleri** — KOBİ + büyük firma. Sektör kısıtsız.
- **TRL band:** ~2–7 (uygulamalı araştırma + geliştirme)
- **Bütçe:** **Üst limit yok** — firmanın mali kapasitesine orantılı. Destek oranı **%75 sabit** (geri ödemesiz).
- **Süre:** Max **36 ay**
- **Başvuru formatı:** **PRODİS** elektronik + AGY100-101 Proje Öneri Bilgileri Formu (DOCX kılavuz), e-imza
- **Çağrı takvimi:** Yılda 2 dönem. **2026/1**: Açılış 01.01.2026, ön kayıt son 09.04.2026, kapanış 13.04.2026
- **Başvuru sınırı:** Kuruluş başına dönem başına **max 2 proje**
- **Değerlendirme:** (1) Endüstriyel Ar-Ge içeriği/yenilik, (2) proje planı + altyapı, (3) ekonomik etki/ulusal kazanım. Hakem firma ziyareti yapar.
- **agency_id:** `tubitak_1501`

### TEYDEB 1507 — KOBİ Ar-Ge Başlangıç *(mevcut)*
- **Eligibility:** Sadece **KOBİ ölçekli sermaye şirketleri**, Türkiye yerleşik
- **TRL band:** ~2–6
- **Bütçe:** **Max 3.500.000 TL**, destek oranı **%75**
- **Süre:** Max **18 ay**
- **Başvuru formatı:** PRODİS + AGY100-101
- **İlk başvuru hakkı:** En az 2'si ortaklı olmak şartıyla **ilk 5 proje** 1507 kapsamında; sonrası 1501'e yönlendirilir
- **Çağrı takvimi:** Yılda 2 dönem. **2026/1**: 01.01.2026 – 30.03.2026
- **agency_id:** `tubitak_1507`

### TEYDEB 1505 — Üniversite-Sanayi İşbirliği *(Faz 5)*
- **Eligibility:** **Ortak başvuru** — Müşteri Kuruluş (KOBİ/büyük firma) + Yürütücü Kuruluş (üniversite/araştırma merkezi)
- **Bütçe:** **Max 1.000.000 TL** + %5 kurum hissesi + PTİ
- **Destek oranı:** Müşteri KOBİ ise daha yüksek (~%75), büyük ise daha düşük (~%60)
- **Süre:** Max **24 ay** (6 aylık dönemlerle izleme)
- **Format:** PRODİS + AGY105 + İşbirliği Sözleşmesi şablonu + Proje Sonuçları Uygulama Planı
- **Çağrı:** **Sürekli açık** — yılın her günü
- **agency_id:** `tubitak_1505`

### TEYDEB 1601 — Yenilik & Girişimcilik Kapasite *(Faz 5)*
- **Eligibility:** Şirketler, üniversiteler, kamu araştırma merkezleri, vakıflar, TSO, OSB, ihracatçı birlikleri (çağrı bazında daraltılabilir)
- **Bütçe:** Çağrıya göre değişir, **%100'e kadar hibe** (geri ödemesiz)
- **Süre:** Max **36 ay** (uzatma dahil)
- **Gider kalemleri:** Personel, PTİ, burs, seyahat, danışmanlık, ekipman (max %10), genel gider (max %15)
- **Çağrı:** Çağrı-bazlı, tematik (ör. "Global Clean Tech Entrepreneurship", "BiGG Yatırım uygulayıcı kuruluş")
- **agency_id:** `tubitak_1601`

### TEYDEB 1512 — BiGG (Bireysel Genç Girişim) *(Faz 5)*
- **Eligibility:** Bireysel girişimciler (genç, üniversite mezunu/öğrenci profili)
- **Aşamalar:** Aşama 1 = uygulayıcı kuruluş seçimi (1612), Aşama 2 = girişimciye destek
- **Destek miktarı:** Aşama 2 max **900.000 TL** — 2024-1 ile **hibe yerine yatırım modeli** (%3 hisse karşılığı)
- **agency_id:** `tubitak_1512` (Aşama 2), uygulayıcı seçimi `tubitak_1612`

### ARDEB 1071 — Uluslararası Araştırma Fonları *(Faz 5)*
- **Eligibility:** Akademisyenler + üniversite-sanayi-kamu konsorsiyumları
- **Bütçe/süre:** **Çağrı bazlı değişken** (EuroHPC, PRIMA, COST, ikili işbirlikleri). 2026: EuroHPC CoE, PRIMA, NRF Kore, ÇEK, Fransa
- **Format:** **UİDB-PBS** + e-imza
- **Çağrı:** Yıl içinde dağınık (5–15 çağrı/yıl)
- **agency_id:** `tubitak_1071`

### BİDEB 2244 — Sanayi Doktora *(Faz 5)*
- **Eligibility:** Üniversite + Özel Sektör Kuruluşu **kurumsal ortak başvuru** (bireysel değil)
- **Format:** E-BİDEB üzerinden kurumsal başvuru
- **Çağrı:** Yıllık (genelde sonbahar)
- **agency_id:** `tubitak_2244`

## Özet Tablo

| programme_id | name | entity_type | TRL | budget_max | currency | duration | grant_rate | language | call_pattern |
|---|---|---|---|---|---|---|---|---|---|
| tubitak_1501 | Sanayi Ar-Ge | KOBİ+büyük şirket | 2–7 | yok | TRY | 36 ay | %75 | TR | yılda 2 (Oca, Tem) |
| tubitak_1507 | KOBİ Başlangıç | KOBİ şirket | 2–6 | 3.5M | TRY | 18 ay | %75 | TR | yılda 2 (Oca, Tem) |
| tubitak_1505 | Üniv-Sanayi | KOBİ/büyük + üniv | 3–7 | 1M | TRY | 24 ay | %60–75 | TR | sürekli açık |
| tubitak_1601 | Kapasite | kurumsal | n/a | çağrı bazlı | TRY | 36 ay | %100'e kadar | TR | çağrı bazlı |
| tubitak_1512 | BiGG | birey girişimci | 2–4 | 900K | TRY | 12–18 ay | yatırım (%3 hisse) | TR | yıllık |
| tubitak_1071 | Uluslararası | akademisyen+kons. | 1–9 | çağrı bazlı | TRY | çağrı bazlı | çağrı bazlı | TR/EN | yıl içi dağınık |
| tubitak_2244 | Sanayi Doktora | üniv+firma | n/a | burs+protokol | TRY | 4 yıl | n/a (burs) | TR | yıllık |

## Scraper Notları

- **Açık çağrı listesi crawl**: `/tr/acik-cagrilar` HTML → card-list parse. Son tarih formatı `DD Mon YYYY` (TR). Deadline regex: `(\d{2})\s+(Oca|Şub|Mar|Nis|May|Haz|Tem|Ağu|Eyl|Eki|Kas|Ara)\s+(\d{4})`.
- **Program detay sayfası crawl**: `/tr/destekler/{kategori}/...` URL listesini sabit slug → fetch et, son güncellemeyi izle (Drupal `<meta name="dcterms.modified">`).
- **PDF rehber linkleri**: `<a href="/sites/default/files/...DuyuruMetni....pdf">` pattern. Tek-tip filename: `{program}-{yyyy}-{n}-DuyuruMetni-{tarih}.pdf`.
- **PDF parsing**: bazı duyurular tarama imajı → **Tesseract OCR fallback** (Türkçe dil paketiyle).
- **Duyurular RSS yok** — periyodik HTML diff gerek. `/tr/duyurular` listesini günlük çek.
- **Rate limit**: 0.5–1 req/sec, retry-after header dinle.

## Belirsiz/Eksik Bilgi

- **1512 detay sayfası 403** verdi — uygulayıcı kuruluş tam listesi ve girişimci yaş/eğitim şartları doğrudan teyit edilemedi
- **1507 2026/1 PDF tarama imajı** — bütçe limiti 3.5M TL haricindeki nitel detaylar ikincil kaynaklardan
- **AGY100/AGY105 form template'leri** — doldurulabilir DOCX template indirme linki PRODİS girişi sonrası erişilebilir; Bluedev'in kendi DOCX template'ini üretmesi (rehbere uyumlu) zorunlu
- **Horizon Europe ↔ 1071 ilişkisi**: 1071'in HE başarısız hakem yorumunu re-application için kullanılabildiği söylenir ama bu özelliğin 2026'daki resmi varlığı teyit edilmedi

## Kaynaklar
- https://tubitak.gov.tr/tr/destekler/sanayi/ulusal-destek-programlari
- https://tubitak.gov.tr/tr/acik-cagrilar
- https://tubitak.gov.tr/sites/default/files/2026-02/1501-2026-1-DuyuruMetni-26.12.2025-v2.pdf
- https://tubitak.gov.tr/sites/default/files/2026-01/1507-2026-1-DuyuruMetni-26.12.2025.pdf
- https://eteydeb.tubitak.gov.tr/prodis.htm
