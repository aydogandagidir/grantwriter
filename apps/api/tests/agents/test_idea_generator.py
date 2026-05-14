"""Unit tests for :mod:`src.agents.idea_generator`.

Covers the pure pieces: the tolerant JSON response parser and the
per-idea coercion/validation. The full pipeline (cache read/write +
call/org loads + LLM round-trip) is integration-tested against a live
DB with a mock router.
"""

from __future__ import annotations

from src.agents.idea_generator import (
    GeneratedIdea,
    _coerce_idea,
    _parse_response,
)

# ── _parse_response ──────────────────────────────────────────────────────


def test_parse_response_clean_json() -> None:
    raw = '{"ideas": [{"title": "X", "abstract": "Y"}]}'
    payload = _parse_response(raw)
    assert payload["ideas"][0]["title"] == "X"


def test_parse_response_strips_markdown_fences() -> None:
    raw = '```json\n{"ideas": []}\n```'
    assert _parse_response(raw) == {"ideas": []}


def test_parse_response_invalid_json_returns_empty() -> None:
    assert _parse_response("the model rambled instead of returning JSON") == {}


def test_parse_response_non_dict_returns_empty() -> None:
    assert _parse_response("[1, 2, 3]") == {}


# ── _coerce_idea ─────────────────────────────────────────────────────────


def test_coerce_idea_full_payload() -> None:
    raw = {
        "title": "Edge-deployed anomaly detection for factory PLCs",
        "abstract": "A" * 300,
        "technology_angle": "Quantised transformer on ARM Cortex-M",
        "impact_thesis": "Cuts unplanned downtime for mid-size manufacturers",
        "est_budget_eur_min": 800_000,
        "est_budget_eur_max": 1_500_000,
        "est_trl": 5,
        "suggested_consortium_type": "SME lead + 1 RTO + 1 factory end-user",
        "alignment_score": 0.88,
    }
    idea = _coerce_idea(raw)
    assert idea is not None
    assert isinstance(idea, GeneratedIdea)
    assert idea.title.startswith("Edge-deployed")
    assert idea.est_trl == 5
    assert idea.alignment_score == 0.88
    # V1 never sets distinctiveness — that's the V2 scorer pass.
    assert idea.distinctiveness_score is None


def test_coerce_idea_drops_entry_without_title() -> None:
    assert _coerce_idea({"abstract": "has body but no title"}) is None


def test_coerce_idea_drops_entry_without_abstract() -> None:
    assert _coerce_idea({"title": "has title but no body"}) is None


def test_coerce_idea_drops_non_dict() -> None:
    assert _coerce_idea("not a dict") is None
    assert _coerce_idea(None) is None
    assert _coerce_idea([1, 2]) is None


def test_coerce_idea_clamps_alignment_score() -> None:
    high = _coerce_idea(
        {"title": "T", "abstract": "x" * 50, "alignment_score": 1.7}
    )
    low = _coerce_idea(
        {"title": "T", "abstract": "x" * 50, "alignment_score": -0.5}
    )
    assert high is not None and high.alignment_score == 1.0
    assert low is not None and low.alignment_score == 0.0


def test_coerce_idea_defaults_alignment_when_missing_or_bad() -> None:
    missing = _coerce_idea({"title": "T", "abstract": "x" * 50})
    bad = _coerce_idea(
        {"title": "T", "abstract": "x" * 50, "alignment_score": "very high"}
    )
    assert missing is not None and missing.alignment_score == 0.5
    assert bad is not None and bad.alignment_score == 0.5


def test_coerce_idea_rejects_out_of_range_trl() -> None:
    # TRL 12 isn't a thing — coerce to None rather than persist garbage.
    idea = _coerce_idea(
        {"title": "T", "abstract": "x" * 50, "est_trl": 12}
    )
    assert idea is not None
    assert idea.est_trl is None


def test_coerce_idea_handles_null_budget() -> None:
    idea = _coerce_idea(
        {
            "title": "T",
            "abstract": "x" * 50,
            "est_budget_eur_min": None,
            "est_budget_eur_max": None,
        }
    )
    assert idea is not None
    assert idea.est_budget_eur_min is None
    assert idea.est_budget_eur_max is None


def test_coerce_idea_truncates_overlong_fields() -> None:
    idea = _coerce_idea(
        {"title": "T" * 500, "abstract": "A" * 10_000}
    )
    assert idea is not None
    assert len(idea.title) <= 300
    assert len(idea.abstract) <= 8000
