"""Regex-based citation extractor + best-effort author/year parser.

Three patterns cover the formats grant proposals actually use:
- ``[Author Year]`` / ``[Author et al., Year]`` (Latin + Turkish chars).
- ``[NN]`` numbered references.
- ``(Author Year)`` parenthesised in-line.

False positives here are cheap (the verifier flags fabricated downstream);
false negatives — citations the regex misses — are expensive because the
Hallucination Hunter never sees them.

Source-of-truth replacement for the regex that previously lived in
``src/agents/excellence_writer.py``. The writer agents now import
:func:`extract_citations` from here; the old name is re-exported there
for back-compat.
"""

from __future__ import annotations

import re
from typing import Any

from src.citations.base import Citation

# ── Patterns ────────────────────────────────────────────────────────────

# [Author Year], [Aydın et al., 2024], [Smith and Jones 2023]
# Year is required (4 digits + optional lowercase letter).
_BRACKET_AUTHOR_YEAR = re.compile(r"\[[A-ZÇĞİÖŞÜa-zçğıöşü][^\[\]]{1,80}\d{4}[a-z]?\]")
# [12] — 1 to 3 digits.
_BRACKET_NUMERIC = re.compile(r"\[\d{1,3}\]")
# (Smith 2023), (Aydın et al. 2024)
_PAREN_AUTHOR_YEAR = re.compile(r"\([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü\s.,&]{1,60}\d{4}[a-z]?\)")

# Splits a bracket / paren body into the parts we care about.
_AUTHOR_YEAR_SPLIT = re.compile(r"^\s*(?P<authors>.+?)\s*[,;\s]\s*(?P<year>\d{4}[a-z]?)\s*$")


def extract_citations(text: str) -> list[Citation]:
    """Return one :class:`Citation` per unique citation marker in ``text``.

    Order is preserved (first occurrence wins). Each result has:
    - ``raw_text`` = exactly what the regex captured (brackets/parens included)
    - ``authors`` + ``year`` populated when the format permits
    - ``doi`` / ``title`` left empty (the verifier resolves them)
    """

    seen: set[str] = set()
    out: list[Citation] = []
    for pattern in (_BRACKET_AUTHOR_YEAR, _BRACKET_NUMERIC, _PAREN_AUTHOR_YEAR):
        for match in pattern.findall(text):
            if match in seen:
                continue
            seen.add(match)
            authors, year = _parse_author_year(match)
            out.append(Citation(raw_text=match, authors=authors, year=year))
    return out


def parse_author_year(raw_text: str) -> dict[str, Any]:
    """Public wrapper used by the verifier when issuing Crossref queries."""

    authors, year = _parse_author_year(raw_text)
    return {"authors": authors, "year": year}


def _parse_author_year(raw_text: str) -> tuple[list[str], int | None]:
    """Split ``[Smith 2023]`` etc. into authors + year. Numbered refs
    (``[12]``) yield ``([], None)`` — they have no author/year info."""

    body = raw_text.strip().lstrip("[(").rstrip("])")
    if body.isdigit():
        return [], None

    match = _AUTHOR_YEAR_SPLIT.match(body)
    if not match:
        return [], None

    raw_authors = match.group("authors").strip()
    year_str = match.group("year").strip()
    try:
        year = int(year_str[:4])
    except ValueError:
        year = None

    # Normalise common compound forms: "Smith and Jones" / "Smith & Jones"
    # / "Smith et al."
    if " et al" in raw_authors.lower():
        primary = re.split(r"\s+et\s+al", raw_authors, flags=re.IGNORECASE)[0]
        authors = [primary.strip().rstrip(",.")]
    else:
        # Split on "and" / "&" / commas; drop empties.
        parts = re.split(r"\s+(?:and|&)\s+|,", raw_authors)
        authors = [p.strip() for p in parts if p.strip()]
    return authors, year


__all__ = ["extract_citations", "parse_author_year"]
