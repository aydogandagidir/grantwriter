"""Build the AGY100 DOCX scaffold for TÜBİTAK 1507.

Identical to 1501's builder except for the cover heading. Both
delegate to :func:`~src.programs._tubitak_base.build_tubitak_template`.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx.document import Document as DocumentT

from src.programs._tubitak_base import build_tubitak_template

PROGRAMME_NAME = "TÜBİTAK 1507 — KOBİ AR-GE Başlangıç Destek Programı"


def build_template() -> DocumentT:
    return build_tubitak_template(programme_name=PROGRAMME_NAME)


def to_bytes() -> bytes:
    buf = BytesIO()
    build_template().save(buf)
    return buf.getvalue()


def main(target: Path | None = None) -> Path:
    target = target or (
        Path(__file__).resolve().parent / "templates" / "agy100_1507_2026.docx"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    build_template().save(str(target))
    return target


if __name__ == "__main__":
    written = main()
    print(f"wrote: {written}")


__all__ = ["PROGRAMME_NAME", "build_template", "main", "to_bytes"]
