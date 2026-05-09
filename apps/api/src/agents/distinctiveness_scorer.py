"""Distinctiveness Scorer agent — wraps the embedding-based scorer in the
BaseAgent interface so the orchestrator can run it like any other agent.

Per docs/06-agent-architecture.md §4.7. No LLM calls beyond embeddings, so
estimated_duration_seconds is ~5 (one embed + one indexed query).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.compliance.distinctiveness import (
    DistinctivenessScore,
    DistinctivenessScorer,
    ProposalNotFoundError,
    ProposalNotReadyError,
)

if TYPE_CHECKING:
    import asyncpg


class DistinctivenessScorerAgent(BaseAgent):
    agent_id = "distinctiveness_scorer"
    name = "Distinctiveness Scorer"
    description = (
        "Compares the proposal's Excellence section against recently-funded "
        "CORDIS projects to detect generic AI-generated patterns."
    )
    version = "v1"
    requires_rag = False
    estimated_duration_seconds = 5

    def __init__(
        self,
        conn: asyncpg.Connection,
        scorer: DistinctivenessScorer | None = None,
    ) -> None:
        self._conn = conn
        self._scorer = scorer or DistinctivenessScorer()

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        started = time.perf_counter()
        try:
            score = await self._scorer.score(agent_input.proposal_id, self._conn)
            status = "completed"
        except ProposalNotFoundError as exc:
            return self._failure_output(started, "not_found", str(exc))
        except ProposalNotReadyError as exc:
            return self._failure_output(started, "skipped", str(exc))

        duration_ms = int((time.perf_counter() - started) * 1000)
        return AgentOutput(
            agent_id=self.agent_id,
            status=status,
            output=score.model_dump(mode="json"),
            duration_ms=duration_ms,
            cost_usd=_estimate_embedding_cost_usd(agent_input),
            tokens_used={},
        )

    async def stream(self, agent_input: AgentInput) -> AsyncIterator[str]:
        # Embedding-based scorer has no incremental output. Run once, yield the
        # final result as a single JSON event so the SSE consumer's contract is
        # consistent with the other agents.
        result = await self.run(agent_input)
        yield json.dumps(result.model_dump(mode="json"))

    def _failure_output(
        self, started: float, status: str, message: str
    ) -> AgentOutput:
        duration_ms = int((time.perf_counter() - started) * 1000)
        unknown = DistinctivenessScore(
            score=None, level="unknown", message=message, similar_projects=[]
        )
        return AgentOutput(
            agent_id=self.agent_id,
            status=status,
            output=unknown.model_dump(mode="json"),
            duration_ms=duration_ms,
            metadata={"reason": message},
        )


def _estimate_embedding_cost_usd(_: AgentInput) -> float:
    """Rough placeholder — exact cost is computed in the embeddings module's
    usage logger when the full LLM router lands. text-embedding-3-large is
    $0.13/1M input tokens; an Excellence section is ~4K tokens → ~$0.0005.
    """
    return 0.0005
