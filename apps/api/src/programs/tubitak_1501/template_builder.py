"""Build the AGY100 DOCX scaffold for TÜBİTAK 1501.

Thin wrapper around :func:`build_tubitak_template` — provides the
1501-specific cover heading and target path. The committed binary at
``templates/agy100_2026.docx`` is produced by running ``main()`` once;
the runtime export path opens that binary.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx.document import Document as DocumentT

from src.programs._tubitak_base import (
    TEMPLATE_PLACEHOLDER_TEXT,
    TEMPLATE_SECTIONS,
    build_tubitak_template,
)

# Re-exports preserved for backward compat with existing tests.
PLACEHOLDER_TEXT = TEMPLATE_PLACEHOLDER_TEXT
SECTIONS = TEMPLATE_SECTIONS

PROGRAMME_NAME = "TÜBİTAK 1501 — Sanayi AR-GE Projeleri"


def build_template() -> DocumentT:
    """Return a fresh Document with the AGY100 scaffold for 1501."""

    return build_tubitak_template(programme_name=PROGRAMME_NAME)


def to_bytes() -> bytes:
    """Convenience: build and serialise the template."""

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


__all__ = [
    "PLACEHOLDER_TEXT",
    "PROGRAMME_NAME",
    "SECTIONS",
    "build_template",
    "main",
    "to_bytes",
]
