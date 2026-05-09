"""Unit tests for the BaseAgent wrapper around DistinctivenessScorer."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.agents.base import AgentInput
from src.agents.distinctiveness_scorer import DistinctivenessScorerAgent
from src.compliance.distinctiveness import (
    DistinctivenessScore,
    ProposalNotFoundError,
    ProposalNotReadyError,
    SimilarProject,
)


def _agent_input() -> AgentInput:
    return AgentInput(
        proposal_id=uuid4(),
        tenant_id=uuid4(),
        programme_id="horizon_eu_ria",
        language="en",
    )


def _ok_score() -> DistinctivenessScore:
    return DistinctivenessScore(
        score=0.72,
        level="distinctive",
        message="Your proposal is sufficiently distinctive (closest match: 0.72).",
        similar_projects=[
            SimilarProject(
                cordis_id="cordis_X",
                acronym="X",
                title="X title",
                similarity=0.72,
                cordis_url="https://cordis.europa.eu/project/id/cordis_X",
            )
        ],
    )


@pytest.fixture
def fake_conn() -> Any:
    return AsyncMock()


async def test_run_returns_completed_output(fake_conn: Any) -> None:
    scorer = AsyncMock()
    scorer.score = AsyncMock(return_value=_ok_score())
    agent = DistinctivenessScorerAgent(conn=fake_conn, scorer=scorer)

    output = await agent.run(_agent_input())

    scorer.score.assert_awaited_once()
    assert output.agent_id == "distinctiveness_scorer"
    assert output.status == "completed"
    assert output.output["level"] == "distinctive"
    assert output.output["score"] == pytest.approx(0.72)
    assert output.duration_ms >= 0
    assert output.cost_usd > 0


async def test_run_skips_when_proposal_not_ready(fake_conn: Any) -> None:
    scorer = AsyncMock()
    scorer.score = AsyncMock(side_effect=ProposalNotReadyError("no excellence_md"))
    agent = DistinctivenessScorerAgent(conn=fake_conn, scorer=scorer)

    output = await agent.run(_agent_input())

    assert output.status == "skipped"
    assert output.output["level"] == "unknown"
    assert "no excellence_md" in output.metadata["reason"]
    assert output.metadata["reason_code"] == "proposal_not_ready"


async def test_run_skips_when_proposal_missing(fake_conn: Any) -> None:
    """ProposalNotFoundError also maps to skipped (with a discriminating code).

    AgentStatus only allows completed/failed/skipped, so 'not_found' is not a
    valid status. The reason_code lets the orchestrator distinguish later.
    """
    scorer = AsyncMock()
    scorer.score = AsyncMock(side_effect=ProposalNotFoundError("bogus"))
    agent = DistinctivenessScorerAgent(conn=fake_conn, scorer=scorer)

    output = await agent.run(_agent_input())

    assert output.status == "skipped"
    assert output.output["level"] == "unknown"
    assert output.metadata["reason_code"] == "proposal_not_found"
    assert "bogus" in output.metadata["reason"]


async def test_stream_yields_single_terminal_event(fake_conn: Any) -> None:
    scorer = AsyncMock()
    scorer.score = AsyncMock(return_value=_ok_score())
    agent = DistinctivenessScorerAgent(conn=fake_conn, scorer=scorer)

    events = [event async for event in agent.stream(_agent_input())]

    assert len(events) == 1
    payload = json.loads(events[0])
    assert payload["agent_id"] == "distinctiveness_scorer"
    assert payload["output"]["level"] == "distinctive"


def test_agent_class_metadata() -> None:
    assert DistinctivenessScorerAgent.agent_id == "distinctiveness_scorer"
    assert DistinctivenessScorerAgent.version == "v1"
    assert DistinctivenessScorerAgent.requires_rag is False
    assert DistinctivenessScorerAgent.estimated_duration_seconds == 5
