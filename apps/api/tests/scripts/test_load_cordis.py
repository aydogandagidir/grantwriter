"""Pure-function tests for the CORDIS loader script.

The end-to-end download → embed → insert path is covered by manual verification
(documented in the implementation plan); these tests pin down the parsing,
filtering, and topic-splitting logic so future CORDIS schema drift fails loud.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

# Make the script importable as a module.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import load_cordis  # noqa: E402


def _sample_df(rows: list[dict[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_filter_recent_drops_old_projects() -> None:
    df = _sample_df(
        [
            {"id": "1", "title": "T1", "objective": "x" * 200, "topics": "A", "startDate": "2024-01-15"},
            {"id": "2", "title": "T2", "objective": "x" * 200, "topics": "A", "startDate": "2018-01-01"},
            {"id": "3", "title": "T3", "objective": "x" * 200, "topics": "A", "startDate": "2025-06-30"},
        ]
    )
    out = load_cordis.filter_recent(df, years=3, today=date(2026, 5, 9))
    assert sorted(out["id"].tolist()) == ["1", "3"]


def test_filter_recent_drops_short_or_missing_abstracts() -> None:
    df = _sample_df(
        [
            {"id": "1", "title": "T1", "objective": "too short", "topics": "A", "startDate": "2025-01-01"},
            {"id": "2", "title": "T2", "objective": None, "topics": "A", "startDate": "2025-01-01"},
            {"id": "3", "title": "T3", "objective": "x" * 200, "topics": "A", "startDate": "2025-01-01"},
        ]
    )
    out = load_cordis.filter_recent(df, years=3, today=date(2026, 5, 9))
    assert out["id"].tolist() == ["3"]


def test_filter_recent_dedupes_on_id() -> None:
    df = _sample_df(
        [
            {"id": "1", "title": "A", "objective": "x" * 200, "topics": "A", "startDate": "2025-01-01"},
            {"id": "1", "title": "A-dup", "objective": "x" * 200, "topics": "B", "startDate": "2025-01-01"},
        ]
    )
    out = load_cordis.filter_recent(df, years=3, today=date(2026, 5, 9))
    assert len(out) == 1


def test_split_topics_handles_semicolon_separator() -> None:
    assert load_cordis.split_topics("HORIZON-CL4-A;HORIZON-CL4-B") == [
        "HORIZON-CL4-A",
        "HORIZON-CL4-B",
    ]


def test_split_topics_handles_single_topic() -> None:
    assert load_cordis.split_topics("HORIZON-CL4-A") == ["HORIZON-CL4-A"]


def test_split_topics_returns_empty_for_blank_or_none() -> None:
    assert load_cordis.split_topics("") == []
    assert load_cordis.split_topics(None) == []
    assert load_cordis.split_topics(float("nan")) == []


def test_split_topics_strips_whitespace_and_drops_empty() -> None:
    assert load_cordis.split_topics(" A ;  ; B ; ") == ["A", "B"]


def test_normalize_rows_shape() -> None:
    df = _sample_df(
        [
            {
                "id": "100",
                "title": "T",
                "objective": "x" * 200,
                "topics": "T1;T2",
                "startDate": "2025-06-01",
                "endDate": "2027-06-01",
                "acronym": "ACR",
                "frameworkProgramme": "HORIZON",
                "totalCost": "1234567.89",
            }
        ]
    )
    df = load_cordis.filter_recent(df, years=3, today=date(2026, 5, 9))
    rows = load_cordis.normalize_rows(df)
    assert len(rows) == 1
    row = rows[0]
    assert row["cordis_id"] == "100"
    assert row["acronym"] == "ACR"
    assert row["topic_ids"] == ["T1", "T2"]
    assert row["programme"] == "HORIZON"
    assert row["budget_eur"] == pytest.approx(1234567.89)
    assert row["start_date"] == date(2025, 6, 1)


def test_vector_literal_roundtrip() -> None:
    formatted = load_cordis.vector_literal([0.1, -0.5, 0.0])
    assert formatted == "[0.1000000,-0.5000000,0.0000000]"
