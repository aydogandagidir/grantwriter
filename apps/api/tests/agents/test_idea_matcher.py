"""Unit tests for :mod:`src.agents.idea_matcher`.

These tests cover the pure-function pieces of the matcher: scoring
helpers (jaccard, TRL fit, budget fit, cosine), the prompt-builder
shape, and the JSON-response parser. The full pipeline (hard filter +
SQL + LLM round-trip) gets its own integration suite gated on
``TEST_DATABASE_URL`` — see test_idea_matcher_integration.py.
"""

from __future__ import annotations

from src.agents.idea_matcher import (
    WEIGHT_BUDGET_FIT,
    WEIGHT_KEYWORD,
    WEIGHT_SECTOR,
    WEIGHT_SEMANTIC,
    WEIGHT_TRL_FIT,
    _budget_fit,
    _cosine,
    _jaccard,
    _norm_set,
    _parse_rerank_response,
    _soft_score,
    _trl_fit,
)

# ── _norm_set + _jaccard ─────────────────────────────────────────────────


def test_norm_set_lowercases_and_strips() -> None:
    assert _norm_set(["  AI ", "Machine Learning", " ai"]) == {
        "ai",
        "machine learning",
    }


def test_norm_set_drops_empties() -> None:
    assert _norm_set(["", "   ", "x"]) == {"x"}
    assert _norm_set([]) == set()


def test_jaccard_identical_sets() -> None:
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets() -> None:
    assert _jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_partial_overlap() -> None:
    # {a, b, c} ∩ {b, c, d} = {b, c} → 2/4 = 0.5
    assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == 0.5


def test_jaccard_empty_inputs() -> None:
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard({"a"}, set()) == 0.0
    assert _jaccard(set(), set()) == 0.0


# ── _trl_fit ─────────────────────────────────────────────────────────────


def test_trl_fit_inside_band_is_one() -> None:
    # Idea TRL 5 vs call TRL [4, 7] → fit 1.0
    assert _trl_fit(5, 4, 7) == 1.0
    assert _trl_fit(4, 4, 7) == 1.0
    assert _trl_fit(7, 4, 7) == 1.0


def test_trl_fit_one_step_outside_decays() -> None:
    # Idea TRL 3 vs call TRL [4, 7] → delta=1 → 1.0 - 0.25*1 = 0.75
    assert _trl_fit(3, 4, 7) == 0.75
    # Idea TRL 8 vs call TRL [4, 7] → delta=1 → 0.75
    assert _trl_fit(8, 4, 7) == 0.75


def test_trl_fit_far_outside_floors_at_zero() -> None:
    # Idea TRL 1 vs call TRL [7, 9] → delta=6 → 1 - 1.5 = -0.5 → clamped 0
    assert _trl_fit(1, 7, 9) == 0.0


def test_trl_fit_unknown_is_neutral() -> None:
    # Missing data → neutral 0.5 so the score isn't punished for unknowns.
    assert _trl_fit(None, 4, 7) == 0.5
    assert _trl_fit(5, None, 7) == 0.5
    assert _trl_fit(5, 4, None) == 0.5


# ── _budget_fit ──────────────────────────────────────────────────────────


def test_budget_fit_full_overlap_is_one() -> None:
    # Idea band 1M-3M lies entirely inside call band 0-5M → 1.0
    assert _budget_fit(1_000_000, 3_000_000, 0, 5_000_000) == 1.0


def test_budget_fit_disjoint_bands_are_zero() -> None:
    # Idea 5M-10M, call 0-1M → no overlap.
    assert _budget_fit(5_000_000, 10_000_000, 0, 1_000_000) == 0.0


def test_budget_fit_partial_overlap() -> None:
    # Idea 1M-5M, call 3M-10M → overlap = 3M-5M = 2M; idea span 4M → 0.5.
    assert _budget_fit(1_000_000, 5_000_000, 3_000_000, 10_000_000) == 0.5


def test_budget_fit_unknown_is_neutral() -> None:
    assert _budget_fit(None, 1_000_000, 0, 5_000_000) == 0.5
    assert _budget_fit(1_000_000, None, 0, 5_000_000) == 0.5
    assert _budget_fit(1_000_000, 5_000_000, None, 5_000_000) == 0.5


def test_budget_fit_handles_inverted_band() -> None:
    # If someone enters max < min, fall back to neutral instead of crashing.
    assert _budget_fit(5_000_000, 1_000_000, 0, 10_000_000) == 0.5


# ── _cosine ──────────────────────────────────────────────────────────────


def test_cosine_identical_vectors_is_one() -> None:
    v = [1.0, 2.0, 3.0]
    assert abs(_cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_vectors_is_zero() -> None:
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_opposite_vectors_is_minus_one() -> None:
    # Cosine of antiparallel vectors is -1; we don't clamp so the
    # caller can detect the case in tests / scoring.
    assert abs(_cosine([1.0, 0.0], [-1.0, 0.0]) - -1.0) < 1e-9


def test_cosine_zero_norm_returns_zero() -> None:
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_length_mismatch_returns_zero() -> None:
    # Mismatched lengths shouldn't crash — they should signal "no signal".
    assert _cosine([1.0, 0.0], [1.0]) == 0.0


def test_cosine_empty_input_returns_zero() -> None:
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([1.0], []) == 0.0


# ── _soft_score: combined formula ────────────────────────────────────────


def _candidate(
    *,
    call_id: str = "11111111-1111-1111-1111-111111111111",
    semantic: float = 0.8,
    keywords: list[str] | None = None,
    sectors: list[str] | None = None,
    trl_min: int | None = 4,
    trl_max: int | None = 7,
    budget_min: float | None = 1_000_000,
    budget_max: float | None = 5_000_000,
) -> object:
    """Minimal _CandidateCall stub via duck-typing — _soft_score reads
    only the attribute set we list here."""

    from uuid import UUID

    from src.agents.idea_matcher import _CandidateCall

    return _CandidateCall(
        call_id=UUID(call_id),
        programme_id="horizon_eu_ria",
        agency_id=None,
        title="t",
        deadline_iso="2026-09-15",
        topic_keywords=keywords or [],
        sectors=sectors or [],
        trl_min=trl_min,
        trl_max=trl_max,
        budget_min_eur=budget_min,
        budget_max_eur=budget_max,
        embedding=None,
        scope_summary=None,
        eligibility_tags=["sme"],
        semantic_score=semantic,
    )


def _ctx(*, keywords: list[str], sectors: list[str], trl: int | None, budget_lo: float | None, budget_hi: float | None) -> object:
    from uuid import uuid4

    from src.agents.idea_matcher import _IdeaContext

    return _IdeaContext(
        idea_id=uuid4(),
        tenant_id=uuid4(),
        title="t",
        abstract="a",
        embedding=None,
        sectors=sectors,
        keywords=keywords,
        trl_estimate=trl,
        budget_min_eur=budget_lo,
        budget_max_eur=budget_hi,
        org_country="tr",
        org_entity_type="sme",
        org_trl_current=4,
    )


def test_soft_score_combines_components_with_published_weights() -> None:
    """Sanity: the total equals the weighted sum of its parts."""

    ctx = _ctx(
        keywords=["ai", "ml"],
        sectors=["J62"],
        trl=5,
        budget_lo=2_000_000,
        budget_hi=4_000_000,
    )
    cand = _candidate(
        semantic=0.8,
        keywords=["ai"],
        sectors=["J62"],
        trl_min=4,
        trl_max=7,
        budget_min=1_000_000,
        budget_max=5_000_000,
    )
    [scored] = _soft_score(ctx, [cand])

    # Expected:
    #   semantic = 0.8
    #   keyword = jaccard({"ai","ml"}, {"ai"}) = 1/2 = 0.5
    #   sector = jaccard({"J62"}, {"J62"}) lower-cased = 1.0
    #   trl_fit = 1.0 (5 in [4,7])
    #   budget_fit = 1.0 (idea band fully inside call band)
    #   total = 0.5*0.8 + 0.2*0.5 + 0.15*1.0 + 0.10*1.0 + 0.05*1.0
    expected = (
        WEIGHT_SEMANTIC * 0.8
        + WEIGHT_KEYWORD * 0.5
        + WEIGHT_SECTOR * 1.0
        + WEIGHT_TRL_FIT * 1.0
        + WEIGHT_BUDGET_FIT * 1.0
    )
    assert scored["total_score"] == round(expected, 4)
    assert scored["keyword_overlap_score"] == 0.5
    assert scored["sector_score"] == 1.0
    assert scored["trl_fit_score"] == 1.0
    assert scored["budget_fit_score"] == 1.0


def test_soft_score_weights_sum_to_one() -> None:
    """If the weights don't sum to 1.0, breakdown bars in the UI lie."""

    total = (
        WEIGHT_SEMANTIC
        + WEIGHT_KEYWORD
        + WEIGHT_SECTOR
        + WEIGHT_TRL_FIT
        + WEIGHT_BUDGET_FIT
    )
    assert abs(total - 1.0) < 1e-9


# ── _parse_rerank_response: tolerant JSON ────────────────────────────────


def test_parse_rerank_response_clean_json() -> None:
    raw = '{"ranked": [{"call_id": "abc", "rationale_tr": "tr", "rationale_en": "en", "identified_gaps": []}]}'
    payload = _parse_rerank_response(raw)
    assert payload["ranked"][0]["call_id"] == "abc"


def test_parse_rerank_response_strips_markdown_fences() -> None:
    # LLMs sometimes wrap JSON in ```json fences despite instructions.
    raw = '```json\n{"ranked": []}\n```'
    payload = _parse_rerank_response(raw)
    assert payload == {"ranked": []}


def test_parse_rerank_response_invalid_json_returns_empty() -> None:
    assert _parse_rerank_response("not json at all") == {}


def test_parse_rerank_response_non_dict_top_level_returns_empty() -> None:
    # A bare JSON array doesn't have the {"ranked": ...} shape we expect.
    assert _parse_rerank_response("[]") == {}
