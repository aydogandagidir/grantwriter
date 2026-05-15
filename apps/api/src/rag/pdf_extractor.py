"""PDF text extraction for funder-guideline ingestion (Faz 3).

We use ``pypdf`` (pure-Python, no system deps) and pair it with a
heuristic section detector. The extractor does NOT try to recover
column layout, footnote anchoring, or table structure — funder
guidelines are mostly continuous prose, and the few that aren't (e.g.
HE evaluation criteria tables) survive as flat text with line breaks
that the chunker treats as paragraph separators.

The section heuristic looks for short top-of-page lines that "look like
headings": all-caps OR Title Case, no terminal punctuation, ≤ ``80``
chars, and bordered by blank lines. When found, the heading becomes the
section label for the chunks that follow. Pages without a heading
inherit the previous one (or fall back to ``"body"``).

Output is shaped so the existing :func:`src.rag.chunker.chunk_text` can
consume each section's text directly.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)

# Heuristic guard rails — tuned against a sample of HE work programmes,
# TÜBİTAK 1501 guidelines, and NLnet form text.
_MAX_HEADING_CHARS: Final[int] = 80
_MIN_HEADING_CHARS: Final[int] = 3
_DEFAULT_SECTION: Final[str] = "body"
_MAX_PAGES_DEFAULT: Final[int] = 300

# Lines we treat as page chrome and strip wholesale before paragraph
# stitching. Funder PDFs love these footer/header patterns; the
# chunker treats them as paragraphs otherwise and inflates the chunk
# count without adding signal.
_CHROME_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^\s*(page|sayfa)\s+\d+(\s*/\s*\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),
    re.compile(r"^\s*-\s*\d+\s*-\s*$"),  # "- 5 -"
)

# Terminal-punctuation set — a "heading" doesn't end in any of these.
_TERMINAL_PUNCT: Final[frozenset[str]] = frozenset(".:;,!?…")


class PDFPage(BaseModel):
    """One extracted PDF page."""

    model_config = ConfigDict(frozen=True)

    index: int
    """Zero-based page index in the source PDF."""

    text: str
    """Page text with chrome (page-N footers) stripped, paragraphs preserved."""

    is_blank: bool
    """``True`` iff stripped text is empty or near-empty (< 8 chars)."""


class PDFSection(BaseModel):
    """A logical section produced by the heading-detection pass."""

    model_config = ConfigDict(frozen=True)

    title: str
    """Heuristic section title; ``"body"`` for the implicit default section."""

    text: str
    """Joined paragraphs belonging to this section, blank-line separated."""

    page_start: int
    """First page (zero-based) where this section's content appears."""

    page_end: int
    """Last page (zero-based) where this section's content appears (inclusive)."""


class PDFDocument(BaseModel):
    """Result of one PDF extraction."""

    model_config = ConfigDict(frozen=True)

    page_count: int
    pages: list[PDFPage] = Field(default_factory=list)
    sections: list[PDFSection] = Field(default_factory=list)
    full_text: str
    """All non-blank page text concatenated (legacy single-doc consumers)."""

    truncated: bool = False
    """``True`` if extraction stopped early at ``max_pages``."""


def extract_pdf(
    content: bytes,
    *,
    max_pages: int = _MAX_PAGES_DEFAULT,
) -> PDFDocument:
    """Extract structured text from a PDF byte string.

    Raises ``PdfReadError`` (re-raised from pypdf) when the bytes are not
    a valid PDF. Pages that pypdf cannot decode contribute an empty
    ``PDFPage`` with ``is_blank=True``; the rest of the document still
    gets processed.
    """

    if not content:
        raise PdfReadError("empty pdf bytes")

    try:
        reader = PdfReader(io.BytesIO(content))
    except PdfReadError:
        raise

    pdf_pages: list[PDFPage] = []
    total = len(reader.pages)
    take = min(total, max_pages)

    for idx in range(take):
        try:
            raw = reader.pages[idx].extract_text() or ""
        except Exception:
            # pypdf raises a grab-bag of exceptions on malformed pages
            # (KeyError on missing /Font, NotImplementedError on rare
            # encodings, etc.). Log and skip — we keep the rest of the
            # document.
            logger.warning("pdf_extract_page_failed", extra={"page": idx})
            raw = ""
        cleaned = _strip_chrome(raw)
        pdf_pages.append(
            PDFPage(
                index=idx,
                text=cleaned,
                is_blank=len(cleaned.strip()) < 8,
            )
        )

    sections = _detect_sections(pdf_pages)
    full_text = "\n\n".join(p.text for p in pdf_pages if not p.is_blank).strip()

    return PDFDocument(
        page_count=total,
        pages=pdf_pages,
        sections=sections,
        full_text=full_text,
        truncated=take < total,
    )


def _strip_chrome(raw: str) -> str:
    """Drop running-header / page-number lines; preserve paragraph breaks."""

    if not raw:
        return ""
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if any(pat.match(stripped) for pat in _CHROME_PATTERNS):
            continue
        kept.append(stripped)

    # Collapse runs of blank lines down to a single blank — that's the
    # paragraph separator the chunker keys off.
    out: list[str] = []
    blank = False
    for ln in kept:
        if ln:
            out.append(ln)
            blank = False
        else:
            if not blank and out:
                out.append("")
            blank = True
    return "\n".join(out).strip()


def _looks_like_heading(line: str) -> bool:
    """Return ``True`` for short, capital-leaning, non-sentence lines."""

    stripped = line.strip()
    if not (_MIN_HEADING_CHARS <= len(stripped) <= _MAX_HEADING_CHARS):
        return False
    if stripped[-1] in _TERMINAL_PUNCT:
        return False

    letters = [c for c in stripped if c.isalpha()]
    if len(letters) < 3:
        return False

    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    # All-caps OR Title-Case-ish (most words start with uppercase).
    if upper_ratio >= 0.65:
        return True

    words = [w for w in re.split(r"\s+", stripped) if w]
    title_words = sum(1 for w in words if w[:1].isupper())
    if len(words) >= 2 and title_words / len(words) >= 0.7:
        return True

    # Numbered heading: "1. Excellence", "2.1 Impact"
    return bool(re.match(r"^\d+(\.\d+)*\.?\s+\S", stripped) and stripped[0].isdigit())


def _detect_sections(pages: list[PDFPage]) -> list[PDFSection]:
    """Walk pages, emit one ``PDFSection`` per detected heading."""

    if not pages:
        return []

    sections: list[PDFSection] = []
    current_title = _DEFAULT_SECTION
    current_body: list[str] = []
    current_start = 0
    current_end = 0

    def _flush() -> None:
        nonlocal current_body, current_start, current_end
        body = "\n\n".join(b for b in current_body if b.strip()).strip()
        if not body:
            current_body = []
            return
        sections.append(
            PDFSection(
                title=current_title,
                text=body,
                page_start=current_start,
                page_end=current_end,
            )
        )
        current_body = []

    for page in pages:
        if page.is_blank:
            current_end = page.index
            continue
        # Look at the first few non-empty lines as heading candidates.
        lines = [ln for ln in page.text.splitlines() if ln.strip()]
        if lines and _looks_like_heading(lines[0]):
            _flush()
            current_title = lines[0].strip()
            current_start = page.index
            current_end = page.index
            body_lines = lines[1:]
        else:
            current_end = page.index
            body_lines = lines

        if body_lines:
            current_body.append("\n".join(body_lines))

    _flush()

    if not sections:
        # No heading detected anywhere — wrap the whole doc as one
        # section so the chunker still has something to consume.
        body = "\n\n".join(p.text for p in pages if not p.is_blank).strip()
        if body:
            sections.append(
                PDFSection(
                    title=_DEFAULT_SECTION,
                    text=body,
                    page_start=0,
                    page_end=pages[-1].index,
                )
            )

    return sections


__all__ = ["PDFDocument", "PDFPage", "PDFSection", "extract_pdf"]
