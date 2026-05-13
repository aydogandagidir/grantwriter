"""Unit tests for :mod:`src.scrapers.normalization`.

These are pure-function tests — no IO, no fixtures. Every parser is
expected to handle realistic upstream variants (EN + TR, weird spacing,
mixed decimal marks) without falling over. Edge cases we deliberately
cover: empty strings, malformed dates, dimensionally implausible TRL,
budget ranges with currency symbol *after* the number, URLs with
tracking junk.
"""

from __future__ import annotations

from datetime import date

import pytest
from src.scrapers.normalization import (
    canonicalize_url,
    compute_lifecycle_status,
    extract_eligibility_tags,
    extract_trl_range,
    map_to_nace,
    parse_budget_range,
    parse_deadline,
    set_fx_rates,
    to_eur,
)


# ── parse_budget_range ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected_low, expected_high, expected_currency",
    [
        ("€2-10M", 2_000_000.0, 10_000_000.0, "EUR"),
        ("€2-10 million", 2_000_000.0, 10_000_000.0, "EUR"),
        ("2.000.000 - 10.000.000 EUR", 2_000_000.0, 10_000_000.0, "EUR"),
        ("500K – 1.5M TL", 500_000.0, 1_500_000.0, "TRY"),
        ("3.500.000 TL", 3_500_000.0, 3_500_000.0, "TRY"),
        ("€100k", 100_000.0, 100_000.0, "EUR"),
        ("5 milyon TL", 5_000_000.0, 5_000_000.0, "TRY"),
        ("$2.5M", 2_500_000.0, 2_500_000.0, "USD"),
    ],
)
def test_parse_budget_range_common_forms(
    text: str,
    expected_low: float | None,
    expected_high: float | None,
    expected_currency: str,
) -> None:
    low, high, currency = parse_budget_range(text)
    assert low == pytest.approx(expected_low) if expected_low else low is None
    assert high == pytest.approx(expected_high) if expected_high else high is None
    assert currency == expected_currency


def test_parse_budget_range_returns_default_when_no_number() -> None:
    low, high, currency = parse_budget_range("budget TBD")
    assert low is None and high is None
    assert currency == "EUR"  # default fallback


def test_parse_budget_range_empty_string() -> None:
    low, high, currency = parse_budget_range("")
    assert low is None and high is None and currency == "EUR"


# ── extract_trl_range ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Project should target TRL 4-7.", (4, 7)),
        ("Technology Readiness Level 5 to 9", (5, 9)),
        ("Teknoloji Hazırlık Seviyesi 3-6", (3, 6)),
        ("THS 7", (7, 7)),
        ("Tek TRL: TRL 5", (5, 5)),
        ("TRL 9–5", (5, 9)),  # min > max swapped
    ],
)
def test_extract_trl_range_common_forms(text: str, expected: tuple[int, int]) -> None:
    assert extract_trl_range(text) == expected


@pytest.mark.parametrize("text", ["no mention", "", "TRL 10 (out of range)"])
def test_extract_trl_range_rejects_implausible(text: str) -> None:
    assert extract_trl_range(text) == (None, None)


# ── parse_deadline ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Deadline: 2026-09-15", date(2026, 9, 15)),
        ("Son başvuru 15/09/2026", date(2026, 9, 15)),
        ("15.09.2026", date(2026, 9, 15)),
        ("September 15, 2026", date(2026, 9, 15)),
        ("Sep 15 2026", date(2026, 9, 15)),
        ("15 Eylül 2026", date(2026, 9, 15)),
        ("15 Şubat 2026", date(2026, 2, 15)),
        ("15 Subat 2026", date(2026, 2, 15)),  # accent-less Turkish
    ],
)
def test_parse_deadline_common_forms(text: str, expected: date) -> None:
    assert parse_deadline(text) == expected


def test_parse_deadline_rejects_year_out_of_plausible_range() -> None:
    # We won't accept obvious typos like 2001 or 2099.
    assert parse_deadline("Deadline: 2001-09-15") is None
    assert parse_deadline("Deadline: 2099-01-01") is None


def test_parse_deadline_picks_first_match() -> None:
    """When multiple dates appear (deadline + opening), the first one
    wins — scrapers should call this on a pre-narrowed snippet."""

    text = "Opens 2026-01-01, deadline 2026-09-15"
    assert parse_deadline(text) == date(2026, 1, 1)


def test_parse_deadline_returns_none_on_garbage() -> None:
    assert parse_deadline("") is None
    assert parse_deadline("no date here") is None


# ── compute_lifecycle_status ─────────────────────────────────────────────


def test_lifecycle_status_no_deadline_is_open() -> None:
    # No deadline → safer to leave as 'open' than 'draft'.
    assert compute_lifecycle_status(None) == "open"


def test_lifecycle_status_past_deadline_is_closed() -> None:
    today = date(2026, 5, 13)
    assert compute_lifecycle_status(date(2026, 4, 1), today=today) == "closed"


def test_lifecycle_status_within_two_weeks_is_closing_soon() -> None:
    today = date(2026, 5, 13)
    assert compute_lifecycle_status(date(2026, 5, 20), today=today) == "closing_soon"


def test_lifecycle_status_far_deadline_is_open() -> None:
    today = date(2026, 5, 13)
    assert compute_lifecycle_status(date(2026, 9, 15), today=today) == "open"


# ── canonicalize_url ─────────────────────────────────────────────────────


def test_canonicalize_url_strips_utm() -> None:
    raw = "https://example.com/calls/xyz?utm_source=nl&utm_campaign=spring"
    assert canonicalize_url(raw) == "https://example.com/calls/xyz"


def test_canonicalize_url_lowercases_host_and_scheme() -> None:
    raw = "HTTPS://Example.COM/Path"
    assert canonicalize_url(raw) == "https://example.com/Path"


def test_canonicalize_url_sorts_query() -> None:
    raw = "https://example.com/x?z=1&a=2&m=3"
    # Sorted alphabetically by key.
    assert canonicalize_url(raw) == "https://example.com/x?a=2&m=3&z=1"


def test_canonicalize_url_drops_fragment() -> None:
    raw = "https://example.com/page#section"
    assert canonicalize_url(raw) == "https://example.com/page"


def test_canonicalize_url_preserves_real_query() -> None:
    raw = "https://example.com/calls?id=42&utm_source=x"
    assert canonicalize_url(raw) == "https://example.com/calls?id=42"


def test_canonicalize_url_empty_returns_empty() -> None:
    assert canonicalize_url("") == ""


# ── map_to_nace ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sector, expected",
    [
        ("software development", "J62"),
        ("Open-Source Software", "J62"),
        ("renewable energy", "D35"),
        ("automotive industry", "C29"),
        ("AI / machine learning", "J62"),  # longest-match wins
        ("medical devices", "C32"),
        ("smart city", "F42"),
    ],
)
def test_map_to_nace_known_phrases(sector: str, expected: str) -> None:
    assert map_to_nace(sector) == expected


def test_map_to_nace_unknown_returns_none() -> None:
    assert map_to_nace("interpretive dance") is None


def test_map_to_nace_empty_returns_none() -> None:
    assert map_to_nace("") is None


# ── extract_eligibility_tags ─────────────────────────────────────────────


def test_extract_eligibility_tags_picks_up_sme_signal() -> None:
    text = "Bu çağrı sadece KOBİ ölçeğindeki şirketler için açıktır."
    assert "sme" in extract_eligibility_tags(text)


def test_extract_eligibility_tags_consortium_required() -> None:
    text = "A consortium of at least 3 partners from different countries"
    tags = extract_eligibility_tags(text)
    assert "consortium_required" in tags


def test_extract_eligibility_tags_dedupes() -> None:
    # SME mentioned in both EN and TR — only one 'sme' tag in output.
    text = "Small and medium enterprises (KOBİ) eligible"
    tags = extract_eligibility_tags(text)
    assert tags.count("sme") == 1


def test_extract_eligibility_tags_empty_text() -> None:
    assert extract_eligibility_tags("") == []


# ── FX / to_eur ──────────────────────────────────────────────────────────


def test_to_eur_known_currency() -> None:
    # 1,000,000 TL ≈ €28,000 at the table snapshot rate of 0.028.
    assert to_eur(1_000_000, "TRY") == pytest.approx(28_000.0)


def test_to_eur_eur_passthrough() -> None:
    assert to_eur(42.0, "EUR") == 42.0


def test_to_eur_unknown_currency_returns_none() -> None:
    assert to_eur(1_000, "XYZ") is None


def test_to_eur_none_amount() -> None:
    assert to_eur(None, "EUR") is None


def test_set_fx_rates_replaces_table() -> None:
    original = {"EUR": 1.0, "TRY": 0.028}
    try:
        set_fx_rates({"EUR": 1.0, "TRY": 0.030, "USD": 1.0})
        assert to_eur(1_000_000, "TRY") == pytest.approx(30_000.0)
        assert to_eur(100, "USD") == pytest.approx(100.0)
    finally:
        set_fx_rates(original)


def test_set_fx_rates_rejects_missing_eur_anchor() -> None:
    with pytest.raises(ValueError, match="must anchor EUR"):
        set_fx_rates({"TRY": 0.028, "USD": 0.92})
