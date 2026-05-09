"""Unit tests for the threshold/level mapping in DistinctivenessScorer.

The scorer's data path (proposal lookup, embedding, pgvector query) is exercised
by the integration test in test_distinctiveness_integration.py. These tests
target the pure logic so they run without a database.
"""

from __future__ import annotations

import pytest
from src.compliance.distinctiveness import DistinctivenessScore, DistinctivenessScorer


def _row(cordis_id: str, acronym: str, similarity: float) -> dict[str, object]:
    return {
        "cordis_id": cordis_id,
        "acronym": acronym,
        "title": f"{acronym} title",
        "similarity": similarity,
    }


def test_distinctive_level_for_low_similarity() -> None:
    scorer = DistinctivenessScorer()
    rows = [_row("101", "GREENBOT", 0.70), _row("102", "AIMED", 0.55)]
    score = scorer._build_score(rows)
    assert isinstance(score, DistinctivenessScore)
    assert score.level == "distinctive"
    assert score.score == pytest.approx(0.70)
    assert "distinctive" in score.message.lower()
    assert len(score.similar_projects) == 2
    assert score.similar_projects[0].cordis_id == "101"
    assert score.similar_projects[0].cordis_url == "https://cordis.europa.eu/project/id/101"


def test_warning_level_between_high_and_critical() -> None:
    scorer = DistinctivenessScorer()
    rows = [_row("201", "ECOFLOW", 0.88), _row("202", "X", 0.84)]
    score = scorer._build_score(rows)
    assert score.level == "warning"
    assert score.score == pytest.approx(0.88)
    assert "ECOFLOW" in score.message
    assert "differentiating" in score.message.lower() or "consider" in score.message.lower()


def test_critical_level_for_very_similar() -> None:
    scorer = DistinctivenessScorer()
    rows = [_row("301", "GENERIC", 0.95)]
    score = scorer._build_score(rows)
    assert score.level == "critical"
    assert score.score == pytest.approx(0.95)
    assert "GENERIC" in score.message
    assert "rewrite" in score.message.lower()


def test_unknown_when_no_comparable_projects() -> None:
    scorer = DistinctivenessScorer()
    score = scorer._build_score([])
    assert score.level == "unknown"
    assert score.score is None
    assert score.similar_projects == []
    assert "no comparable" in score.message.lower()


def test_top_5_similar_projects_returned() -> None:
    scorer = DistinctivenessScorer()
    rows = [_row(f"id{i}", f"ACR{i}", 0.9 - (i * 0.01)) for i in range(10)]
    score = scorer._build_score(rows)
    assert len(score.similar_projects) == 5
    assert [p.cordis_id for p in score.similar_projects] == ["id0", "id1", "id2", "id3", "id4"]


def test_acronym_falls_back_to_cordis_id_when_missing() -> None:
    scorer = DistinctivenessScorer()
    rows = [{"cordis_id": "999", "acronym": None, "title": "T", "similarity": 0.93}]
    score = scorer._build_score(rows)
    assert "999" in score.message  # cordis_id used in place of acronym
    assert score.level == "critical"


def test_custom_thresholds() -> None:
    scorer = DistinctivenessScorer(threshold_high=0.5, threshold_critical=0.7)
    assert scorer._build_score([_row("a", "A", 0.45)]).level == "distinctive"
    assert scorer._build_score([_row("a", "A", 0.6)]).level == "warning"
    assert scorer._build_score([_row("a", "A", 0.8)]).level == "critical"
