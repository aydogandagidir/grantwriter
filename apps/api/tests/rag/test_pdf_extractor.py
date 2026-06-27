"""PDFExtractor unit tests.

We exercise three layers separately:

  1. ``_strip_chrome`` and ``_looks_like_heading`` are pure string
     functions — table-driven tests cover the page-footer / heading
     heuristics without ever touching a PDF.
  2. ``_detect_sections`` consumes a list of synthetic ``PDFPage``s, so
     we can drive section grouping deterministically without depending
     on any PDF rendering library.
  3. ``extract_pdf`` gets a smoke test against a minimal hand-crafted
     PDF byte string and a malformed input — enough to prove the pypdf
     plumbing works end-to-end without committing megabytes of binary
     fixtures.
"""

from __future__ import annotations

import pytest
from pypdf.errors import PdfReadError
from src.rag.pdf_extractor import (
    PDFPage,
    _detect_sections,
    _looks_like_heading,
    _strip_chrome,
    extract_pdf,
)

# ── _strip_chrome ────────────────────────────────────────────────────


def test_strip_chrome_removes_page_n_of_m_footer() -> None:
    raw = "Body line one.\n\nPage 3 / 12\n\nBody line two."
    stripped = _strip_chrome(raw)
    assert "Page 3 / 12" not in stripped
    assert "Body line one." in stripped
    assert "Body line two." in stripped


def test_strip_chrome_removes_localized_page_marker() -> None:
    raw = "Heading\n\nSayfa 5\n\nBody."
    stripped = _strip_chrome(raw)
    assert "Sayfa 5" not in stripped
    assert "Body." in stripped


def test_strip_chrome_handles_dash_page_marker() -> None:
    raw = "Heading\n\n- 5 -\n\nBody."
    stripped = _strip_chrome(raw)
    assert "- 5 -" not in stripped


def test_strip_chrome_collapses_consecutive_blank_lines() -> None:
    raw = "A\n\n\n\nB\n\n\nC"
    stripped = _strip_chrome(raw)
    assert stripped == "A\n\nB\n\nC"


def test_strip_chrome_empty_input_returns_empty() -> None:
    assert _strip_chrome("") == ""


# ── _looks_like_heading ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "line, expected",
    [
        ("EVALUATION CRITERIA", True),  # all-caps short
        ("1. Excellence", True),  # numbered
        ("2.3 Implementation Plan", True),  # multi-level numbered
        ("Project Impact And Sustainability", True),  # Title Case
        ("This is a normal sentence ending with a period.", False),
        ("foo bar baz quux", False),  # lowercase
        ("A", False),  # too short
        (
            "An exceedingly long heading-like line that wraps past the eighty "
            "character limit we set internally",
            False,
        ),
        ("Heading: with a colon", False),  # ends with terminal punct
        ("123-AB", False),  # too few letters
    ],
)
def test_looks_like_heading(line: str, expected: bool) -> None:
    assert _looks_like_heading(line) is expected


# ── _detect_sections ─────────────────────────────────────────────────


def _page(index: int, text: str) -> PDFPage:
    return PDFPage(
        index=index,
        text=text,
        is_blank=not text.strip(),
    )


def test_detect_sections_picks_up_numbered_heading() -> None:
    pages = [
        _page(0, "1. Excellence\n\nThis section describes excellence."),
        _page(1, "2. Impact\n\nThis section describes impact."),
    ]
    sections = _detect_sections(pages)
    titles = [s.title for s in sections]
    assert "1. Excellence" in titles
    assert "2. Impact" in titles
    bodies = "\n".join(s.text for s in sections)
    assert "describes excellence" in bodies
    assert "describes impact" in bodies


def test_detect_sections_picks_up_all_caps_heading() -> None:
    pages = [
        _page(0, "EVALUATION CRITERIA\n\nCriterion text follows here."),
    ]
    sections = _detect_sections(pages)
    assert sections[0].title == "EVALUATION CRITERIA"
    assert "Criterion text" in sections[0].text


def test_detect_sections_falls_back_to_body_when_no_heading() -> None:
    pages = [
        _page(0, "Just regular body text without any heading. More body."),
    ]
    sections = _detect_sections(pages)
    assert len(sections) == 1
    assert sections[0].title == "body"
    assert "regular body text" in sections[0].text


def test_detect_sections_skips_blank_pages() -> None:
    pages = [
        _page(0, "1. Scope\n\nFirst page body."),
        _page(1, ""),  # blank
        _page(2, "Second page body, no heading."),
    ]
    sections = _detect_sections(pages)
    bodies = "\n".join(s.text for s in sections)
    # Blank page in the middle doesn't break grouping; both bodies land
    # under the active section.
    assert "First page body." in bodies
    assert "Second page body" in bodies


def test_detect_sections_tracks_page_range() -> None:
    pages = [
        _page(0, "1. Excellence\n\nPage 0 body."),
        _page(1, "Continuation of section 1 on page 1."),
        _page(2, "2. Impact\n\nPage 2 body."),
    ]
    sections = _detect_sections(pages)
    by_title = {s.title: s for s in sections}
    assert by_title["1. Excellence"].page_start == 0
    assert by_title["1. Excellence"].page_end == 1
    assert by_title["2. Impact"].page_start == 2


def test_detect_sections_empty_pages_returns_empty() -> None:
    assert _detect_sections([]) == []


# ── extract_pdf smoke ───────────────────────────────────────────────


# A minimal valid PDF — generated once with pypdf.PdfWriter, kept here
# as a constant so the test doesn't need any PDF-writing dependency at
# runtime. Just one blank page; we verify the extractor can parse it,
# returns the right page count, and doesn't crash on the
# no-text-detected path.
_BLANK_ONE_PAGE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
    b"/Resources<<>>/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 0>>stream\nendstream\nendobj\n"
    b"xref\n0 5\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000050 00000 n \n"
    b"0000000094 00000 n \n"
    b"0000000168 00000 n \n"
    b"trailer<</Size 5/Root 1 0 R>>\n"
    b"startxref\n206\n"
    b"%%EOF\n"
)


def test_extract_pdf_empty_bytes_raises() -> None:
    with pytest.raises(PdfReadError):
        extract_pdf(b"")


def test_extract_pdf_invalid_bytes_raises() -> None:
    with pytest.raises(PdfReadError):
        extract_pdf(b"not a pdf at all")


def test_extract_pdf_blank_page_does_not_crash() -> None:
    doc = extract_pdf(_BLANK_ONE_PAGE_PDF)
    assert doc.page_count == 1
    assert len(doc.pages) == 1
    # No body text → no sections detected.
    assert doc.full_text == ""
    assert doc.sections == []


def test_extract_pdf_respects_max_pages() -> None:
    # Even a single-page PDF should honour max_pages=0 → no pages.
    doc = extract_pdf(_BLANK_ONE_PAGE_PDF, max_pages=0)
    assert doc.page_count == 1
    assert len(doc.pages) == 0
    assert doc.truncated is True
