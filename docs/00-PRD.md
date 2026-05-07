# 00 — Product Requirements Document (PRD)

**Doküman sahibi:** Aydoğan / Bluedev
**Versiyon:** 1.0
**Tarih:** 2026-05-07
**Status:** Approved for development

---

## 1. Vizyon

**"Çağrı linkini ver, taslak başvuruyu al — kaynaklı, compliance-onaylı, distinctive."**

Bluedev GrantWriter, Türkiye ve AB hibe ekosisteminde başvuran firmalar için **uçtan uca AI-destekli hibe yazımı SaaS'ıdır**. Manuel yazımda 200-600 saat alan bir Horizon Europe başvurusunu 8-15 saate, TÜBİTAK 1501 başvurusunu 4-8 saate indirir; halüsinasyon riskini Crossref/OpenAlex citation grounding ile <%5'e düşürür; Horizon Europe'un 2025'te zorunlu kıldığı AI disclosure compliance'ı otomatik sağlar.

**Slogan:** "Hibe başvurusu yazmıyoruz, kazanılır hale getiriyoruz."

---

## 2. Problem (Niçin var?)

Pazar validasyon raporundan (mevcut paket dosyası dışında, `MARKET_RESEARCH.md` referans):

1. **Başarı oranları çöktü**: Horizon Europe genel %12, EIC Accelerator %2.7, Cluster 4 AI %5.5 (Science|Business 2025-2026 verisi).
2. **Başvuru hacmi patladı**: REA Ekim 2025 — bazı çağrılarda %80 artış. AI-yazılı başvuruların önemli payı var.
3. **Mevcut çözümler yetersiz**:
   - WinGrants AI: tek dil (EN), tek pazar (HE), yüksek fiyat
   - Grantable/Instrumentl: US-only, AB/TR çağrı yapısını anlamıyor
   - EMDESK: post-award proje yönetimi, yazım değil
   - Türkçe + AB ikili pazara hizmet eden **hiçbir ürün yok**
4. **Halüsinasyon krizi**: ChatGPT/Claude doğrudan kullanımında %14-95 fabricated citation oranı. HE sayfa 32 AI disclosure zorunluluğu (2025).
5. **Türk ekosistem boşluğu**: TÜBİTAK PRODİS / KOSGEB KBS için AI-native tek bir tool yok. Danışmanlar manuel çalışıyor (sabit ücret + başarı primi, fiyat opaque).

---

## 3. Hedef Kullanıcılar (Personas)

### Persona 1 — "Mehmet, KOBİ Kurucusu" (Primary)
- 35 yaş, deep-tech startup CEO, 8 çalışan, İstanbul
- Her yıl 1-2 TÜBİTAK 1501, 1 KOSGEB AR-GE başvurusu yapıyor
- Horizon Europe başvurusu denedi, ortağı bulamadığı için iptal etti
- Şu an: 80 saatini başvuru yazımına ayırıyor, danışmana 50K TL ödüyor
- Acı: "Aynı şeyi her yıl yeniden yazıyorum, danışman teknik dilimi anlamıyor"
- Beklenti: Önceki başvurudan otomatik öğrenen, teknik terimi koruyan AI

### Persona 2 — "Selin, R&D Müdürü" (Primary)
- 42 yaş, orta-büyük şirket (200 çalışan), Ankara
- Yılda 4-6 başvuru (TÜBİTAK + Eurostars + Horizon Europe)
- 2 yardımcısı var, ekibi yönetiyor
- Acı: "Konsorsiyum partner bulmak haftalar alıyor, EEN yetersiz"
- Beklenti: Multi-user, version control, partner database

### Persona 3 — "Dr. Ayşe, TTO Direktörü" (Secondary, B2B kanal)
- 50 yaş, üniversite TTO direktörü, İzmir
- 30+ akademisyenin başvurularını yönetiyor (ARDEB + HE)
- Acı: "Her hoca farklı şablonla geliyor, ben editor görevi yapıyorum"
- Beklenti: Multi-tenant, ekip yönetimi, raporlama

### Persona 4 — "Marco, Italian SME Owner" (Phase 2 — AB pazarı)
- 38 yaş, İtalyan KOBİ sahibi, Milano
- HE Cluster 4 AI çağrılarına başvuruyor
- Acı: "Bürokrasi, lump sum bütçe karmaşık"
- Beklenti: EU-spesifik UX, IT lokalizasyon (Faz 2)

---

## 4. Faz 1 Scope (4 hafta — MVP)

### IN SCOPE

**5 Program Desteği:**
1. **TÜBİTAK 1501** (Sanayi AR-GE) — TR yazım, AGY100 form template
2. **TÜBİTAK 1507** (KOBİ AR-GE Başlangıç) — TR yazım, basitleştirilmiş form
3. **KOSGEB AR-GE/Yenilik** (KBS) — TR yazım, KOSGEB format
4. **Horizon Europe RIA/IA** (Cluster 4 AI/digital odaklı) — EN yazım, Standard Application Form Part B
5. **Cascade Funding + NLnet** (NGI Zero, FSTP) — EN yazım, basit format

**Çekirdek Özellikler:**
- 7 AI agent ile uçtan uca taslak üretimi (bkz. `06-agent-architecture.md`)
- Çağrı tarama (EU F&T Portal API, NLnet RSS, Cascade Funding scraping)
- RAG (kazanmış başvuru corpus + EC sample successful proposals)
- **Citation grounding** (Crossref + OpenAlex doğrulama)
- **AI disclosure compliance** (HE sayfa 32 otomatik doldurma)
- **Distinctiveness scoring** (CORDIS funded projects ile cosine similarity)
- DOCX export (HE Standard Application Form template, TÜBİTAK AGY100, KOSGEB)
- Lump sum bütçe Excel template (HE için)
- Multi-tenant Supabase Auth + RLS
- BYOK (kullanıcı kendi Anthropic/OpenAI key'ini girebilir)
- Türkçe + İngilizce UI
- Stripe billing (3 plan: Starter €99/ay, Pro €299/ay, Agency €799/ay)

### OUT OF SCOPE (Faz 1)
- Eurostars, MSCA, ERC programları (Faz 2)
- Konsorsiyum partner matching motoru (Faz 2)
- Reviewer feedback (ESR) analyzer (Faz 2)
- Mobile app (Faz 3)
- Self-hosted EU model (Mistral/Llama on Scaleway) (Faz 3)
- White-label / agency mode advanced (Faz 2)
- Otomatik portal upload (PRODİS, KBS, F&T Portal API yok — manuel)

---

## 5. User Stories (P0 = MVP zorunlu, P1 = should-have, P2 = nice-to-have)

### Epic 1: Çağrı Keşfi
- **US-1.1 [P0]** Bir kullanıcı olarak, dashboard'da **5 programdan açık çağrıları** filtreleyebilmek istiyorum (program, son tarih, bütçe, sektör).
- **US-1.2 [P0]** Bir kullanıcı olarak, çağrı detayında **eligibility checklist**'i görmek istiyorum (TRL, partner sayısı, ülke kısıtı, KOBİ tanımı).
- **US-1.3 [P1]** Bir kullanıcı olarak, **yeni çağrı bildirimi** almak istiyorum (e-posta, profil eşleşmesine göre).
- **US-1.4 [P2]** Bir kullanıcı olarak, geçmiş benzer çağrıların başarı oranlarını görmek istiyorum.

### Epic 2: Brief Girişi
- **US-2.1 [P0]** Bir kullanıcı olarak, **proje fikrimi 5-10 dakikada** brief formuna girmek istiyorum (program-spesifik form).
- **US-2.2 [P0]** Bir kullanıcı olarak, **şirket bilgilerimi tek seferde** girmek (sonraki başvurularda otomatik doldurulmalı).
- **US-2.3 [P0]** Bir kullanıcı olarak, **konsorsiyum partner bilgilerini** (HE için) girebilmek.
- **US-2.4 [P1]** Bir kullanıcı olarak, eski başvurularımdan **brief'i kopyalayıp düzenleyebilmek**.

### Epic 3: AI Yazımı
- **US-3.1 [P0]** Bir kullanıcı olarak, brief'i girdikten sonra **30-60 dakikada tam taslak** almak (background job, e-posta bildirimi).
- **US-3.2 [P0]** Bir kullanıcı olarak, **agent'ların adım adım çalışmasını canlı görmek** (SSE streaming).
- **US-3.3 [P0]** Bir kullanıcı olarak, **her cümlenin kaynağını** görmek (human/AI/AI-edited markup).
- **US-3.4 [P0]** Bir kullanıcı olarak, **her referansın doğrulanmış** (DOI link tıklanabilir) olduğunu görmek.
- **US-3.5 [P0]** Bir kullanıcı olarak, **Türkçe brief'i girip İngilizce çıktı** almak (HE için).
- **US-3.6 [P1]** Bir kullanıcı olarak, agent'lardan birini **manuel tetikleyebilmek** (örn. "Impact bölümünü yeniden yaz").

### Epic 4: Editör
- **US-4.1 [P0]** Bir kullanıcı olarak, **markdown editor**'de taslağı düzenlemek (TipTap).
- **US-4.2 [P0]** Bir kullanıcı olarak, **section-by-section** çalışmak (Excellence/Impact/Implementation).
- **US-4.3 [P0]** Bir kullanıcı olarak, **bütçe tablosunu inline** düzenleyebilmek.
- **US-4.4 [P1]** Bir kullanıcı olarak, **takım üyeleriyle yorum** ekleyebilmek.
- **US-4.5 [P1]** Bir kullanıcı olarak, **versiyon geçmişi** görmek.

### Epic 5: Compliance & Quality
- **US-5.1 [P0]** Bir kullanıcı olarak, **HE AI disclosure metnini** otomatik üretilmiş görmek (sayfa 32).
- **US-5.2 [P0]** Bir kullanıcı olarak, **distinctiveness score'umu** görmek (renk kodlu: yeşil <0.85, sarı 0.85-0.92, kırmızı >0.92).
- **US-5.3 [P0]** Bir kullanıcı olarak, **sayfa limiti uyarısı** almak (HE 45 sayfa, EIC 20 sayfa).
- **US-5.4 [P0]** Bir kullanıcı olarak, **doğrulanmamış citation'lar için kırmızı bayrak** görmek; submit'ten önce çözmem zorunlu.
- **US-5.5 [P1]** Bir kullanıcı olarak, **DNSH ve gender dimension** otomatik kontrol almak.

### Epic 6: Export
- **US-6.1 [P0]** Bir kullanıcı olarak, **resmi DOCX şablonunda** export almak (HE Standard Application Form, TÜBİTAK AGY100, KOSGEB).
- **US-6.2 [P0]** Bir kullanıcı olarak, **PDF export** almak.
- **US-6.3 [P0]** Bir kullanıcı olarak, **lump sum Excel** template (HE) almak.
- **US-6.4 [P1]** Bir kullanıcı olarak, **bibliography'i BibTeX/RIS** olarak export.

### Epic 7: Account & Billing
- **US-7.1 [P0]** Bir kullanıcı olarak, **Google/email ile giriş** yapmak (Supabase Auth).
- **US-7.2 [P0]** Bir kullanıcı olarak, **takım davet etmek** (Pro+ planlar).
- **US-7.3 [P0]** Bir kullanıcı olarak, **kendi Anthropic/OpenAI API key'imi** girebilmek (BYOK).
- **US-7.4 [P0]** Bir kullanıcı olarak, **Stripe ile ödeme** yapmak.
- **US-7.5 [P1]** Bir kullanıcı olarak, aylık **kullanım raporumu** görmek (token, başvuru sayısı).

---

## 6. Success Metrics (4 hafta sonu MVP)

### Ürün Metrikleri
| Metrik | Hedef | Ölçüm |
|---|---|---|
| Time to first draft | <60 dk | Brief submit → tam taslak hazır |
| Halüsinasyon oranı | <%5 | 100 rastgele citation, Crossref doğrulama |
| AI disclosure compliance | %100 | Her HE başvurusu sayfa 32 dolu |
| Distinctiveness coverage | %100 | Her başvuru için skor üretiliyor |
| Multi-tenant izolasyon | 0 leak | RLS penetration test |

### İş Metrikleri (Faz 1 sonu — Hafta 4)
| Metrik | Hedef |
|---|---|
| Çalışan E2E demo | 5 program × 1 demo başvuru |
| Pilot kullanıcı | 3 Bluedev iç test + 2 dış pilot |
| İlk paying customer | 1 (Bluedev kendi başvurusu için) |
| Production deploy | Vercel + Railway, çalışıyor |

### Faz 1 Sonu Kararı (Hafta 4 retro)
- ✅ 5 metrikten en az 4'ü tutarsa → Faz 2'ye geç
- ⚠️ 3 metrik tutarsa → Faz 1.5 (1-2 hafta polish + bug fix)
- ❌ 2 ya da daha az tutarsa → scope küçült (2 programa düş, sadece TR pazar)

---

## 7. Pricing & Business Model

| Plan | Fiyat | Limit | Hedef |
|---|---|---|---|
| **Starter** | €99/ay (₺3.500) | 3 başvuru/ay, BYOK zorunlu | Solo KOBİ |
| **Pro** | €299/ay (₺10.500) | 15 başvuru/ay, Bluedev managed LLM, çağrı tarama, distinctiveness | Orta KOBİ |
| **Agency** | €799/ay (₺28.000) | Sınırsız, multi-tenant, white-label, API erişim | TTO, accelerator, danışmanlık |

**Yıllık iskonto:** %20 (Pro €2.870/yıl, Agency €7.670/yıl)

**Pilot fiyatlandırma (Faz 1):** İlk 10 kullanıcıya 6 ay %50 iskonto.

---

## 8. Constraints

- **Süre:** 4 hafta hard deadline (3 kişilik ekip)
- **Bütçe:** ~€15K (3 maaş × 4 hafta + altyapı + Claude API)
- **Yasal:** KVKK + GDPR + AI Act (Türkiye + EU çift compliance)
- **Etik:** Halüsinasyon riski yüksek; production'da minimum %5 hata oranı kabul edilebilir, 0 fabricated citation hedefli
- **Ölçek:** İlk 6 ay max 100 kullanıcı, 500 başvuru beklenmiyor

---

## 9. Open Questions (Hafta 1'de cevaplanacak)

1. **AI disclosure metni**: Bluedev kendi disclaimer'ını mı kullanacak yoksa kullanıcıya seçim mi sunacak? → Ürün kararı, hafta 1 sonu
2. **Stripe vs Iyzico**: Türkiye'de Iyzico daha kolay onboarding ama EU için Stripe gerekli. → İki entegrasyonu da yapacağız
3. **Self-host opsiyonu**: Enterprise plan için Mistral/Llama EU cloud opsiyonu Faz 2'de mi Faz 3'te mi? → Faz 3
4. **TÜBİTAK PRODİS API**: TÜBİTAK ile resmi entegrasyon mümkün mü? → Bluedev ekibi araştıracak, Faz 2 hedefi

---

## 10. Approval

| Rol | İsim | Onay |
|---|---|---|
| Founder/PM | Aydoğan | ✅ 2026-05-07 |
| Tech Lead | (atanacak) | ⏳ |
| Designer | N/A (Bluedev Stitch) | — |

---

**Sonraki adım:** `01-CLAUDE.md` ve `02-architecture.md` dosyalarını oku.