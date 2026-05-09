"""AI Disclosure aggregator + template tests.

The async DB path is exercised against an ``AsyncMock`` for
``asyncpg.Connection`` — we don't depend on a live database for these
tests; the integration path (real Postgres + real provenance rows) is
covered separately under ``tests/programs/`` once the orchestrator
wires the agent into the saga.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from src.compliance.ai_disclosure import (
    DisclosureStats,
    aggregate_provenance,
    generate_ai_disclosure,
    render_disclosure,
)

# ── aggregate_provenance ───────────────────────────────────────────────


def test_aggregate_counts_by_source() -> None:
    rows = [
        {"source": "human", "agent_id": None, "llm_model": None},
        {"source": "human", "agent_id": None, "llm_model": None},
        {"source": "ai-generated", "agent_id": "excellence_writer", "llm_model": "claude-opus-4-7"},
        {"source": "ai-generated", "agent_id": "impact_writer", "llm_model": "claude-opus-4-7"},
        {"source": "ai-edited", "agent_id": "excellence_writer", "llm_model": "claude-opus-4-7"},
    ]
    stats = aggregate_provenance(rows)
    assert stats.total_sentences == 5
    assert stats.human == 2
    assert stats.ai_generated == 2
    assert stats.ai_edited == 1
    assert stats.imported == 0
    assert stats.rag_retrieved == 0


def test_aggregate_collects_distinct_agents_and_models() -> None:
    rows = [
        {
            "source": "ai-generated",
            "agent_id": "excellence_writer",
            "llm_model": "claude-opus-4-7",
        },
        {
            "source": "ai-generated",
            "agent_id": "excellence_writer",  # duplicate — should dedupe
            "llm_model": "claude-opus-4-7",  # duplicate
        },
        {
            "source": "ai-generated",
            "agent_id": "impact_writer",
            "llm_model": "claude-sonnet-4-6",
        },
    ]
    stats = aggregate_provenance(rows)
    assert stats.agents_used == ["excellence_writer", "impact_writer"]  # sorted, deduped
    assert stats.models_used == ["claude-opus-4-7", "claude-sonnet-4-6"]


def test_aggregate_handles_unknown_source_gracefully() -> None:
    """A row with a source value outside the migration's CHECK constraint
    shouldn't crash aggregation — just skip the row's source counter.

    Defensive against schema drift; the migration enforces the enum, but
    tests should not assume the DB is the only writer."""

    rows = [
        {"source": "human", "agent_id": None, "llm_model": None},
        {"source": "unknown-future-value", "agent_id": None, "llm_model": None},
    ]
    stats = aggregate_provenance(rows)
    assert stats.total_sentences == 2
    assert stats.human == 1


def test_aggregate_handles_empty_input() -> None:
    stats = aggregate_provenance([])
    assert stats.total_sentences == 0
    assert stats.human == 0
    assert stats.agents_used == []
    assert stats.models_used == []


# ── render_disclosure ──────────────────────────────────────────────────


def test_template_renders_with_sample_stats() -> None:
    stats = DisclosureStats(
        total_sentences=412,
        human=89,
        ai_generated=287,
        ai_edited=36,
        imported=0,
        rag_retrieved=0,
        agents_used=["excellence_writer", "impact_writer"],
        models_used=["claude-opus-4-7", "claude-sonnet-4-6"],
    )
    text = render_disclosure(stats, total_citations=47, verified_count=44)

    # Every placeholder substituted — no stray ``{`` left over from the
    # template (other than legitimate prose punctuation, which we'd never
    # write inside the template).
    assert "{" not in text
    assert "}" not in text

    # Spot-check the populated sentences.
    assert "Of 412 sentences in the proposal" in text
    assert "89 were authored directly" in text
    assert "287 were generated" in text
    assert "36 were AI-generated but subsequently edited" in text
    assert "44 of\n47 citations" in text or "44 of 47" in text
    assert "claude-opus-4-7" in text
    assert "Anthropic" in text  # default provider


def test_template_renders_with_zero_citations() -> None:
    """Disclosure should still render before citations are verified —
    the wording stays honest ("0 of 0 verified") rather than crashing."""

    stats = DisclosureStats(
        total_sentences=10,
        human=2,
        ai_generated=8,
        ai_edited=0,
        imported=0,
        rag_retrieved=0,
        agents_used=[],
        models_used=[],
    )
    text = render_disclosure(stats)
    assert "0 of\n0" in text or "0 of 0" in text
    # When models_used is empty, fall back to "Claude" rather than "" —
    # otherwise the sentence reads "uses  via Anthropic".
    assert "Claude" in text


# ── generate_ai_disclosure (DB-backed) ─────────────────────────────────


async def test_generate_returns_none_when_provenance_empty() -> None:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    result = await generate_ai_disclosure(uuid4(), conn)
    assert result is None
    conn.fetch.assert_awaited_once()


async def test_generate_full_path_renders_template() -> None:
    """Mock both the provenance fetch and the citations fetch; assert the
    template is rendered with the correct counts derived from the mock
    rows."""

    conn = AsyncMock()
    provenance_rows = [
        {"source": "human", "agent_id": None, "llm_model": None},
        {
            "source": "ai-generated",
            "agent_id": "excellence_writer",
            "llm_model": "claude-opus-4-7",
        },
        {
            "source": "ai-edited",
            "agent_id": "excellence_writer",
            "llm_model": "claude-opus-4-7",
        },
    ]
    conn.fetch = AsyncMock(return_value=provenance_rows)
    conn.fetchrow = AsyncMock(return_value={"total": 5, "verified": 4})

    text = await generate_ai_disclosure(uuid4(), conn)
    assert text is not None
    assert "Of 3 sentences" in text
    assert "1 were authored directly" in text
    assert "1 were generated" in text
    assert "1 were AI-generated but subsequently edited" in text
    assert "4 of\n5" in text or "4 of 5" in text
    assert "claude-opus-4-7" in text


async def test_generate_swallows_citation_lookup_errors() -> None:
    """If the citations table is missing or its schema has drifted, the
    disclosure should still render with zero citation counts rather than
    failing the whole agent."""

    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"source": "human", "agent_id": None, "llm_model": None},
        ]
    )
    conn.fetchrow = AsyncMock(side_effect=RuntimeError("citations table missing"))

    text = await generate_ai_disclosure(uuid4(), conn)
    assert text is not None
    assert "Of 1 sentences" in text
    assert "0 of\n0" in text or "0 of 0" in text


# ── helpers ────────────────────────────────────────────────────────────


def _row(**overrides: Any) -> dict[str, Any]:
    """Build a provenance row with sane defaults for ad-hoc tests."""

    base: dict[str, Any] = {
        "source": "ai-generated",
        "agent_id": "excellence_writer",
        "llm_model": "claude-opus-4-7",
    }
    base.update(overrides)
    return base
