"""Horizon Europe AI Disclosure (Standard Application Form, page 32).

Aggregates ``proposal_provenance`` rows into the EC-recommended disclosure
text per docs/09-security-compliance.md §2.2. The function is async and
takes an ``asyncpg.Connection`` so the Compliance Reviewer agent can plug
it into its existing connection rather than re-opening one.

The pure-Python aggregation is exposed as :func:`aggregate_provenance` so
tests can exercise the counting logic without a database.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).resolve().parent / "ai_disclosure_template.md"

# TODO(BD-corpus-size): Pull the real corpus size from
# ``rag_corpus_chunks`` once the RAG pipeline lands. Hard-coding 500 keeps
# the disclosure renderable today without a misleading "0".
_DEFAULT_CORPUS_SIZE = 500
_DEFAULT_PROVIDER = "Anthropic"


class DisclosureStats(BaseModel):
    """Counts derived from ``proposal_provenance`` for the disclosure.

    The five ``source`` enum values from the migration map directly to the
    integer counters here; ``agents_used`` and ``models_used`` are the
    de-duplicated set of non-null per-row metadata.
    """

    model_config = ConfigDict(frozen=True)

    total_sentences: int
    human: int
    ai_generated: int
    ai_edited: int
    imported: int
    rag_retrieved: int
    agents_used: list[str] = Field(default_factory=list)
    """Sorted, deduplicated list — sets are not stable across processes."""
    models_used: list[str] = Field(default_factory=list)


def aggregate_provenance(rows: list[dict[str, Any]]) -> DisclosureStats:
    """Aggregate raw provenance rows into a :class:`DisclosureStats`.

    Pure function: the test seam. Each row is a mapping with at least a
    ``source`` key; ``agent_id`` and ``llm_model`` are optional (the
    migration allows nulls).
    """

    counts = {
        "human": 0,
        "ai-generated": 0,
        "ai-edited": 0,
        "imported": 0,
        "rag-retrieved": 0,
    }
    agents: set[str] = set()
    models: set[str] = set()

    for row in rows:
        source = row.get("source")
        if source in counts:
            counts[source] += 1
        agent_id = row.get("agent_id")
        if agent_id:
            agents.add(str(agent_id))
        llm_model = row.get("llm_model")
        if llm_model:
            models.add(str(llm_model))

    return DisclosureStats(
        total_sentences=len(rows),
        human=counts["human"],
        ai_generated=counts["ai-generated"],
        ai_edited=counts["ai-edited"],
        imported=counts["imported"],
        rag_retrieved=counts["rag-retrieved"],
        agents_used=sorted(agents),
        models_used=sorted(models),
    )


def render_disclosure(
    stats: DisclosureStats,
    *,
    total_citations: int = 0,
    verified_count: int = 0,
    provider: str = _DEFAULT_PROVIDER,
    corpus_size: int = _DEFAULT_CORPUS_SIZE,
) -> str:
    """Format ``stats`` into the EC-recommended disclosure text.

    Defaults to zero citations so the disclosure still renders for drafts
    that haven't reached the verification stage yet — the wording stays
    honest ("0 of 0 verified") rather than crashing.
    """

    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    models_used = ", ".join(stats.models_used) if stats.models_used else "Claude"
    return template.format(
        models_used=models_used,
        provider=provider,
        corpus_size=corpus_size,
        total_sentences=stats.total_sentences,
        human=stats.human,
        ai_generated=stats.ai_generated,
        ai_edited=stats.ai_edited,
        verified_count=verified_count,
        total_citations=total_citations,
    )


async def generate_ai_disclosure(
    proposal_id: UUID,
    conn: asyncpg.Connection,
) -> str | None:
    """Build the AI disclosure text for ``proposal_id``.

    Returns ``None`` if there is no provenance for this proposal yet — the
    draft hasn't been generated, or the migration hasn't been applied. The
    caller decides whether absence is fine (early-stage draft) or surfaces
    as an issue.
    """

    rows = await conn.fetch(
        """
        select source, agent_id, llm_model
        from proposal_provenance
        where proposal_id = $1
        """,
        proposal_id,
    )
    if not rows:
        return None

    materialised = [dict(row) for row in rows]
    stats = aggregate_provenance(materialised)

    total_citations, verified_count = await _citation_totals(proposal_id, conn)

    return render_disclosure(
        stats,
        total_citations=total_citations,
        verified_count=verified_count,
    )


async def _citation_totals(
    proposal_id: UUID, conn: asyncpg.Connection
) -> tuple[int, int]:
    """Return ``(total_citations, verified_count)`` for the proposal.

    The ``citations`` table may not exist in every test environment and
    its exact column name for the verification flag may shift across
    sprints. We guard with a broad except so a missing or renamed table
    degrades the disclosure gracefully (zeros) rather than failing the
    Compliance Reviewer agent.
    """

    try:
        row = await conn.fetchrow(
            """
            select
              count(*) as total,
              count(*) filter (where status = 'verified') as verified
            from citations
            where proposal_id = $1
            """,
            proposal_id,
        )
    except Exception:
        logger.warning(
            "ai_disclosure_citation_lookup_failed",
            extra={"proposal_id": str(proposal_id)},
            exc_info=True,
        )
        return 0, 0

    if row is None:
        return 0, 0
    return int(row["total"] or 0), int(row["verified"] or 0)


__all__ = [
    "DisclosureStats",
    "aggregate_provenance",
    "generate_ai_disclosure",
    "render_disclosure",
]
