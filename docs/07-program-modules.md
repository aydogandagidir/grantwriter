# 07 — Program Modülleri

## 1. Niye Plugin Mimarisi?

Faz 1'de 5 program destekliyoruz. Faz 2'de Eurostars, MSCA, ERC, EIC eklenecek. Eğer program-spesifik mantığı core'a karıştırırsak, her yeni program eklendiğinde 6 dosyaya dokunmak gerekecek — bu da regresyon riski demek. Bu yüzden her program **tek bir klasör altında izole** edilir, çekirdek kod programdan hiç haberdar değildir.

Çekirdek bir `BaseProgramModule` arayüzü tanımlar; her program bu arayüzü uygular; çekirdek runtime'da `programme_id` ile doğru modülü registry'den çözer. Yeni program eklemek için core'da kod değişikliği yok — sadece yeni klasör + registry kaydı.

Ekibe net mesaj: **`apps/api/src/programs/` dışına program-spesifik kod sızdırmıyoruz.** Eğer Excellence Writer agent'ı içinde "if programme_id == 'horizon_eu_ria'" görürseniz PR reddedilir.

---

## 2. BaseProgramModule Interface

`apps/api/src/programs/base.py`:

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Literal

class BriefField(BaseModel):
    """Brief formundaki tek bir alan."""
    key: str                    # 'problem_statement'
    label_tr: str
    label_en: str
    type: Literal["text", "textarea", "number", "select", "multiselect", "date", "currency"]
    required: bool = True
    max_length: int | None = None
    options: list[dict] | None = None
    help_text_tr: str | None = None
    help_text_en: str | None = None
    placeholder_tr: str | None = None
    placeholder_en: str | None = None

class BriefSchema(BaseModel):
    sections: list[dict]        # [{title, fields: [BriefField]}]

class CallMetadata(BaseModel):
    """Call Analyst output, program-spesifik alanlar burada."""
    eligibility: dict
    scope_summary: str
    expected_outcomes: list[str]
    expected_impacts: list[str]
    evaluation_criteria: list[dict]
    page_limit: int | None
    language: Literal["tr", "en"]
    key_terms_to_use: list[str]
    deadlines: dict
    extra: dict = {}            # program-spesifik

class ValidationIssue(BaseModel):
    severity: Literal["blocker", "warning", "info"]
    section: str | None
    code: str
    message_tr: str
    message_en: str
    suggestion: str | None = None

class BaseProgramModule(ABC):
    program_id: str
    name_tr: str
    name_en: str
    funder: str
    language: Literal["tr", "en", "both"]

    # Section structure for this programme
    sections: list[str]         # ['excellence', 'impact', 'implementation']

    # Sub-section IDs for prompts and validation
    subsection_map: dict[str, list[str]]
    # e.g. {"excellence": ["1.1_objectives", "1.2_methodology", ...]}

    @abstractmethod
    def parse_call(self, call_text: str, call_metadata: dict) -> CallMetadata: ...

    @abstractmethod
    def get_brief_schema(self) -> BriefSchema: ...

    @abstractmethod
    def get_template_path(self) -> str:
        """DOCX template file path."""

    @abstractmethod
    def get_prompt_path(self, agent_id: str) -> str:
        """Prompt file path for given agent."""

    @abstractmethod
    def validate_draft(self, draft: dict, metadata: CallMetadata) -> list[ValidationIssue]: ...

    @abstractmethod
    async def export_docx(self, proposal: dict) -> bytes: ...

    @abstractmethod
    async def export_xlsx_budget(self, proposal: dict) -> bytes | None:
        """Budget Excel export. Return None if not applicable."""
```

Registry:

```python
# apps/api/src/programs/__init__.py
from .horizon_eu_ria import HorizonEURIAModule
from .tubitak_1501 import TUBITAK1501Module
from .tubitak_1507 import TUBITAK1507Module
from .kosgeb_arge import KOSGEBARGEModule
from .cascade_funding import CascadeFundingModule

REGISTRY: dict[str, BaseProgramModule] = {
    "horizon_eu_ria": HorizonEURIAModule(),
    "tubitak_1501": TUBITAK1501Module(),
    "tubitak_1507": TUBITAK1507Module(),
    "kosgeb_arge": KOSGEBARGEModule(),
    "cascade_funding": CascadeFundingModule(),
}

def get_module(program_id: str) -> BaseProgramModule:
    if program_id not in REGISTRY:
        raise ValueError(f"Unknown programme: {program_id}")
    return REGISTRY[program_id]
```

Yeni program eklemek için: yeni klasör + bu dosyaya tek satır.

---

## 3. Horizon Europe RIA/IA

### 3.1 Karar gerekçesi (niye HE'yi MVP'ye aldık?)

HE en kapsamlı program — onu çözebiliyorsak diğerleri trivial. Ayrıca pazar değeri en yüksek (€95B program, 7 yıl). Ancak en zor olduğu için 3 sprint'in büyük kısmı buna gidiyor; diğerleri HE altyapısı üzerinden çıkıyor.

### 3.2 Bölüm yapısı (Standard Application Form Part B)

```python
sections = ["excellence", "impact", "implementation"]

subsection_map = {
    "excellence": [
        "1.1_objectives_and_ambition",
        "1.2_methodology",
        "1.3_state_of_the_art",
        "1.4_open_science",
    ],
    "impact": [
        "2.1_pathways_to_impact",
        "2.2_measures_to_maximise_impact",
        "2.3_summary_canvas",
    ],
    "implementation": [
        "3.1_work_plan_and_resources",
        "3.2_capacity_of_participants",
        "3.3_consortium_as_a_whole",
    ],
}

# Sayfa limiti: RIA/IA için 45, EIC için 20, MSCA için 10 — MVP'de 45 sabit
page_limit = 45
```

### 3.3 Brief schema

Brief formu HE için şu alanları toplar — fazla soru sormuyoruz çünkü brief yazımı kullanıcı için zaten yorucu, eksik bilgi RAG ile telafi ediliyor:

```python
def get_brief_schema(self) -> BriefSchema:
    return BriefSchema(sections=[
        {
            "title_tr": "Proje Özü",
            "title_en": "Project Core",
            "fields": [
                BriefField(key="title", label_tr="Proje başlığı", label_en="Project title",
                          type="text", max_length=200),
                BriefField(key="acronym", label_tr="Akronim", label_en="Acronym",
                          type="text", max_length=15, required=False),
                BriefField(key="problem_statement",
                          label_tr="Çözmeye çalıştığınız problem (200-400 kelime)",
                          label_en="Problem you're solving (200-400 words)",
                          type="textarea", max_length=3000),
                BriefField(key="proposed_solution",
                          label_tr="Önerdiğiniz çözüm — teknik yaklaşım",
                          label_en="Your proposed solution — technical approach",
                          type="textarea", max_length=3000),
                BriefField(key="trl_current",
                          label_tr="Şu anki TRL", label_en="Current TRL",
                          type="select",
                          options=[{"value": i, "label": f"TRL {i}"} for i in range(1, 10)]),
                BriefField(key="trl_target",
                          label_tr="Proje sonu hedef TRL", label_en="Target TRL at project end",
                          type="select",
                          options=[{"value": i, "label": f"TRL {i}"} for i in range(1, 10)]),
            ]
        },
        {
            "title_tr": "Konsorsiyum",
            "title_en": "Consortium",
            "fields": [
                BriefField(key="role",
                          label_tr="Sizin rolünüz", label_en="Your role",
                          type="select",
                          options=[
                              {"value": "coordinator", "label_tr": "Koordinatör", "label_en": "Coordinator"},
                              {"value": "partner", "label_tr": "Ortak", "label_en": "Partner"},
                          ]),
                BriefField(key="partners",
                          label_tr="Ortaklar (her satıra bir tane: İsim, Ülke, Tip)",
                          label_en="Partners (one per line: Name, Country, Type)",
                          type="textarea", max_length=2000,
                          help_text_tr="Min 3 ortak, 3 farklı AB MS/AC ülkesinden zorunlu",
                          help_text_en="Min 3 partners from 3 different EU MS/AC required"),
            ]
        },
        {
            "title_tr": "Etki ve Bütçe",
            "title_en": "Impact & Budget",
            "fields": [
                BriefField(key="target_users",
                          label_tr="Hedef kullanıcılar/paydaşlar",
                          label_en="Target users/stakeholders",
                          type="textarea", max_length=1500),
                BriefField(key="market_size",
                          label_tr="Pazar büyüklüğü (TAM/SAM/SOM)",
                          label_en="Market size (TAM/SAM/SOM)",
                          type="textarea", max_length=1000, required=False),
                BriefField(key="budget_request_eur",
                          label_tr="Talep edilen bütçe (EUR)",
                          label_en="Requested budget (EUR)",
                          type="currency"),
                BriefField(key="duration_months",
                          label_tr="Proje süresi (ay)",
                          label_en="Project duration (months)",
                          type="number"),
            ]
        },
    ])
```

### 3.4 DOCX şablonu

EC'nin resmi "Standard Application Form Part B" şablonunu kullanıyoruz. Template dosyaları:

```
apps/api/src/programs/horizon_eu_ria/templates/
├── ria_part_b_2026.docx          # base template (footer, header, styling)
├── ria_part_b_2026_with_lump_sum.docx  # lump sum variant
└── README.md                      # template versiyon notları
```

Export `python-docx` ile yapılır. Reasoning: docxtpl Jinja2 sintaksı destekliyor ama complex tablolar (WP table, budget) için programatik kontrol gerekli; python-docx daha verbose ama stabil.

```python
from docx import Document
from docx.shared import Pt, Cm

async def export_docx(self, proposal: dict) -> bytes:
    template_path = self._select_template(proposal)
    doc = Document(template_path)

    # Headers and metadata
    self._fill_metadata(doc, proposal)

    # Section 1: Excellence
    excellence_md = proposal["draft"]["excellence_md"]
    self._render_markdown_to_docx(doc, excellence_md, start_heading="1. Excellence")

    # Section 2: Impact
    self._render_markdown_to_docx(doc, proposal["draft"]["impact_md"],
                                   start_heading="2. Impact")

    # Section 3: Implementation (includes WP table, Gantt, budget)
    self._render_implementation(doc, proposal)

    # AI disclosure (page 32 — auto-positioned by template bookmark)
    self._fill_ai_disclosure(doc, proposal["ai_disclosure_text"])

    # Bibliography (numbered references)
    self._render_bibliography(doc, proposal["bibliography"])

    # Validate page count post-render
    page_count = self._estimate_pages(doc)
    if page_count > 45:
        # Don't block, but record warning
        proposal.setdefault("metadata", {})["page_count_warning"] = page_count

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
```

Markdown→DOCX render'ı için custom converter — mevcut kütüphaneler (mistune+ext) HE'nin spesifik styling ihtiyacını karşılamıyor (örn. caption styling, WP table formatting). 600-800 satırlık bir converter yazıyoruz, Sprint 2 task.

### 3.5 Lump sum bütçe (HE 2024+ çoğu çağrıda zorunlu)

EC lump sum modeli: bütçe WP başına sabit, fatura toplama yok. Bu da Excel template'i complex yapıyor — EC'nin resmi `LumpSumBudget.xlsx`'inde 12 sayfa, makro (XLSM) içeriyor.

**Karar:** Makroları reproduce etmiyoruz. EC'nin resmi şablonunu indirip içine sadece veri yazıyoruz (`openpyxl` ile cell update). Makrolar Excel'de açıldığında çalışır.

```python
async def export_xlsx_budget(self, proposal: dict) -> bytes:
    template = openpyxl.load_workbook("templates/lump_sum_budget_2026.xlsx",
                                       keep_vba=True)
    ws_partners = template["Partners"]

    # Fill partner data
    for idx, partner in enumerate(proposal["budget"]["by_partner"], start=2):
        ws_partners.cell(row=idx, column=1, value=partner["name"])
        ws_partners.cell(row=idx, column=2, value=partner["country"])
        ws_partners.cell(row=idx, column=3, value=partner["entity_type"])

    # Fill WP costs
    ws_wp = template["WP Costs"]
    for wp in proposal["budget"]["by_wp"]:
        # ... cell-by-cell fill
        pass

    buf = BytesIO()
    template.save(buf)
    return buf.getvalue()
```

Risk: EC her yıl şablonu güncelliyor. Mitigation: template versioning (`lump_sum_budget_2026.xlsx`, `_2027.xlsx`), runtime'da çağrı yılına göre seç.

### 3.6 Validation kuralları

```python
def validate_draft(self, draft: dict, metadata: CallMetadata) -> list[ValidationIssue]:
    issues = []

    # Sayfa limiti
    estimated_pages = self._estimate_pages_from_markdown(draft)
    if estimated_pages > 45:
        issues.append(ValidationIssue(
            severity="blocker",
            section=None,
            code="page_limit_exceeded",
            message_tr=f"Toplam sayfa sayısı {estimated_pages}, limit 45.",
            message_en=f"Total pages {estimated_pages}, limit is 45.",
            suggestion="Trim Excellence section first (typically over-written)"
        ))

    # Required subsections
    for section, subsections in self.subsection_map.items():
        section_md = draft.get(f"{section}_md", "")
        for sub in subsections:
            heading_pattern = self._heading_for_subsection(sub)
            if heading_pattern not in section_md:
                issues.append(ValidationIssue(
                    severity="blocker",
                    section=section,
                    code="missing_subsection",
                    message_tr=f"{sub} bölümü eksik.",
                    message_en=f"{sub} subsection missing.",
                ))

    # Konsorsiyum yeterliliği
    partners = draft.get("brief", {}).get("partners", "")
    partner_lines = [p for p in partners.split("\n") if p.strip()]
    if len(partner_lines) < 3:
        issues.append(ValidationIssue(
            severity="blocker",
            section="implementation",
            code="insufficient_consortium",
            message_tr="Min 3 ortak gerekli (3 farklı AB MS/AC ülkesinden).",
            message_en="Minimum 3 partners required (from 3 different EU MS/AC).",
        ))

    # DNSH check (Cluster 4'te zorunlu değil ama yazmak iyi)
    impact_md = draft.get("impact_md", "")
    if "DNSH" not in impact_md and "do no significant harm" not in impact_md.lower():
        issues.append(ValidationIssue(
            severity="warning",
            section="impact",
            code="missing_dnsh",
            message_tr="DNSH (Do No Significant Harm) bahsi yok. Cluster 4'te zorunlu değil ama önerilir.",
            message_en="DNSH not mentioned. Not mandatory for Cluster 4 but recommended.",
        ))

    # Gender dimension
    if "gender" not in (draft.get("excellence_md", "") + draft.get("impact_md", "")).lower():
        issues.append(ValidationIssue(
            severity="warning",
            section="excellence",
            code="missing_gender_dimension",
            message_tr="Gender dimension bahsi yok. HE evaluation criteria gereği önerilir.",
            message_en="Gender dimension not addressed. Recommended per HE evaluation criteria.",
        ))

    # AI disclosure (page 32) — handled by ComplianceReviewer separately
    return issues
```

---

## 4. TÜBİTAK 1501 (Sanayi AR-GE)

### 4.1 Karar gerekçesi

Türkiye pazarında en yüksek hacimli program — yıllık 2000+ başvuru. Bluedev'in en hızlı ROI sağlayacağı program. AGY100 form yapısı standart, parsing zor değil.

### 4.2 Form yapısı (AGY100 — Proje Öneri Bilgileri Formu)

PRODİS'te gerçek form 28 alandan oluşuyor. Çekirdek bölümler:

```python
sections = ["excellence", "impact", "implementation"]  # bizim normalize ettiğimiz isimler

subsection_map = {
    "excellence": [
        "B1_proje_konusu_ve_amaclari",
        "B2_yenilikci_yonleri",
        "B3_yontem_ve_teknik",
        "B4_literature_review",        # technical literature
    ],
    "impact": [
        "C1_ekonomik_ve_ulusal_kazanim",
        "C2_yaygin_etki",
        "C3_pazar_analizi",
    ],
    "implementation": [
        "D1_is_paketleri",
        "D2_zaman_planlamasi",
        "D3_butce",
        "D4_proje_yonetimi_ve_riskler",
    ],
}

page_limit = None  # TÜBİTAK katı limit koymuyor ama 50-80 sayfa öneriliyor
```

### 4.3 PRODİS entegrasyonu

**TÜBİTAK'ın resmi API'si yok.** Bu MVP için kabul edilen bir kısıt. Çıktı stratejisi:

1. DOCX'i Bluedev'in geliştirdiği AGY100-uyumlu şablonda üret
2. Kullanıcı PRODİS'e manuel kopyala-yapıştır yapar (alan-bazlı)
3. Ekstra: kopyalamayı kolaylaştırmak için her alan için ayrı clipboard butonu

UI'da `/proposals/[id]/export/prodis` sayfası: alan-alan görünüm, her alanın yanında "Kopyala" butonu. Bu, PRODİS portalına yapıştırırken iş akışını yarıya indirir.

### 4.4 Bütçe yapısı (fatura-bazlı)

TÜBİTAK lump sum kullanmıyor — fatura-bazlı, kalemli bütçe (M1: personel, M2: alet/teçhizat, M3: hizmet alımı, M4: malzeme, M5: yurt içi/dışı seyahat, M6: danışmanlık, vb.).

Excel export `openpyxl` ile, kendi şablonumuz (TÜBİTAK resmi şablonu yok internet'te, bu yüzden TÜBİTAK Mali Rapor formundan reverse-engineer ettik).

```python
async def export_xlsx_budget(self, proposal: dict) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "AGY100 Bütçe"

    # M1-M6 kalemlerini doldur
    headers = ["Gider Kalemi", "Açıklama", "Miktar", "Birim Fiyat", "Toplam (TL)", "Ay"]
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header).font = Font(bold=True)

    row = 2
    for category in ["M1", "M2", "M3", "M4", "M5", "M6"]:
        items = proposal["budget"]["by_category"].get(category, [])
        for item in items:
            ws.cell(row=row, column=1, value=category)
            ws.cell(row=row, column=2, value=item["description"])
            ws.cell(row=row, column=3, value=item["quantity"])
            ws.cell(row=row, column=4, value=item["unit_price"])
            ws.cell(row=row, column=5, value=item["total"])
            ws.cell(row=row, column=6, value=item["month"])
            row += 1

    # Toplam satırı
    ws.cell(row=row, column=1, value="TOPLAM").font = Font(bold=True)
    ws.cell(row=row, column=5, value=f"=SUM(E2:E{row-1})")

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

### 4.5 Validation

TÜBİTAK için kritik kurallar:
- KOBİ tanımı (250 personel altı, 40M TL ciro altı)
- Proje süresi 18-36 ay arası
- Bütçe min 100K TL, max yok ama 5M TL üstünde sıkı değerlendirme
- B2 (yenilikçi yönleri) bölümü zayıfsa direkt elenir → bu bölüm için min 800 kelime şartı koyduk

```python
def validate_draft(self, draft, metadata) -> list[ValidationIssue]:
    issues = []

    # B2 minimum
    b2_text = self._extract_subsection(draft, "B2_yenilikci_yonleri")
    if len(b2_text.split()) < 800:
        issues.append(ValidationIssue(
            severity="blocker",
            section="excellence",
            code="b2_too_short",
            message_tr="B2 (Yenilikçi Yönler) en az 800 kelime olmalı. TÜBİTAK değerlendirmesinde en kritik bölüm.",
            message_en="B2 (Innovative Aspects) must be at least 800 words. Most critical section in TÜBİTAK eval.",
            suggestion="Mevcut state-of-the-art'ı detaylandırın, sizin yaklaşımınızın farkını net belirtin.",
        ))

    # Süre kontrolü
    duration = draft.get("brief", {}).get("duration_months", 0)
    if duration < 18 or duration > 36:
        issues.append(ValidationIssue(
            severity="blocker",
            section="implementation",
            code="invalid_duration",
            message_tr=f"Proje süresi {duration} ay. TÜBİTAK 1501'de 18-36 ay arası zorunlu.",
            message_en=f"Project duration {duration} months. TÜBİTAK 1501 requires 18-36 months.",
        ))

    # KOBİ kontrolü (brief'ten gelir)
    company_size = draft.get("brief", {}).get("company_size", {})
    if company_size.get("employees", 0) > 250:
        issues.append(ValidationIssue(
            severity="warning",
            code="not_kobi",
            message_tr="Şirket KOBİ tanımına girmiyor. 1501 büyük firmaları da kabul ediyor ama destek oranı %40'a düşer.",
            message_en="Company exceeds SME definition. 1501 accepts large firms but support rate drops to 40%.",
        ))

    return issues
```

---

## 5. TÜBİTAK 1507 (KOBİ AR-GE Başlangıç)

1501'in basitleştirilmiş hali — KOBİ-only, max 24 ay, max 1.500.000 TL bütçe (2026 değerleri için hafta 1'de doğrulanacak).

### 5.1 Tasarım kararı

1507 modülü 1501'i miras alır, sadece farklı parametreleri override eder. Tek bir base class TÜBİTAKBase var, 1501 ve 1507 ondan türüyor:

```python
# apps/api/src/programs/_tubitak_base.py
class TUBITAKBaseModule(BaseProgramModule):
    """Common logic for TÜBİTAK programmes."""
    funder = "TÜBİTAK"
    language = "tr"

    def parse_call(self, call_text, metadata):
        # AGY100 form yapısı ortak
        ...

    def get_brief_schema(self):
        # %80 ortak
        ...

# apps/api/src/programs/tubitak_1501.py
class TUBITAK1501Module(TUBITAKBaseModule):
    program_id = "tubitak_1501"
    name_tr = "TÜBİTAK 1501 Sanayi AR-GE"
    name_en = "TÜBİTAK 1501 Industrial R&D"
    duration_min = 18
    duration_max = 36
    budget_max_tl = None
    requires_kobi = False

# apps/api/src/programs/tubitak_1507.py
class TUBITAK1507Module(TUBITAKBaseModule):
    program_id = "tubitak_1507"
    name_tr = "TÜBİTAK 1507 KOBİ AR-GE Başlangıç"
    duration_min = 12
    duration_max = 24
    budget_max_tl = 1_500_000      # Hafta 1 doğrulanacak
    requires_kobi = True            # KOBİ olmayan başvuramaz

    def validate_draft(self, draft, metadata):
        issues = super().validate_draft(draft, metadata)
        # Bütçe limiti
        budget_total = draft.get("budget", {}).get("total_tl", 0)
        if budget_total > self.budget_max_tl:
            issues.append(ValidationIssue(
                severity="blocker",
                code="budget_exceeded",
                message_tr=f"Bütçe {budget_total:,.0f} TL. 1507 limiti {self.budget_max_tl:,.0f} TL.",
                message_en=f"Budget {budget_total:,.0f} TL exceeds 1507 limit {self.budget_max_tl:,.0f} TL.",
            ))
        return issues
```

Bu kalıbı diğer programlar için de tekrarlayacağız (HorizonEuropeBase, KOSGEBBase) — Faz 2 genişlemesi 10x kolaylaşacak.

---

## 6. KOSGEB AR-GE/Yenilik

### 6.1 Karar gerekçesi

KOSGEB Türkiye'de KOBİ'ler için ikinci en büyük hibe kaynağı. AR-GE programı 2024'te yenilendi (KBS — KOSGEB Bilgi Sistemi üzerinden). Form yapısı TÜBİTAK 1501'e benzer ama daha kısa, daha az teknik derinlik bekleniyor.

### 6.2 Önemli farklar

- Dil: Türkçe zorunlu, terimler bazen TÜBİTAK'tan farklı (örn. "yenilikçi" yerine "yenilik niteliği")
- Bütçe yapısı: 6 değil 4 kalem (Personel, Makine-Teçhizat-Yazılım, Hizmet Alımı, Diğer)
- Süre: 12-36 ay
- Min işletme yaşı: 1 yıl (1501'de yok)
- KBS'de form tabanlı, PDF export yok — DOCX'i kullanıcı manuel KBS'ye girer (1501 ile aynı durum)

```python
class KOSGEBARGEModule(BaseProgramModule):
    program_id = "kosgeb_arge"
    funder = "KOSGEB"
    language = "tr"
    duration_min = 12
    duration_max = 36
    requires_kobi = True
    min_company_age_years = 1

    sections = ["excellence", "impact", "implementation"]
    subsection_map = {
        "excellence": [
            "K1_proje_konusu",
            "K2_yenilik_niteligi",
            "K3_uygulanacak_yontem",
        ],
        "impact": [
            "L1_beklenen_ekonomik_kazanim",
            "L2_pazar_analizi",
        ],
        "implementation": [
            "M1_is_zaman_plani",
            "M2_butce",
            "M3_riskler",
        ],
    }
```

---

## 7. Cascade Funding & NLnet

### 7.1 Karar gerekçesi

Cascade Funding (NGI Zero, NGI Sargasso, FSTP — Financial Support to Third Parties) AB'nin "yeniden dağıtılan" hibe mekanizması. Tek tek başvuru basit (1500-3000 EUR for NLnet, 50K-300K EUR for NGI Zero), form kısa (5-10 sayfa). Pazar fırsatı: hızlı kazanılabilen, KOBİ'ler için "ısınma" başvurusu — Bluedev'in daha sonra HE'ye geçirebileceği müşteri kapısı.

### 7.2 Form yapısı

NLnet ve NGI Zero için ortak iskelet:

```python
sections = ["project", "team", "budget"]
subsection_map = {
    "project": [
        "abstract",
        "main_question",
        "current_status",
        "experience",
        "comparison",  # how it compares to existing solutions
    ],
    "team": [
        "members",
        "expertise",
    ],
    "budget": [
        "tasks_and_costs",  # NLnet uses simple task-cost mapping
    ],
}
page_limit = 10
```

### 7.3 Çoklu portal stratejisi

NGI Zero, NGI Sargasso, NGI TALER, EUREKA Cascade, Switch ON — her biri farklı portal, farklı form. **Karar:** MVP'de yalnızca **NLnet** (NGI Zero ve NGI Sargasso ortak portal) destekliyoruz. Diğer FSTP'leri Faz 2'de program-spesifik alt-modül olarak ekleriz:

```
apps/api/src/programs/cascade_funding/
├── __init__.py
├── base.py                 # CascadeFundingModule (default = NLnet)
├── nlnet/
│   ├── __init__.py
│   ├── form_schema.py
│   └── templates/
│       └── nlnet_application.docx
└── ngi_taler/              # Faz 2
```

### 7.4 Çağrı tarama

NLnet RSS feed kullanılabilir: `https://nlnet.nl/news/feed.atom`. NGI Zero açık çağrıları sayfasından scraping (HTML, BeautifulSoup). Daily Celery task.

---

## 8. Plugin sistem testi

Sprint 1 sonunda yapılacak smoke test:

```python
# apps/api/tests/programs/test_registry.py
def test_all_programmes_implement_interface():
    from src.programs import REGISTRY
    for prog_id, module in REGISTRY.items():
        assert isinstance(module, BaseProgramModule)
        # Brief schema valid
        schema = module.get_brief_schema()
        assert len(schema.sections) > 0
        # Templates exist
        assert os.path.exists(module.get_template_path())
        # Prompts exist for each agent
        for agent in ["excellence_writer", "impact_writer", "implementation_writer"]:
            prompt_path = module.get_prompt_path(agent)
            assert os.path.exists(prompt_path), f"Missing prompt: {prompt_path}"

@pytest.mark.parametrize("prog_id", REGISTRY.keys())
async def test_program_e2e(prog_id):
    """Each program must produce a complete draft from a fixture brief."""
    fixture = load_fixture(f"briefs/{prog_id}_minimal.json")
    proposal = await create_proposal(prog_id, fixture)
    job = await trigger_generation(proposal.id)
    await wait_for_completion(job.id, timeout=900)
    final = await get_proposal(proposal.id)
    assert final.status in ("draft_complete", "draft_complete_with_issues")
    assert all(final.draft.get(f"{s}_md") for s in REGISTRY[prog_id].sections)
```

---

**Sonraki dosya:** `08-frontend-spec.md` — Next.js sayfaları ve UX akışı.