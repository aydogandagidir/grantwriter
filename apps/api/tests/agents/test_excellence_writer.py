"""Excellence Writer tests — TÜBİTAK 1501 path.

Three required scenarios from S1.D4.T2:
  1. B1, B2, B3, B4 headings present in the rendered Excellence section.
  2. B2 has at least 800 words (hard rule per docs/07 §4.5 — TÜBİTAK
     panel rejection trigger #1).
  3. Output language is Turkish.

Plus citation extraction, programme-not-supported failure path, and
streaming-interface verification.
"""

from __future__ import annotations

import uuid

from src.agents import ExcellenceWriter
from src.agents.base import AgentInput
from src.agents.excellence_writer import (
    ExcellenceWriterOutput,
    extract_citations,
)

from tests.llm.conftest import FakeProvider, build_router, make_response

# A canned Turkish Excellence section. B2 is intentionally long enough to
# clear the 800-word panel threshold; the other subsections are shorter.
# Citations seeded throughout so the regex extractor has something to find.
CANNED_TR_EXCELLENCE = """
## B1 Proje Konusu ve Amaçları

Bu proje, Türkiye'deki orta ölçekli tekstil işletmelerinde dokuma
makinelerinden toplanan görüntü verisi üzerinde gerçek-zamanlı kalite
tespiti yapacak, kenar (edge) cihazlarda çalışan bir derin öğrenme
sistemi geliştirmeyi amaçlamaktadır. Mevcut TRL seviyemiz 4 olarak
değerlendirilmekte, proje sonunda hedeflenen TRL seviyesi ise 6'dır.
SMART hedefler şunlardır: dokuma kumaşlarda kusur tespiti için tek
kareyi 200 milisaniyenin altında işleyecek bir model, %95 üzeri
duyarlılık ve %90 üzeri kesinlik, sahada 6 ay sürecek pilot doğrulama.

## B2 Yenilikçi Yönler

Bu projenin mevcut durumdan farkı üç temel boyutta yenilikçi bir
yaklaşım sunmasıdır. İlk boyut teknolojik yeniliktir. Türkiye'deki
tekstil endüstrisinde dokuma makinelerinde gerçek-zamanlı kalite
tespiti çoğunlukla insan operatörlere bağlıdır; bu durum hem yorgunluk
kaynaklı hata oranlarını hem de zincirleme üretim duruşlarını
artırmaktadır. Mevcut otomatik sistemlerde kullanılan klasik
bilgisayarlı görü algoritmaları, örneğin OpenCV tabanlı eşikleme
yaklaşımları, ışık koşullarına ve kumaş dokusuna duyarlıdır; tipik
tespit süreleri 1.2 ile 1.8 saniye arasında değişmekte ve saniyede
altı ila dokuz metre ilerleyen üretim hatları için yetersiz
kalmaktadır [Yıldırım 2023]. Bizim önerdiğimiz YOLOv9-tiny tabanlı ve
Türkiye'deki dokuma kumaşlardan toplanmış on sekiz bin görsel üzerinde
fine-tune edilmiş model, Edge TPU üzerinde iki yüz milisaniyenin
altında inference süresi sağlamaktadır.

İkinci boyut süreç yeniliğidir. Mevcut çözümler genellikle merkezi
bir sunucuda yapılan toplu çıkarımı esas almakta, bu da hat üzerinde
ek ağ yatırımı gerektirmektedir. Önerilen mimari, her dokuma makinesi
yanında konumlandırılmış bir kenar cihazını ana karar mercii olarak
kullanır; merkezi sistem yalnızca özet metriklerini ve yeniden eğitim
için seçilmiş örnekleri toplar. Bu sayede ağ trafiği yaklaşık doksan
beş yüzde oranında azalır, kesintisiz üretim sürdürülür ve müşterinin
mevcut altyapısına minimum müdahale ile entegre olunur. Ayrıca model
güncellemeleri merkezi olarak yönetilse de inference yereldedir;
bu da KVKK ile uyum açısından kullanıcı verisinin işletme dışına
çıkmamasını garanti altına alır.

Üçüncü boyut pazar yeniliğidir. Avrupa kaynaklı çözümler ortalama
yıllık on bin Euro üzerinde lisans ücretleri ve İngilizce destek ile
gelmekte, Türkiye'deki kobi ölçekli işletmeler için ekonomik açıdan
uygulanabilir olmamaktadır. Bizim modelimiz, açık kaynak temellere
dayalı geliştirilmiş, yıllık iki bin beş yüz Euro karşılığında
yerinde destek ile sunulmakta ve ödenebilirlik problemini doğrudan
hedeflemektedir. Bu fiyatlandırma yaklaşımı, dokuma sektöründeki
beş binin üzerinde küçük ve orta ölçekli işletmeyi adreslenebilir
pazar haline getirmektedir [Aydın et al. 2024].

Mevcut çözümlerle kıyaslandığında temel farklılıklar şunlardır:
gecikme süresi sekiz kat daha düşük, sahada gereken donanım maliyeti
otuz binden üç bin Euro'ya iniyor, kullanıcı arayüzü Türkçe ve hat
operatörünün anlayabileceği seviyede sade tutuluyor, eğitim verisi
Türkiye'deki üreticilere özgü kumaşları yansıtıyor ve dolayısıyla
genel modellerin üretemediği yerel doğruluk sağlanıyor. Bu farklılaşma,
Türkiye'nin dokuma ihracatındaki rekabet gücünü doğrudan
desteklemektedir; hatalı ürün sevkiyatı oranındaki düşüş Avrupa'lı
müşteriler nezdinde itibar kazanımı sağlamakta, geri çağırma
maliyetlerini azaltmaktadır.

Bu proje aynı zamanda dış bağımlılığı azaltma açısından kritik bir
adımdır. Mevcut otomatik kalite tespit sistemlerinin çoğu Almanya
ve İtalya kaynaklıdır; lisans ve bakım maliyetleri yıllık olarak
yurt dışına ödenmektedir. Yerli geliştirilen modelin yaygınlaşması,
yıllık tahmini iki yüz milyon Türk lirası tutarındaki yurt dışı
ödemelerinin Türkiye içinde döngüye girmesini sağlayacaktır. Ayrıca
bu sistem, Türkiye'nin yapay zeka stratejisi 2024-2027 belgesinde
vurgulanan "endüstriyel yapay zekada yerli çözümler" başlığı altındaki
hedeflerle birebir örtüşmektedir [TÜBİTAK 2024].

Yenilikçilik düzeyini somut bir biçimde değerlendirmek için tarafımızca
geliştirilen ve sektörel uzmanlardan oluşan bir referans grubu ile
yürütülen ön çalışma, mevcut çözümlerin doksan iki yüzdesine kıyasla
önerilen sistemin gerçek-zamanlı tespitte %30 daha yüksek doğruluk
gösterdiğini, %40 daha düşük yanlış pozitif oranı sağladığını ve
operatörün eğitim süresini yarıya indirdiğini ortaya koymuştur. Bu
ön bulgular, sektörel kabul açısından da sağlam bir temel oluşturmakta;
sözleşme aşamasındaki üç ayrı pilot işletmeden alınan niyet
mektupları, projenin pazara giriş hızını desteklemektedir. Sonuç
itibariyle bu proje yalnızca teknolojik bir ilerleme değil, aynı
zamanda sektörel bir dönüşüm önerisi sunmaktadır; bu durum TÜBİTAK
1501 değerlendirme kriterleri açısından özgün, ölçülebilir ve
ticarileşme potansiyeli yüksek bir profil çizmektedir.

Teknolojik farkın doğrulanması için ölçülen ek karşılaştırmalı metrikler
şu şekildedir. Mevcut endüstride yaygın kullanımdaki sistem A için bir
karenin işlenmesi ortalama bin iki yüz milisaniye sürmekte, hatasız
sınıflandırma oranı seksen iki yüzdesinde kalmakta, sahada elektrik
kesintisinden sonra otomatik kalkış süresi yaklaşık dört dakika
gerektirmektedir. Sistem B benzer mimaride ancak yüksek başlangıç
maliyeti ile gelmekte, lisans modeline bağlı olarak sahaya özgü
özelleştirme yapılamamaktadır. Önerdiğimiz çözüm bu iki sistemin
yetersiz kaldığı sahaya özgü kumaş çeşitliliğine ve değişken ışık
koşullarına karşı doğrulanmıştır. Üç farklı pilotta dokuz aylık
gözlem süresince ölçülen ortalama gerçek-zamanlı tespit doğruluğu
yüzde doksan altı olarak kaydedilmiş, bir kareyi işleme süresi
yüz altmış milisaniye olarak ölçülmüştür. Hattın saatlik üretim
sayısı işletmeden işletmeye değişmekle birlikte, kusur kaynaklı
duruşların yüzde otuz iki azaldığı ve operatörlerin günlük raporlama
yükünün yarıya indiği gözlemlenmiştir. Bu metrikler, projenin
ticarileşme süresini çağrı kapsamındaki üst sınır olan otuz altı
ayın oldukça altında, pilot başlangıcından itibaren on sekizinci
ayda elde edilebileceğini desteklemektedir.

Ayrıca, yenilikçilik kıyaslaması için kullandığımız referans
çerçeve, OECD Frascati ve TÜBİTAK kurumsal yenilik anketi
metodolojilerini birleştiren karma bir yaklaşımdır; bu sayede hem
üretim sürecindeki süreç yeniliği boyutu hem de ürün yeniliği
boyutu eş zamanlı olarak değerlendirilebilmiştir. Karma metodoloji,
yalnızca akademik özgünlüğe değil aynı zamanda saha uygulanabilirliğine
dair somut göstergelere de yer vermesi açısından TÜBİTAK
panellerinin son yıllardaki değerlendirme eğilimleriyle uyumludur
ve önerinin somut bir dayanak ile sunulmasını mümkün kılmaktadır.

Önerilen sistemin yenilikçilik kazanımlarına dayalı pazar etki
projeksiyonu da yapılmıştır. Türkiye dokuma sektörünün toplam
yıllık üretim değeri tahmini olarak yetmiş milyar Türk lirası
olarak raporlanmaktadır; ürün kalitesinden kaynaklı geri dönüş ve
hatalı sevkiyat oranının yüzde iki ile yüzde dört arasında
seyrettiği bilinmektedir. Sahaya yaygınlaştırılan bir kalite tespit
sistemi bu oranı yarıya indirebilirse, sektörün maliyet tabanında
yıllık yedi yüz milyon Türk lirası mertebesinde net iyileşme
oluşacağı öngörülmektedir. Projenin başarıya ulaşması durumunda
ihracat hacminin Avrupa pazarı tarafında en az yüzde sekiz
oranında artması, bu artışın istihdamda da yaklaşık dört bin yeni
nitelikli iş gücü gereksinimine yol açması beklenmektedir. Bu
büyüklükteki çarpan etkisi TÜBİTAK 1501'in stratejik öncelik
hedefleriyle birebir örtüşmektedir.

## B3 Yöntem ve Teknik

Yöntem aşaması beş ana basamağa bölünmüştür. Birinci basamakta
Türkiye'deki üç farklı tekstil işletmesinden anonim görüntü verisi
toplanacak, ikinci basamakta YOLOv9-tiny mimarisi üzerinde transfer
öğrenme uygulanarak fine-tune yapılacaktır. Üçüncü basamakta Edge TPU
ve Jetson Nano platformlarında karşılaştırmalı dağıtım denemeleri
gerçekleştirilecek, dördüncü basamakta saha pilotunda sürekli
iyileştirme döngüsü kurulacak ve beşinci basamakta sonuçlar
ölçülerek raporlanacaktır. Doğrulama planında başarı kriterleri
şunlardır: ortalama gecikme iki yüz milisaniyenin altında, duyarlılık
%95 üzeri, kesinlik %90 üzeri ve haftalık operatör memnuniyet
puanı beş üzerinden dört üzeri.

Riskler kategorize edilmiştir. Donanım tedarik gecikmesi durumunda
yedek tedarikçi planı devreye alınır. Eğitim verisinin yetersiz
kalması durumunda sentetik veri üretim teknikleri (data augmentation,
CycleGAN tabanlı sentez) tetiklenir. Sahada beklenmedik kumaş türü
karşımıza çıkarsa modelin çevrimdışı yeniden eğitilmesi için ayrı
bir pipeline hazırlanmıştır.

## B4 Literatür Taraması

Literatür taramasında dokuma kumaşlarda kusur tespiti üzerine yapılan
son üç yılın çalışmaları incelenmiştir. Türkiye'deki üniversitelerde
yapılan tezlerin önemli bir bölümü laboratuvar koşullarına özgüdür;
saha entegrasyonu az çalışılmıştır [Demir 2022]. Avrupa'daki
araştırmalar genellikle CNN tabanlı yaklaşımları benimsemekte ve
gerçek-zamanlı performansa az değinmektedir. Bu projenin literatüre
katkısı; Türkiye'deki dokuma kumaş çeşitliliğine özgü bir veri seti
yayınlamak ve gerçek-zamanlı kenar dağıtım performansını ölçen bir
referans noktası oluşturmaktır.
""".strip()


def _build_agent(canned: str = CANNED_TR_EXCELLENCE) -> tuple[ExcellenceWriter, FakeProvider]:
    primary = FakeProvider(
        "claude",
        [
            make_response(
                text=canned,
                model="claude-opus-4-7",
                provider="claude",
                input_tokens=12_000,
                output_tokens=3_500,
                cost_usd=0.43,
            )
        ],
    )
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})
    return ExcellenceWriter(router=router), primary


def _make_input(*, programme_id: str = "tubitak_1501", language: str = "tr") -> AgentInput:
    call_metadata_dict = {
        "scope_summary": "Sanayi AR-GE; tekstil endüstrisinde gerçek-zamanlı kalite tespiti.",
        "expected_outcomes": ["Endüstriye uygulanabilir prototip"],
        "key_terms_to_use": [
            "yenilikçi ürün",
            "Edge TPU",
            "TÜBİTAK 1501",
            "ticarileşme",
            "kenar yapay zeka",
        ],
        "page_limit": None,
        "language_required": "tr",
        "user_eligible": True,
        "user_eligibility_issues": [],
    }
    return AgentInput(
        proposal_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        programme_id=programme_id,
        language=language,  # type: ignore[arg-type]
        brief={
            "title": "Tekstil hattında gerçek-zamanlı kalite tespiti",
            "problem_statement": (
                "Türkiye'deki dokuma sektöründe kumaş kusur tespiti hâlâ "
                "büyük ölçüde manueldir; bu durum hem hata oranını hem "
                "iş gücü maliyetini artırmaktadır."
            ),
            "proposed_solution": (
                "Edge TPU üzerinde çalışan, Türkiye'deki kumaşlardan "
                "toplanmış veri ile fine-tune edilmiş YOLOv9-tiny tabanlı "
                "kalite tespit sistemi."
            ),
            "team_expertise": "Beş yıl AR-GE deneyimi, üç patent, sektörel ortaklıklar.",
            "duration_months": 24,
        },
        call={"call_text": "TÜBİTAK 1501 2026 Çağrısı (test fixture)..."},
        previous_outputs={
            "call_analyst": {
                "agent_id": "call_analyst",
                "status": "completed",
                "output": call_metadata_dict,
            },
            "rag_context": "",
        },
    )


# ── Required scenarios ──────────────────────────────────────────────────


async def test_b1_b2_b3_b4_headings_present() -> None:
    agent, primary = _build_agent()
    result = await agent.run(_make_input())

    assert result.status == "completed"
    parsed = ExcellenceWriterOutput.model_validate(result.output)
    md = parsed.excellence_md
    assert "## B1" in md
    assert "## B2" in md
    assert "## B3" in md
    assert "## B4" in md
    # All four subsections in the dict are non-empty after split.
    for key in (
        "B1_proje_konusu_ve_amaclari",
        "B2_yenilikci_yonleri",
        "B3_yontem_ve_teknik",
        "B4_literature_review",
    ):
        assert parsed.subsections.get(key, "").strip(), f"empty subsection: {key}"

    # Sanity: agent posted to the LLM with the right task and cache_system on.
    assert len(primary.calls) == 1
    sent_request, model_used, _ = primary.calls[0]
    assert model_used == "claude-opus-4-7"
    assert sent_request.task == "excellence_writer"
    assert sent_request.cache_system is True


async def test_b2_at_least_800_words() -> None:
    agent, _ = _build_agent()
    result = await agent.run(_make_input())

    parsed = ExcellenceWriterOutput.model_validate(result.output)
    b2 = parsed.subsections["B2_yenilikci_yonleri"]
    word_count = len(b2.split())
    assert (
        word_count >= 800
    ), f"B2 too short ({word_count} words). TÜBİTAK panel rejection threshold."


async def test_output_is_turkish() -> None:
    agent, _ = _build_agent()
    result = await agent.run(_make_input())

    parsed = ExcellenceWriterOutput.model_validate(result.output)
    md = parsed.excellence_md
    # At least one Turkish-specific character must appear somewhere.
    turkish_chars = set("çğıöşüÇĞİÖŞÜ")
    assert any(c in turkish_chars for c in md), "no Turkish-specific characters in output"
    # Common Turkish stop words too, just to be sure it's not an isolated diacritic.
    common_words = ("için", "olan", "ile", "bir")
    lower = md.lower()
    assert any(w in lower for w in common_words), "no common Turkish words found"


# ── Additional coverage ────────────────────────────────────────────────


def test_extract_citations_pulls_bracket_and_paren_forms() -> None:
    text = (
        "Ön çalışmalar [Yıldırım 2023] ve [Aydın et al. 2024] sonuçlarını "
        "doğrulamaktadır. Klasik yaklaşım (Demir 2022) burada yetersiz. "
        "Numbered also work [12] but [bogus year missing] should not."
    )
    citations = [c.raw_text for c in extract_citations(text)]
    assert "[Yıldırım 2023]" in citations
    assert "[Aydın et al. 2024]" in citations
    assert "(Demir 2022)" in citations
    assert "[12]" in citations
    assert "[bogus year missing]" not in citations


async def test_citations_extracted_from_response() -> None:
    agent, _ = _build_agent()
    result = await agent.run(_make_input())

    parsed = ExcellenceWriterOutput.model_validate(result.output)
    raws = {c.raw_text for c in parsed.citations_used}
    assert "[Yıldırım 2023]" in raws
    assert "[Aydın et al. 2024]" in raws
    # All entries start unverified (Hallucination Hunter verifies later).
    assert all(c.verified is False for c in parsed.citations_used)


async def test_unsupported_programme_returns_failed_status() -> None:
    agent, primary = _build_agent()
    result = await agent.run(_make_input(programme_id="kosgeb_arge"))

    assert result.status == "failed"
    # ``kosgeb_arge`` is not yet registered in the programmes registry,
    # so the agent fails before reaching prompt-load.
    assert "unknown programme" in result.metadata["error"].lower()
    # Agent must NOT have called the LLM if the programme is unknown.
    assert len(primary.calls) == 0


async def test_stream_yields_full_body_in_chunks() -> None:
    agent, _ = _build_agent()
    input_ = _make_input()

    chunks: list[str] = []
    async for piece in agent.stream(input_):
        chunks.append(piece)

    assert chunks, "stream produced no output"
    assembled = "".join(chunks)
    assert "## B1" in assembled
    assert "## B2" in assembled
    # We split at the first blank line, so when present we get >= 2 chunks.
    if "\n\n" in CANNED_TR_EXCELLENCE.strip():
        assert len(chunks) >= 2


async def test_key_terms_used_intersects_call_metadata() -> None:
    """The agent reports which key_terms_to_use appear in the rendered text.

    Compliance Reviewer (S2) uses this to flag missing required
    terminology, so worth pinning behaviour now.
    """

    canned = (
        "## B1 Proje Konusu ve Amaçları\nProje TÜBİTAK 1501 kapsamında "
        "ticarileşme odaklı çalışacaktır.\n\n"
        "## B2 Yenilikçi Yönler\n"
        + (" yenilikçi ürün " * 500)
        + "\n\n## B3 Yöntem\n...\n\n## B4 Literatür Taraması\n..."
    )
    agent, _ = _build_agent(canned=canned)
    result = await agent.run(_make_input())
    parsed = ExcellenceWriterOutput.model_validate(result.output)

    # Use casefold rather than lower for Turkish-safe normalization.
    used_norm = {t.casefold() for t in parsed.key_terms_used}
    # The call_metadata.key_terms_to_use list seeds these:
    assert "yenilikçi ürün".casefold() in used_norm
    assert "ticarileşme".casefold() in used_norm
    assert "tübi̇tak 1501".casefold() in used_norm or "TÜBİTAK 1501".casefold() in used_norm
    # Edge TPU was NOT in the canned text → must NOT appear.
    assert "edge tpu" not in used_norm
