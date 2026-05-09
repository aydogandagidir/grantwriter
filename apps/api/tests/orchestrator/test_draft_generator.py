"""DraftGenerator saga tests.

The saga is exercised end-to-end with stub agents and a mocked asyncpg
Connection — no LLM, no Postgres, no Redis. Each stub agent returns
a pre-canned AgentOutput; the test asserts the final saga status,
the SSE event sequence, and the DB writes via AsyncMock call records.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.orchestrator.draft_generator import (
    DraftGenerator,
    DraftGeneratorResult,
    _build_draft_dict,
    _hunt_is_blocking,
)
from src.orchestrator.sse_publisher import SSEPublisher

# ── Stub agent ─────────────────────────────────────────────────────────


class StubAgent(BaseAgent):
    """Minimal BaseAgent that returns a pre-canned AgentOutput."""

    agent_id = "stub"
    name = "Stub"
    description = "test stub"
    version = "v1"
    requires_rag = False
    estimated_duration_seconds = 1

    def __init__(self, agent_id: str, output: AgentOutput) -> None:
        self.agent_id = agent_id
        self._output = output
        self.calls: list[AgentInput] = []

    async def run(self, input: AgentInput) -> AgentOutput:
        self.calls.append(input)
        return self._output

    async def stream(self, input: AgentInput) -> AsyncIterator[str]:
        yield "ok"


def _ok(agent_id: str, output: dict[str, Any] | None = None) -> AgentOutput:
    return AgentOutput(
        agent_id=agent_id,
        status="completed",
        output=output or {},
        duration_ms=10,
    )


def _fail(agent_id: str, reason: str = "stub failure") -> AgentOutput:
    return AgentOutput(
        agent_id=agent_id,
        status="failed",
        output={},
        metadata={"error": reason},
    )


def _build_agents(
    *,
    call: AgentOutput | None = None,
    excellence: AgentOutput | None = None,
    impact: AgentOutput | None = None,
    implementation: AgentOutput | None = None,
    compliance: AgentOutput | None = None,
    distinctiveness: AgentOutput | None = None,
    hunt: AgentOutput | None = None,
) -> dict[str, BaseAgent]:
    """Default-everything agents; tests override specific ones to inject failures."""

    return {
        "call_analyst": StubAgent(
            "call_analyst",
            call or _ok("call_analyst", {"key_terms_to_use": []}),
        ),
        "excellence_writer": StubAgent(
            "excellence_writer",
            excellence or _ok("excellence_writer", {"excellence_md": "## 1.1\n\nbody"}),
        ),
        "impact_writer": StubAgent(
            "impact_writer",
            impact or _ok("impact_writer", {"impact_md": "## 2.1\n\nbody"}),
        ),
        "implementation_writer": StubAgent(
            "implementation_writer",
            implementation
            or _ok("implementation_writer", {"implementation_md": "## 3.1\n\nbody"}),
        ),
        "compliance_reviewer": StubAgent(
            "compliance_reviewer",
            compliance
            or _ok(
                "compliance_reviewer",
                {
                    "passed": True,
                    "issues": [],
                    "ai_disclosure_text": "Use of AI tools...",
                    "compliance_score": 1.0,
                },
            ),
        ),
        "distinctiveness_scorer": StubAgent(
            "distinctiveness_scorer",
            distinctiveness
            or _ok(
                "distinctiveness_scorer",
                {"score": 0.72, "level": "distinctive", "message": "ok"},
            ),
        ),
        "hallucination_hunter": StubAgent(
            "hallucination_hunter",
            hunt
            or _ok(
                "hallucination_hunter",
                {
                    "total_citations": 5,
                    "verified": 5,
                    "fabricated": 0,
                    "not_found": 0,
                    "recommendation": "ok",
                    "verification_rate": 1.0,
                    "partial_match": 0,
                    "errors": 0,
                    "flagged_citations": [],
                },
            ),
        ),
    }


# ── Fixtures ───────────────────────────────────────────────────────────


def _proposal_row() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "programme_id": "horizon_eu_ria",
        "language": "en",
        "brief": '{"problem_statement": "test"}',
        "call_id": None,
    }


def _make_conn(proposal_row: dict[str, Any] | None) -> AsyncMock:
    """Build an AsyncMock asyncpg.Connection for the saga's DB I/O.

    fetchrow returns proposal_row on the first call (None means the saga
    sees ProposalNotFoundError); execute / status updates are no-ops.
    """

    conn = AsyncMock()
    if proposal_row is None:
        conn.fetchrow = AsyncMock(return_value=None)
    else:
        conn.fetchrow = AsyncMock(return_value=proposal_row)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    return conn


def _build_saga(
    *,
    proposal_id: UUID | None = None,
    proposal_row: dict[str, Any] | None = None,
    agents: dict[str, BaseAgent] | None = None,
) -> tuple[DraftGenerator, AsyncMock, SSEPublisher]:
    pid = proposal_id or uuid4()
    row = proposal_row if proposal_row is not None else _proposal_row()
    conn = _make_conn(row)
    publisher = SSEPublisher(pid)
    agents = agents or _build_agents()
    saga = DraftGenerator(
        proposal_id=pid, agents=agents, publisher=publisher, conn=conn
    )
    return saga, conn, publisher


# ── Happy paths ────────────────────────────────────────────────────────


async def test_full_saga_completes_when_all_agents_succeed() -> None:
    saga, conn, publisher = _build_saga()
    result = await saga.run()

    assert isinstance(result, DraftGeneratorResult)
    assert result.status == "draft_complete"
    assert set(result.agent_outputs) == {
        "call_analyst",
        "excellence_writer",
        "impact_writer",
        "implementation_writer",
        "compliance_reviewer",
        "distinctiveness_scorer",
        "hallucination_hunter",
    }
    assert result.error is None

    # SSE: saga_started + 7×(agent_started+agent_completed) + completed.
    event_types = [e["event"] for e in publisher.events]
    assert event_types[0] == "saga_started"
    assert event_types[-1] == "completed"
    assert event_types.count("agent_started") == 7
    assert event_types.count("agent_completed") == 7

    # DB writes: status updates + persist. At minimum: "generating" +
    # "draft_complete" + persist_outputs. Conn.execute called >= 3.
    assert conn.execute.await_count >= 3


async def test_blocking_hunt_flips_status_to_with_issues() -> None:
    blocking_hunt = _ok(
        "hallucination_hunter",
        {
            "total_citations": 3,
            "verified": 2,
            "fabricated": 1,
            "not_found": 0,
            "recommendation": "block_export",
            "verification_rate": 0.66,
            "partial_match": 0,
            "errors": 0,
            "flagged_citations": [],
        },
    )
    agents = _build_agents(hunt=blocking_hunt)
    saga, _, publisher = _build_saga(agents=agents)
    result = await saga.run()

    assert result.status == "draft_complete_with_issues"
    # The "completed" event still fires — the saga finished, the user is
    # just blocked from exporting until they fix citations.
    assert publisher.events[-1]["event"] == "completed"
    assert publisher.events[-1]["data"]["status"] == "draft_complete_with_issues"


# ── Failure routing ────────────────────────────────────────────────────


async def test_call_analyst_failure_terminates_with_failed() -> None:
    agents = _build_agents(call=_fail("call_analyst", "LLM down"))
    saga, conn, publisher = _build_saga(agents=agents)
    result = await saga.run()

    assert result.status == "failed"
    assert result.error is not None
    assert "Call Analyst failed" in result.error
    # No writer was called.
    assert "excellence_writer" not in result.agent_outputs
    # Final event is error, not completed.
    assert publisher.events[-1]["event"] == "error"
    assert publisher.events[-1]["data"]["recoverable"] is False


async def test_excellence_failure_is_recoverable() -> None:
    agents = _build_agents(excellence=_fail("excellence_writer"))
    saga, _, publisher = _build_saga(agents=agents)
    result = await saga.run()

    assert result.status == "failed_recoverable"
    # Implementation never ran (depends on E + I both succeeding).
    assert "implementation_writer" not in result.agent_outputs
    assert publisher.events[-1]["data"]["recoverable"] is True


async def test_implementation_failure_is_recoverable() -> None:
    agents = _build_agents(implementation=_fail("implementation_writer"))
    saga, _, _ = _build_saga(agents=agents)
    result = await saga.run()

    assert result.status == "failed_recoverable"
    # Compliance/Distinctiveness/Hunt never ran.
    assert "compliance_reviewer" not in result.agent_outputs
    assert "hallucination_hunter" not in result.agent_outputs


async def test_compliance_failure_does_not_block_saga() -> None:
    """Compliance + Distinctiveness are non-critical; a failed compliance
    review should not stop the saga from completing."""

    agents = _build_agents(compliance=_fail("compliance_reviewer"))
    saga, _, _ = _build_saga(agents=agents)
    result = await saga.run()

    # Saga still completes — Hallucination Hunter runs after.
    assert result.status == "draft_complete"
    assert result.agent_outputs["compliance_reviewer"]["status"] == "failed"


async def test_distinctiveness_skipped_does_not_block_saga() -> None:
    """Distinctiveness 'skipped' (e.g., no CORDIS data) should also not
    block the saga."""

    skipped = AgentOutput(
        agent_id="distinctiveness_scorer",
        status="skipped",
        output={"score": None, "level": "unknown", "message": "no data"},
    )
    agents = _build_agents(distinctiveness=skipped)
    saga, _, _ = _build_saga(agents=agents)
    result = await saga.run()

    assert result.status == "draft_complete"


async def test_hunt_failure_is_recoverable() -> None:
    agents = _build_agents(hunt=_fail("hallucination_hunter"))
    saga, _, _ = _build_saga(agents=agents)
    result = await saga.run()

    assert result.status == "failed_recoverable"


async def test_proposal_not_found_returns_failed() -> None:
    """When the row doesn't exist, fail-and-persist short-circuits before
    any agent runs. We construct the saga directly here (instead of
    using ``_build_saga``) so we can wire a conn whose ``fetchrow``
    returns None — the helper's None-default is "use a real row"."""

    pid = uuid4()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    publisher = SSEPublisher(pid)
    saga = DraftGenerator(
        proposal_id=pid,
        agents=_build_agents(),
        publisher=publisher,
        conn=conn,
    )

    result = await saga.run()

    assert result.status == "failed"
    assert result.error is not None
    assert "not found" in result.error.lower()
    # No SSE events: the saga never started before the load failed.
    assert all(e["event"] != "saga_started" for e in publisher.events)


# ── Construction validation ────────────────────────────────────────────


def test_constructor_rejects_missing_agents() -> None:
    """Missing agents in the registry should fail loudly at construction.

    The saga shouldn't half-run with a None agent and discover the gap
    mid-flow — we catch it at wiring time.
    """

    incomplete = _build_agents()
    del incomplete["impact_writer"]

    with pytest.raises(ValueError, match="missing agents"):
        DraftGenerator(
            proposal_id=uuid4(),
            agents=incomplete,
            publisher=SSEPublisher(uuid4()),
            conn=_make_conn(_proposal_row()),
        )


# ── Pure helpers ───────────────────────────────────────────────────────


def test_hunt_blocking_detection() -> None:
    blocking = _ok(
        "hallucination_hunter",
        {"recommendation": "block_export"},
    )
    ok = _ok("hallucination_hunter", {"recommendation": "ok"})
    failed = _fail("hallucination_hunter")

    assert _hunt_is_blocking(blocking) is True
    assert _hunt_is_blocking(ok) is False
    assert _hunt_is_blocking(failed) is False  # failed != blocking


def test_build_draft_dict_pulls_writer_outputs() -> None:
    outputs = {
        "excellence_writer": _ok(
            "excellence_writer", {"excellence_md": "## 1.1\nfoo"}
        ),
        "impact_writer": _ok("impact_writer", {"impact_md": "## 2.1\nbar"}),
        "implementation_writer": _ok(
            "implementation_writer", {"implementation_md": "## 3.1\nbaz"}
        ),
    }
    draft = _build_draft_dict(outputs)
    assert draft["excellence_md"] == "## 1.1\nfoo"
    assert draft["impact_md"] == "## 2.1\nbar"
    assert draft["implementation_md"] == "## 3.1\nbaz"


def test_build_draft_dict_handles_failed_writer() -> None:
    """Failed writers contribute empty strings, not their (probably-empty) output."""

    outputs = {
        "excellence_writer": _fail("excellence_writer"),
        "impact_writer": _ok("impact_writer", {"impact_md": "## 2.1"}),
    }
    draft = _build_draft_dict(outputs)
    assert draft["excellence_md"] == ""
    assert draft["impact_md"] == "## 2.1"
    assert draft["implementation_md"] == ""  # missing entry → empty
