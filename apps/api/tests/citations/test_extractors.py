"""Citation extractor unit tests."""

from __future__ import annotations

from src.citations.extractors import extract_citations, parse_author_year


def test_extracts_three_marker_forms() -> None:
    text = (
        "Earlier work [Smith 2023] and [Aydın et al. 2024] established "
        "the baseline. Numbered references are also handled [12]. "
        "Parenthesised author-year too (Demir 2022)."
    )
    raws = [c.raw_text for c in extract_citations(text)]
    assert "[Smith 2023]" in raws
    assert "[Aydın et al. 2024]" in raws
    assert "[12]" in raws
    assert "(Demir 2022)" in raws


def test_year_required_rejects_garbage_brackets() -> None:
    text = "Talked to [bogus year missing] and to [a 2099] but [no year here]."
    raws = [c.raw_text for c in extract_citations(text)]
    assert "[bogus year missing]" not in raws
    assert "[no year here]" not in raws


def test_dedup_preserves_order_of_first_occurrence() -> None:
    text = (
        "First [Smith 2023]. Then [Jones 2024]. Then [Smith 2023] again. "
        "Then [Jones 2024] again."
    )
    raws = [c.raw_text for c in extract_citations(text)]
    assert raws == ["[Smith 2023]", "[Jones 2024]"]


def test_parse_author_year_extracts_year_and_normalises_authors() -> None:
    smith_year = 2023
    aydin_year = 2024
    demir_year = 2022

    assert parse_author_year("[Smith 2023]") == {
        "authors": ["Smith"],
        "year": smith_year,
    }
    parsed = parse_author_year("[Aydın et al. 2024]")
    assert parsed["year"] == aydin_year
    assert parsed["authors"] == ["Aydın"]

    parsed_pair = parse_author_year("[Smith and Jones 2023]")
    assert parsed_pair["year"] == smith_year
    assert parsed_pair["authors"] == ["Smith", "Jones"]

    parsed_paren = parse_author_year("(Demir 2022)")
    assert parsed_paren["year"] == demir_year
    assert parsed_paren["authors"] == ["Demir"]


def test_numbered_marker_yields_no_author_or_year() -> None:
    parsed = parse_author_year("[12]")
    assert parsed == {"authors": [], "year": None}


def test_extracted_citation_carries_parsed_year() -> None:
    citations = extract_citations("Background [Smith 2023].")
    assert len(citations) == 1
    expected_year = 2023
    citation = citations[0]
    assert citation.year == expected_year
    assert citation.authors == ["Smith"]
    assert citation.verified is False
    assert citation.doi is None
