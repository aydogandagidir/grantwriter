"""In-memory unit tests for :mod:`src.scrapers.dedup`.

The SQL paths (``find_cross_source_duplicates``, ``tag_duplicates``)
need a live DB and live ``pg_trgm`` extension; they live in the
integration suite (Faz 1, when the runner exists). Here we just cover
the pure-function pieces: title normalization and Python trigram
similarity used as the pre-filter.
"""

from __future__ import annotations

import pytest
from src.scrapers.dedup import normalize_title, trigram_similarity

# ── normalize_title ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("NGI0 Core – Open Call", "ngi0 core open call"),
        ("NGI0 Core - Open call", "ngi0 core open call"),
        ("Sanayi Ar-Ge 2026/1", "sanayi ar ge 2026 1"),
        ("İstanbul Akıllı Şehir Çağrısı", "istanbul akilli sehir cagrisi"),
        ("  multi   space  ", "multi space"),
    ],
)
def test_normalize_title_strips_punctuation_and_accents(
    raw: str, expected: str
) -> None:
    assert normalize_title(raw) == expected


def test_normalize_title_empty() -> None:
    assert normalize_title("") == ""


# ── trigram_similarity ───────────────────────────────────────────────────


def test_trigram_similarity_identical_is_one() -> None:
    assert trigram_similarity("hello world", "hello world") == 1.0


def test_trigram_similarity_close_titles_score_high() -> None:
    # Same call announced on two sources with minor punctuation changes.
    sim = trigram_similarity(
        "NGI0 Core – Open Call 13",
        "NGI0 Core - Open call 13",
    )
    assert sim >= 0.85


def test_trigram_similarity_different_titles_score_low() -> None:
    sim = trigram_similarity(
        "TÜBİTAK 1501 Sanayi Ar-Ge",
        "Horizon Europe Cluster 4 Digital",
    )
    assert sim < 0.20


def test_trigram_similarity_empty_inputs_return_zero() -> None:
    assert trigram_similarity("", "") == 0.0
    assert trigram_similarity("x", "") == 0.0
    assert trigram_similarity("", "y") == 0.0


def test_trigram_similarity_accent_insensitive() -> None:
    """``İstanbul`` and ``Istanbul`` should compare as the same word."""

    sim = trigram_similarity(
        "İstanbul Akıllı Şehir Çağrısı 2026",
        "Istanbul Akilli Sehir Cagrisi 2026",
    )
    # Accent-stripping in normalize_title makes these byte-identical
    # after normalisation; the trigram set is therefore identical.
    assert sim == 1.0
