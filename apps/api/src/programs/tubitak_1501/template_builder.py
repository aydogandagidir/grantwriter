"""Build a fresh AGY100-style DOCX scaffold programmatically.

The committed binary at ``templates/agy100_2026.docx`` is produced by
running this module's ``main()`` (or :func:`build_template`) once. The
runtime export path opens that committed template, fills it with
proposal content, and returns bytes. Re-build only when section
structure or boilerplate copy changes.

Why a real on-disk template?
- Header/footer/margins/font live in the .docx, not in code, so a
  designer can swap them without a code change.
- Keeps the "render proposal" code path straightforward — it never
  builds the chrome of the document, just the body.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentT
from docx.shared import Cm, Pt

# Section structure mirrors docs/07 §4.2 — TÜBİTAK AGY100.
SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "B. Bilimsel ve Teknolojik Detaylar",
        [
            ("B1", "Proje Konusu ve Amaçları"),
            ("B2", "Yenilikçi Yönler"),
            ("B3", "Yöntem ve Teknik"),
            ("B4", "Literatür Taraması"),
        ],
    ),
    (
        "C. Yaygın Etki ve Pazar",
        [
            ("C1", "Ekonomik ve Ulusal Kazanım"),
            ("C2", "Yaygın Etki"),
            ("C3", "Pazar Analizi"),
        ],
    ),
    (
        "D. Uygulama",
        [
            ("D1", "İş Paketleri"),
            ("D2", "Zaman Planlaması"),
            ("D3", "Bütçe"),
            ("D4", "Proje Yönetimi ve Riskler"),
        ],
    ),
]

PLACEHOLDER_TEXT = "[Bu bölüm proje üretimi sırasında doldurulacaktır.]"


def build_template() -> DocumentT:
    """Return a fresh Document with the AGY100 scaffold (no proposal content)."""

    doc = Document()

    # Page margins — TÜBİTAK suggests 2.5 cm all round.
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    # Default body font — 11 pt, single line spacing.
    styles = doc.styles
    if "Normal" in styles:
        normal = styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)

    # Cover heading.
    title = doc.add_heading("TÜBİTAK 1501 — Sanayi AR-GE Projeleri", level=0)
    for run in title.runs:
        run.bold = True

    subtitle = doc.add_paragraph()
    subtitle.add_run("AGY100 — Proje Öneri Bilgileri Formu").italic = True

    # Static placeholder for proposal title (export step overwrites).
    title_p = doc.add_paragraph()
    title_p.add_run("Proje Başlığı: ").bold = True
    title_p.add_run("[proje_basligi]")

    # Section scaffolding.
    for section_title, subsections in SECTIONS:
        doc.add_heading(section_title, level=1)
        for code, label in subsections:
            doc.add_heading(f"{code} {label}", level=2)
            doc.add_paragraph(PLACEHOLDER_TEXT)

    # Budget table placeholder — single header row + 1 sample row.
    doc.add_heading("Bütçe Tablosu", level=1)
    table = doc.add_table(rows=1, cols=6)
    table.style = "Light Grid Accent 1"
    header = table.rows[0].cells
    for idx, label in enumerate(
        ["Gider Kalemi", "Açıklama", "Miktar", "Birim Fiyat (TL)", "Toplam (TL)", "Ay"]
    ):
        header[idx].text = label
        header[idx].paragraphs[0].runs[0].bold = True
    placeholder_row = table.add_row().cells
    placeholder_row[0].text = "[M1-M6]"
    placeholder_row[1].text = "[açıklama]"
    placeholder_row[2].text = "[adet]"
    placeholder_row[3].text = "[birim fiyat]"
    placeholder_row[4].text = "[toplam]"
    placeholder_row[5].text = "[ay]"

    return doc


def to_bytes() -> bytes:
    """Convenience: build the template and return its bytes."""

    buf = BytesIO()
    build_template().save(buf)
    return buf.getvalue()


def main(target: Path | None = None) -> Path:
    """CLI entrypoint: write the template to its committed path."""

    target = target or (Path(__file__).resolve().parent / "templates" / "agy100_2026.docx")
    target.parent.mkdir(parents=True, exist_ok=True)
    build_template().save(str(target))
    return target


if __name__ == "__main__":
    written = main()
    print(f"wrote: {written}")


__all__ = ["SECTIONS", "build_template", "main", "to_bytes"]
