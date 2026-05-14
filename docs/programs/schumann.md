# Schumann Associates — Kaynak Analizi *(Faz 7, low priority)*

> **Önemli ayrım:** Schumann Associates bir **consultancy** — kendi fonu YOK. AB programlarına başvuru danışmanlığı yapıyor. Bu modül bir "trend/haber aggregator" olarak Faz 7'ye bırakılmıştır.

## Resmi Kaynaklar

- **Ana site:** https://www.schumanassociates.com/
- **Newsletter:** mailing list (email subscription)
- **Blog/insights:** https://www.schumanassociates.com/insights veya benzeri (web sayfası yapısına bağlı)

## Schumann'ın Bluedev İçin Değeri

1. **Sektör trendleri:** Hangi alanlar AB fonlarında yükselişte, hangi ülkeler güçlü, hangi tematik öncelikler değişti — Schumann blog yazıları bu sezgileri özetler.
2. **Çağrı yorumları:** Yeni açılan büyük Horizon çağrıları için ne tür projelerin başarılı olacağına dair Schumann analist yorumları faydalı (idea generator için ek context).
3. **Konsorsiyum partnership intelligence:** Schumann hangi konsorsiyumlarla çalışıyor, hangi ülkeler hangi rollerde başarılı — RAG corpus'una ek kaynak.

## Scraping Stratejisi (Faz 7)

- **HTML scrape** blog/insights sayfası — newsletter signup formu var ama otomatik kayıt etik değil
- **RSS varsa** öncelikle (newsletter back-catalog için)
- **Frekans:** haftalık (haberler yavaş hareket eder)
- **Hedef ürün:** "Schumann insights" feed'i FE'de "AB hibe haberleri" widget'ı olarak kullanıcıya sunulur — direkt çağrı veri kaynağı DEĞİL

## Bu Modül Bluedev'in Hangi Eksiğini Doldurur?

- Bluedev'in **idea generator** ajanı (Faz 2) çağrı rehberi + CORDIS funded projects + Schumann analiz yazılarını birleştirerek "bu çağrıya hangi tip proje sunmalı" sezgisini güçlendirir.
- Frontend'de opsiyonel "Sector intelligence" panel — Schumann + diğer consultancy'lerin RSS'lerinden agregat haber feed.

## Benzer Kaynaklar (Faz 7'de birlikte ele alınabilir)

- **EARMA** (European Association of Research Managers and Administrators) — academic perspective
- **EFTA / EARTO** newsletters
- **Catalyze Group** (Hollanda consultancy)
- **Accelopment** (İsviçre consultancy)
- **Zabala Innovation** (İspanya consultancy)
- **APRE** (İtalya NCP)

Bu consultancy aggregator'ları için tek bir `news_aggregator` modülü düşünülebilir; her biri ayrı program modülü değil.

## Karar

**Faz 1-6'ya dahil DEĞİL.** Bluedev'in MVP'si ve büyüme döneminde çağrı + AI ajanlar daha kritik. Schumann benzeri trend aggregator, Faz 7'de kullanıcı sayısı ve retention'a göre değerlendirilir.

## Kaynaklar
- https://www.schumanassociates.com/
