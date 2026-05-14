# KOSGEB Programları — Kaynak Analizi

> **Kritik:** Mevcut `programmes.id = "kosgeb_arge"` entry'si gerçekte KOSGEB'in bağımsız bir programı değil — TÜBİTAK 1501/1507'ye yönlendiren bir köprü. Bu kavramsal hata Faz 5'te düzeltilecek.

## Resmi Kaynaklar

- **Ana destek listesi:** https://www.kosgeb.gov.tr/site/tr/genel/destekler/3/destekler
- **Duyurular / çağrılar:** https://www.kosgeb.gov.tr/site/tr/genel/liste/2/duyurular
- **Başvuru portalı (e-Devlet):** https://edevlet.kosgeb.gov.tr/
- **Doküman CDN:** `https://webdosya.kosgeb.gov.tr/Content/Upload/Dosya/{PROGRAM}/{YIL}/{YYYY.MM.DD}/...`
- **Çağrı merkezi:** 444 1 567 / 0312 595 28 00

## Web Scraping Stratejisi

- **Açık çağrılar:** Tek bir merkezi "open calls" endpoint'i **yok**. Çağrılar iki kanalda yayınlanır: (1) program detay sayfasında inline tablo (`/site/tr/genel/destekdetay/{id}/{slug}`), (2) genel duyurular listesi `/site/tr/genel/liste/2/duyurular` (paginated, statik HTML).
- **HTML pattern:** ASP.NET tabanlı, server-rendered. Program detay sayfaları sabit ID + slug (örn. `9145/ar-ge-destek-programi`). DOM stabil; `<table>` ve `<h3>` başlıklarıyla bölüm ayrımı.
- **Anti-bot:** Belirgin Cloudflare/Captcha **yok**. User-Agent şart; rate-limit pratikte ~1 req/sn güvenli.
- **PDF/DOCX/XLSX rehberler:** `webdosya.kosgeb.gov.tr` CDN'de, yol pattern'i `/Content/Upload/Dosya/{PROGRAM_KISALTMA}/{YYYY}/{YYYY.MM.DD}/{dosya}.{ext}`. **Versiyonlu** — her güncellemede yeni tarih dizini. Scraper sayfadaki son link'i takip etmeli.
- **Periyot:** Programlar **sürekli açık** + dönemli çağrılar (Kapasite Geliştirme yıllık 1-2 kez). Daily polling, `If-Modified-Since` destekleniyor.
- **Robots/sitemap:** `robots.txt` allow-all; sitemap.xml mevcut değil — manuel URL listesi.

## Program Detay

### KOSGEB AR-GE, İnovasyon ve Endüstriyel Uygulama Destek Programı *(deprecated — TÜBİTAK redirect)*
KOSGEB sayfasında bu program TÜBİTAK 1501 ve 1507'ye yönlendiriyor (KOSGEB kendi AR-GE programını TÜBİTAK ile entegre etmiş). KOSGEB tarafında **bağımsız bir "AR-GE, İnovasyon ve Endüstriyel Uygulama Destek Programı" detay sayfası kalmamış**; eski URL'ler artık TÜBİTAK 1501/1507'ye yönlenir.
- agency_id placeholder: `kosgeb_tubitak_1501`, `kosgeb_tubitak_1507`
- **Faz 5'te yapılacak:** Bu programmes entry'sini sil veya alias olarak işaretle; gerçek KOSGEB programlarını ekle.

### Kapasite Geliştirme Destek Programı (KOBİGEL halefi) *(Faz 5)*
- **Eligibility:** NACE C (imalat) + 61, 62, 63, 72; küçük/orta KOBİ; "hızlı büyüyen" statüsü (Technogirişim/EYDEP istisnası).
- **Destek kalemleri:** Personel, makine-teçhizat, yazılım, hizmet (eğitim/danışmanlık), işletme sermayesi.
- **Bütçe:** Toplam **20M TL** (savunma/havacılık tedarikçi geliştirme için **30M TL**), her kalem 20 puan, geri ödemeli kredi-faiz desteği.
- **Süre:** 24 ay; kredi geri ödeme 36 ay.
- **Format:** Portal başvuru + 18 adet PDF/DOCX form; XLSX hesaplama tablosu.
- **Çağrı:** Dönemli (2026/1. dönem 28 Şubat 2026 kapandı).
- agency_id: `kosgeb_kapasite_gelistirme`

### Küresel Rekabetçilik Destek Programı *(Faz 5)*
- **Eligibility:** Limited/A.Ş.; orta-yüksek/yüksek-teknoloji ihracatçı, hızlı büyüyen, Turcorn 100, veya öncelikli ürün üreticisi.
- **Kalemler:** Personel, makine-teçhizat, yazılım, hizmet, işletme sermayesi — **her biri max 50M TL** (toplam 250M TL teorik tavan).
- **Tip:** Geri ödemeli kredi-faiz desteği (kamu bankaları üzerinden).
- **Süre:** 24 ay proje + 36 ay kredi.
- **Format:** Sürekli başvuru, portal.
- agency_id: `kosgeb_kuresel_rekabetcilik`

### Stratejik Ürün Destek Programı *(Faz 5)*
- **Eligibility:** Sermaye şirketi, Bakanlık ön-davet (HAMLE programı ile entegre).
- **Kalemler:** Bağımsız değerlendirme (50.000 TL, %100 hibe), personel (10M TL, %80 geri ödemeli).
- **Süre:** 24 ay.
- **Format:** HAMLE çağrı takvimine bağlı.
- agency_id: `kosgeb_stratejik_urun`

### Girişimci Destek Programı *(Faz 5)*
- **Eligibility:** 0-1 yaş (kuruluş) / 0-3 yaş (iş geliştirme); imalat/61/62/63/72; min %50 ortaklık.
- **Kalemler:** Kuruluş 10-20K TL hibe, personel hibe, iş geliştirme **1.5M TL geri ödemeli**, faiz desteği 1M TL.
- **Çağrı:** 2026/2. dönem 20 Nis – 8 May 2026.
- agency_id: `kosgeb_girisimci`

### Teknoloji Merkezi (TEKMER) Destek Programı *(Faz 5)*
- **Eligibility:** A.Ş.; kurucu üniversite/TGB/TTO/OSB/oda.
- **Kalemler:** Kuruluş 19.5M TL, Performans 45.5M TL, Hızlandırma 65M TL. Yıllık 6.506.000 TL.
- **Süre:** 3 + 7 + 10 yıl.
- agency_id: `kosgeb_tekmer`

### KOBİ Dijital Dönüşüm Destek Programı *(Faz 5)*
- **Eligibility:** NACE C imalat (mikro hariç), öz kaynak pozitif, son 3 yılda en az bir kâr; **TÜSSİDE-DDX veya MEXT/İHKİB-SIRI** dijital olgunluk raporu zorunlu.
- **Kalemler:** Makine-teçhizat 1-20M TL, yazılım/donanım 1-20M TL — faiz desteği (geri ödemeli kredi).
- **Süre:** 24 ay; kredi 36 ay + 6 ay ödemesiz.
- **Format:** XLSX destek hesaplama tablosu + DOCX/PDF formlar.
- agency_id: `kosgeb_dijital_donusum`

### Yeşil Sanayi Destek Programı *(Faz 5)*
- **Eligibility:** İmalat KOBİ; deprem bölgesi (11 il) için artırılmış oran.
- **Kalemler:** Güneş enerjisi 14M TL (%60, deprem bölgesi %80-90); temiz/döngüsel ekonomi 4M TL (%70).
- **Süre:** 8-12 ay.
- agency_id: `kosgeb_yesil_sanayi`

### Uluslararasılaşma — *(yok, Küresel Rekabetçilik içinde)*
KOSGEB'in **bağımsız "Uluslararasılaşma Destek Programı"** 2026 kataloğunda **yok**.

### AR-GE Markası Tescili — *(yok)*
Bağımsız bir KOSGEB programı **bulunamadı**. Marka/patent giderleri Kapasite Geliştirme ve Küresel Rekabetçilik içindeki "hizmet alımı" kalemine düşüyor.

## Özet Tablo

| programme_id | name | entity_type | budget_max_tl | language | first_phase? |
|---|---|---|---|---|---|
| `kosgeb_kapasite_gelistirme` | Kapasite Geliştirme | Küçük/Orta KOBİ | 20-30M | TR | Faz 5 |
| `kosgeb_kuresel_rekabetcilik` | Küresel Rekabetçilik | Ltd/AŞ | 250M (toplam) | TR | Faz 5 |
| `kosgeb_stratejik_urun` | Stratejik Ürün | Sermaye şirketi | 10.05M | TR | Faz 5 |
| `kosgeb_girisimci` | Girişimci | Yeni KOBİ | 2M | TR | Faz 5 |
| `kosgeb_tekmer` | Teknoloji Merkezi | AŞ | 130M (yaşam) | TR | Faz 5 |
| `kosgeb_dijital_donusum` | KOBİ Dijital Dönüşüm | İmalat KOBİ | 40M | TR | Faz 5 |
| `kosgeb_yesil_sanayi` | Yeşil Sanayi | İmalat KOBİ | 14M | TR | Faz 5 |

## Scraper Notları

- **Detay sayfası pattern:** `/site/tr/genel/destekdetay/{numeric_id}/{slug}` — ID sabit, slug değişebilir. Scraper ID üzerinden çekmeli.
- **Çağrı tespiti:** Program sayfasındaki `<table>` içindeki "Çağrı Başlangıç/Bitiş" sütunları veya duyurular sayfasındaki başlıkta `çağrı`, `başvuruları başladı`, `son başvuru` regex'leri.
- **PDF rehber yolu:** `https://webdosya.kosgeb.gov.tr/Content/Upload/Dosya/{KISALTMA}/{YYYY}/{YYYY.MM.DD}/...`. Versiyon tarihi dosya adında değil yol içinde. Son tarihi seçmek için sayfadaki HTML linkini takip et.
- **DOCX/XLSX:** Kapasite Geliştirme ve Dijital Dönüşüm programları DOCX başvuru formu + XLSX hesaplama tablosu yayınlıyor. `xlsx_template_auto_download` memory pattern'i buraya doğrudan uygulanır.
- **Karakter encoding:** Türkçe karakterler URL'de %-encode. `httpx` otomatik handle eder.
- **Cache:** ETag/Last-Modified header'ları destekleniyor — conditional GET ile cost azalt.

## Belirsiz/Eksik Bilgi

- **TRL bandları:** Hiçbir KOSGEB programı resmi olarak TRL kullanmıyor. Modülün TRL haritalamasını içsel yapması gerekecek.
- **KOBİGEL durumu:** Program adının resmi olarak emekli mi yoksa periyodik proje çağrısı şeklinde mi geri döneceği belirsiz; **deprecated** varsaymak güvenli.
- **Stratejik Ürün ↔ HAMLE entegrasyonu:** HAMLE Sanayi Bakanlığı tarafında; çağrı takvimi senkronu gerek.
- **Açık çağrı API'si:** Resmi JSON/RSS feed yok; HTML scraping zorunlu.

## Kaynaklar
- https://www.kosgeb.gov.tr/site/tr/genel/destekler/3/destekler
- https://www.kosgeb.gov.tr/site/tr/genel/destekdetay/9200/kapasite-gelistirme-destek-programi
- https://www.kosgeb.gov.tr/site/tr/genel/destekdetay/9206/kuresel-rekabetcilik-destek-programi
- https://www.kosgeb.gov.tr/site/tr/genel/destekdetay/9144/kobi-dijital-donusum-destek-programi
- https://webdosya.kosgeb.gov.tr/Content/Upload/Dosya/KAPAS%C4%B0TE%20GEL%C4%B0ST%C4%B0RME/2026/Kapasite_Gelis%CC%A7tirme_Destek_Program%C4%B1_Bas%CC%A7vuru_K%C4%B1lavuzu_04.02.2026.pdf
