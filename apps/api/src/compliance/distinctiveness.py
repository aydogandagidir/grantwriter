"""Distinctiveness scoring per docs/04-rag-strategy.md §4.

Compares the user's Excellence section embedding to recently-funded CORDIS
projects in the same topic. Higher cosine similarity to the closest funded
project means lower semantic distinctiveness — and per Nature (Feb 2026) that
correlates with rejection by EU evaluators.

Deviations from the spec query in docs/04 §4.1:
  D1: cast `$1` to `halfvec(3072)` because the column is halfvec.
  D2: `WHERE $2 = ANY(topic_ids)` because CORDIS projects can have multiple
      topics; the column is text[] rather than text.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, Field

from src.llm.embeddings import embed

logger = logging.getLogger(__name__)


Level = Literal["distinctive", "warning", "critical", "unknown"]


class SimilarProject(BaseModel):
    cordis_id: str
    acronym: str | None
    title: str
    similarity: float
    cordis_url: str | None = None


class DistinctivenessScore(BaseModel):
    score: float | None = Field(
        default=None,
        description="Cosine similarity to the most similar funded project in the same topic. Higher = less distinctive. None if no comparable projects.",
    )
    level: Level
    message: str
    similar_projects: list[SimilarProject] = Field(default_factory=list)


class ProposalNotFoundError(LookupError):
    pass


class ProposalNotReadyError(ValueError):
    """Proposal exists but lacks the inputs needed for scoring."""


class DistinctivenessScorer:
    """Score a proposal's Excellence section against CORDIS funded projects.

    Thresholds chosen per docs/04-rag-strategy.md §4.1:
      <0.85 → distinctive (green)
      0.85-0.92 → warning (yellow)
      >0.92 → critical (red, recommend rewrite)
    """

    def __init__(
        self,
        threshold_high: float = 0.85,
        threshold_critical: float = 0.92,
        years_lookback: int = 3,
        top_k: int = 10,
    ) -> None:
        self.threshold_high = threshold_high
        self.threshold_critical = threshold_critical
        self.years_lookback = years_lookback
        self.top_k = top_k

    async def score(
        self, proposal_id: UUID, conn: asyncpg.Connection
    ) -> DistinctivenessScore:
        excellence_text, topic_id = await self._load_proposal_inputs(proposal_id, conn)
        user_embedding = await embed(excellence_text)
        embedding_literal = _vector_literal(user_embedding)

        rows = await conn.fetch(
            f"""
            select
              cordis_id,
              acronym,
              title,
              1 - (abstract_embedding <=> $1::halfvec(3072)) as similarity
            from cordis_funded_projects
            where $2 = any(topic_ids)
              and start_date >= now() - ($3 || ' years')::interval
              and abstract_embedding is not null
            order by abstract_embedding <=> $1::halfvec(3072)
            limit {int(self.top_k)}
            """,
            embedding_literal,
            topic_id,
            str(self.years_lookback),
        )

        score = self._build_score(rows)
        await self._persist(proposal_id, score, conn)
        return score

    async def _load_proposal_inputs(
        self, proposal_id: UUID, conn: asyncpg.Connection
    ) -> tuple[str, str]:
        row = await conn.fetchrow(
            """
            select
              p.draft -> 'excellence_md' as excellence_md,
              c.external_id as topic_id
            from proposals p
            left join calls c on c.id = p.call_id
            where p.id = $1
            """,
            proposal_id,
        )
        if row is None:
            raise ProposalNotFoundError(f"proposal {proposal_id} not found")
        excellence_md = row["excellence_md"]
        topic_id = row["topic_id"]
        # asyncpg returns jsonb scalars as str (or None). The wrapping `->`
        # operator preserves the JSON encoding, so a stored string comes back
        # quoted. We strip surrounding quotes if present.
        if isinstance(excellence_md, str):
            text = excellence_md.strip()
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
        elif excellence_md is None:
            text = ""
        else:
            text = str(excellence_md)
        if not text.strip():
            raise ProposalNotReadyError(
                "proposal has no draft.excellence_md to score"
            )
        if not topic_id:
            raise ProposalNotReadyError(
                "proposal has no linked call (need call.external_id as topic)"
            )
        return text, topic_id

    def _build_score(self, rows: list[asyncpg.Record]) -> DistinctivenessScore:
        if not rows:
            return DistinctivenessScore(
                score=None,
                level="unknown",
                message="No comparable funded projects found in CORDIS for this topic.",
                similar_projects=[],
            )
        top_similarity = float(rows[0]["similarity"])
        top_acronym = rows[0]["acronym"] or rows[0]["cordis_id"]

        if top_similarity < self.threshold_high:
            level: Level = "distinctive"
            message = (
                f"Your proposal is sufficiently distinctive "
                f"(closest match: {top_similarity:.2f})."
            )
        elif top_similarity < self.threshold_critical:
            level = "warning"
            message = (
                f"Your proposal is similar to '{top_acronym}' "
                f"({top_similarity:.0%}). Consider differentiating."
            )
        else:
            level = "critical"
            message = (
                f"Your proposal is very similar to '{top_acronym}' "
                f"({top_similarity:.0%}). Likely too generic — major rewrite recommended."
            )

        similar = [
            SimilarProject(
                cordis_id=r["cordis_id"],
                acronym=r["acronym"],
                title=r["title"],
                similarity=float(r["similarity"]),
                cordis_url=f"https://cordis.europa.eu/project/id/{r['cordis_id']}",
            )
            for r in rows[:5]
        ]
        return DistinctivenessScore(
            score=top_similarity, level=level, message=message, similar_projects=similar
        )

    async def _persist(
        self,
        proposal_id: UUID,
        score: DistinctivenessScore,
        conn: asyncpg.Connection,
    ) -> None:
        if score.score is None:
            return
        try:
            await conn.execute(
                "update proposals set distinctiveness_score = $1 where id = $2",
                score.score,
                proposal_id,
            )
        except asyncpg.PostgresError:
            # Persistence is best-effort — the score is the primary return value.
            logger.exception(
                "failed to persist distinctiveness_score for proposal %s", proposal_id
            )


def _vector_literal(values: list[float]) -> str:
    """Format a Python list as the pgvector text literal `[v1,v2,...]`.

    asyncpg with `register_vector` accepts numpy arrays / lists for vector
    columns, but we cast at the SQL boundary to halfvec, which the binary codec
    does not handle uniformly across pgvector versions. Sending a text literal
    sidesteps codec ambiguity entirely.
    """
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"
