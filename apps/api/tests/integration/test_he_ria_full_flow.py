"""Full Saga end-to-end test for Horizon Europe RIA.

Drives :class:`DraftGenerator` through all 7 agents using:
- :class:`FakeProvider` for the LLM (5 scripted responses keyed by
  ``request.task``)
- A stub :class:`CitationVerifier` that returns ``verified`` for every
  citation
- An ``AsyncMock`` asyncpg connection for the saga's own writes
- A second ``AsyncMock`` connection for distinctiveness — set up to
  return None so the scorer skips gracefully (avoids a real
  pgvector + CORDIS dependency in this test)

This is the contract test for the saga's wiring, not a functional
test of any single agent. It catches regressions like "agents stop
talking to each other" or "the saga drops an event" before they hit
production.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from unittest.mock import AsyncMock

import pytest
from src.agents import (
    CallAnalyst,
    ComplianceReviewer,
    ExcellenceWriter,
    HallucinationHunter,
    ImpactWriter,
    ImplementationWriter,
)
from src.agents.distinctiveness_scorer import DistinctivenessScorerAgent
from src.citations import Citation, CitationVerifier, VerificationResult
from src.compliance.distinctiveness import ProposalNotFoundError
from src.llm.base import LLMRequest, LLMResponse
from src.orchestrator.draft_generator import DraftGenerator
from src.orchestrator.sse_publisher import SSEPublisher
from tests.llm.conftest import FakeProvider, build_router, make_response

# ── Canned LLM responses ────────────────────────────────────────────────


CALL_ANALYST_JSON = json.dumps(
    {
        "eligibility": {
            "eligible_countries": ["EU MS", "Associated Countries"],
            "eligible_entities": ["legal entity"],
            "min_partners": 3,
            "min_countries": 3,
            "trl_range": [4, 6],
        },
        "scope_summary": "Industrial digital twins for manufacturing.",
        "expected_outcomes": ["Validated runtime"],
        "expected_impacts": ["25% downtime reduction"],
        "evaluation_criteria": [
            {"criterion": "Excellence", "weight": 0.5, "sub_criteria": []},
        ],
        "page_limit": 45,
        "language_required": "en",
        "user_eligible": True,
        "user_eligibility_issues": [],
        "key_terms_to_use": ["digital twin", "edge AI", "FAIR"],
        "deadlines": {"submission": "2026-09-15"},
    }
)

CANNED_EXCELLENCE = """\
## 1.1 Objectives and ambition

Edge-deployed YOLOv9-tiny for textile QC, reaching TRL 6 on three pilots.
Gender dimension integrated through balanced data sampling.

## 1.2 Methodology

Theory-of-change links data → model → field deployment, citing [Smith 2023].

## 1.3 State of the art

Prior work [Aydın et al. 2024] addressed lab settings; we extend to production.

## 1.4 Open science practices

FAIR principles + DMP committed at month 3; code under EUPL.
"""

CANNED_IMPACT = """\
## 2.1 Project's pathways towards impact

KIPs: 25% defect reduction, 200 SMEs adopting in 5 years.

## 2.2 Measures to maximise impact

Dissemination via OPC UA standards; DNSH considered for the six EU
environmental objectives.

## 2.3 Summary canvas (key impact pathways)

- Specific needs: 1 200 SMEs in EU tooling sector
- Outcomes: validated runtime + standards-track contribution
"""

CANNED_IMPLEMENTATION = """\
## 3.1 Work plan and resources

WP1 (data) — 18 PM. WP2 (model) — 22 PM. WP3 (pilot) — 16 PM.

## 3.2 Capacity of the participants

5 years AR-GE leadership; synthetic-data pipelines for woven textiles.

## 3.3 Consortium as a whole

Three partners across NL / DE / TR balance industry + research.
"""

COMPLIANCE_LLM_JSON = json.dumps(
    {
        "dnsh": {"present": True, "severity": "ok", "explanation": "Six objectives addressed."},
        "gender_dimension": {
            "present": True,
            "severity": "ok",
            "explanation": "Integrated into methodology.",
        },
        "open_science": {
            "present": True,
            "severity": "ok",
            "explanation": "FAIR + DMP committed.",
        },
    }
)

_CANNED_BY_TASK: dict[str, str] = {
    "call_analyst": CALL_ANALYST_JSON,
    "excellence_writer": CANNED_EXCELLENCE,
    "impact_writer": CANNED_IMPACT,
    "implementation_writer": CANNED_IMPLEMENTATION,
    "compliance_reviewer": COMPLIANCE_LLM_JSON,
}


def _task_aware_response(
    request: LLMRequest, model: str, _api_key: str
) -> LLMResponse:
    """FakeProvider script entry that branches by ``request.task``.

    Writers run in parallel (Excellence ‖ Impact in step 2; Compliance ‖
    Distinctiveness in step 4 — though distinctiveness skips the LLM),
    so the order responses are consumed is non-deterministic. Routing
    by task makes the test robust to scheduling.
    """

    text = _CANNED_BY_TASK.get(request.task)
    if text is None:
        raise AssertionError(
            f"unexpected LLM task {request.task!r} — saga is calling more "
            "agents than the canned-response map covers"
        )
    return make_response(text=text, model=model, provider="claude")


# ── Stubs ──────────────────────────────────────────────────────────────


class _StubVerifier(CitationVerifier):
    """Citation verifier that always returns ``verified``.

    Hallucination Hunter extracts citations from the canned writer
    Markdown; this stub keeps the saga's recommendation = "ok" so the
    final status is ``draft_complete`` rather than
    ``draft_complete_with_issues``.
    """

    def __init__(self) -> None:
        self.calls: list[Citation] = []

    async def verify(self, citation: Citation) -> VerificationResult:
        self.calls.append(citation)
        return VerificationResult(
            status="verified", source="crossref", match_score=0.97
        )

    async def verify_many(
        self, citations: Sequence[Citation]
    ) -> list[VerificationResult]:
        out: list[VerificationResult] = []
        for c in citations:
            out.append(await self.verify(c))
        return out


def _build_main_conn() -> AsyncMock:
    """Saga's primary asyncpg connection — answers _load_proposal +
    swallows _persist_outputs / _update_status writes."""

    proposal_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": proposal_id,
            "tenant_id": tenant_id,
            "programme_id": "horizon_eu_ria",
            "language": "en",
            "brief": json.dumps(
                {
                    "title": "GREENMOBILITY",
                    "acronym": "GMOB",
                    "problem_statement": "EU manufacturers depend on imports.",
                    "proposed_solution": "Edge-deployed YOLOv9-tiny.",
                    "trl_current": 4,
                    "trl_target": 6,
                    "duration_months": 24,
                    "partners": (
                        "ACME (NL, SME)\n"
                        "Fraunhofer IPK (DE, RTO)\n"
                        "Gamma (TR, SME)\n"
                    ),
                }
            ),
            "call_id": None,
        }
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.proposal_id = proposal_id
    return conn


def _build_distinctiveness_conn() -> AsyncMock:
    """Distinctiveness scorer's connection — fetchrow returns None so
    the scorer raises ``ProposalNotFoundError`` and the agent skips
    gracefully (saga treats skipped as non-blocking)."""

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="UPDATE 1")
    return conn


def _build_compliance_conn() -> AsyncMock:
    """Compliance reviewer's connection — used for AI disclosure
    provenance lookup. Returns empty rows so generate_ai_disclosure
    returns None gracefully."""

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


# ── Tests ──────────────────────────────────────────────────────────────


async def test_full_saga_runs_all_seven_agents_to_draft_complete() -> None:
    """Happy path: every agent succeeds, saga writes to DB, recommends OK."""

    primary = FakeProvider("claude", [_task_aware_response] * 5)
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    main_conn = _build_main_conn()
    compliance_conn = _build_compliance_conn()
    distinctiveness_conn = _build_distinctiveness_conn()

    agents = {
        "call_analyst": CallAnalyst(router=router),
        "excellence_writer": ExcellenceWriter(router=router),
        "impact_writer": ImpactWriter(router=router),
        "implementation_writer": ImplementationWriter(router=router),
        "compliance_reviewer": ComplianceReviewer(
            router=router, conn=compliance_conn
        ),
        "distinctiveness_scorer": DistinctivenessScorerAgent(
            conn=distinctiveness_conn
        ),
        "hallucination_hunter": HallucinationHunter(verifier=_StubVerifier()),
    }

    proposal_id = main_conn.proposal_id  # set by _build_main_conn
    publisher = SSEPublisher(proposal_id)
    saga = DraftGenerator(
        proposal_id=proposal_id,
        agents=agents,
        publisher=publisher,
        conn=main_conn,
    )

    result = await saga.run()

    # Final status reflects a successful end-to-end run.
    assert result.status == "draft_complete", result.error
    assert result.error is None

    # Every agent contributed an output.
    assert set(result.agent_outputs) == {
        "call_analyst",
        "excellence_writer",
        "impact_writer",
        "implementation_writer",
        "compliance_reviewer",
        "distinctiveness_scorer",
        "hallucination_hunter",
    }

    # Distinctiveness skipped (no CORDIS data); writers + compliance + hunt all completed.
    assert result.agent_outputs["distinctiveness_scorer"]["status"] == "skipped"
    for required in (
        "call_analyst",
        "excellence_writer",
        "impact_writer",
        "implementation_writer",
        "compliance_reviewer",
        "hallucination_hunter",
    ):
        assert (
            result.agent_outputs[required]["status"] == "completed"
        ), f"{required} did not complete: {result.agent_outputs[required]}"


async def test_full_saga_persists_writer_outputs_to_proposals_row() -> None:
    """Saga's _persist_outputs writes the assembled draft + compliance report."""

    primary = FakeProvider("claude", [_task_aware_response] * 5)
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    main_conn = _build_main_conn()

    agents = {
        "call_analyst": CallAnalyst(router=router),
        "excellence_writer": ExcellenceWriter(router=router),
        "impact_writer": ImpactWriter(router=router),
        "implementation_writer": ImplementationWriter(router=router),
        "compliance_reviewer": ComplianceReviewer(
            router=router, conn=_build_compliance_conn()
        ),
        "distinctiveness_scorer": DistinctivenessScorerAgent(
            conn=_build_distinctiveness_conn()
        ),
        "hallucination_hunter": HallucinationHunter(verifier=_StubVerifier()),
    }

    saga = DraftGenerator(
        proposal_id=main_conn.proposal_id,
        agents=agents,
        publisher=SSEPublisher(main_conn.proposal_id),
        conn=main_conn,
    )

    await saga.run()

    # The persist step writes one big UPDATE — find it among execute() calls.
    persist_calls = [
        call
        for call in main_conn.execute.await_args_list
        if "set draft" in str(call.args[0]).lower()
    ]
    assert len(persist_calls) == 1
    sql = persist_calls[0].args[0]
    args = persist_calls[0].args[1:]

    # Args order: draft_json, compliance_report_json, ai_disclosure_text,
    # distinctiveness_score, proposal_id.
    draft_json = args[0]
    assert "## 1.1 Objectives" in draft_json
    assert "## 2.1 Project" in draft_json
    assert "## 3.1 Work plan" in draft_json
    # compliance_report should include the LLM verdict shape (passed: True).
    compliance_json = args[1]
    assert "passed" in compliance_json.lower()
    # AI disclosure is None (no provenance rows in test).
    assert args[2] is None
    # Distinctiveness skipped → score is None.
    assert args[3] is None
    # SQL touches the right columns.
    assert "compliance_report" in sql.lower()
    assert "ai_disclosure_text" in sql.lower()
    assert "distinctiveness_score" in sql.lower()


async def test_full_saga_publishes_sse_events_in_order() -> None:
    """SSE event sequence: saga_started → agent_*started/completed → completed."""

    primary = FakeProvider("claude", [_task_aware_response] * 5)
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    main_conn = _build_main_conn()
    publisher = SSEPublisher(main_conn.proposal_id)

    agents = {
        "call_analyst": CallAnalyst(router=router),
        "excellence_writer": ExcellenceWriter(router=router),
        "impact_writer": ImpactWriter(router=router),
        "implementation_writer": ImplementationWriter(router=router),
        "compliance_reviewer": ComplianceReviewer(
            router=router, conn=_build_compliance_conn()
        ),
        "distinctiveness_scorer": DistinctivenessScorerAgent(
            conn=_build_distinctiveness_conn()
        ),
        "hallucination_hunter": HallucinationHunter(verifier=_StubVerifier()),
    }

    saga = DraftGenerator(
        proposal_id=main_conn.proposal_id,
        agents=agents,
        publisher=publisher,
        conn=main_conn,
    )
    result = await saga.run()
    assert result.status == "draft_complete"

    event_types = [e["event"] for e in publisher.events]

    # Sanity: starts with saga_started, ends with completed.
    assert event_types[0] == "saga_started"
    assert event_types[-1] == "completed"

    # Seven agents × (started + completed/skipped/failed) = 14 events,
    # plus saga_started + completed = 16 total. The skipped distinctiveness
    # still emits started + agent_failed (since status != "completed"),
    # so the agent_started count is 7 and agent_completed + agent_failed
    # together is also 7.
    assert event_types.count("saga_started") == 1
    assert event_types.count("completed") == 1
    assert event_types.count("agent_started") == 7
    assert event_types.count("agent_completed") + event_types.count(
        "agent_failed"
    ) == 7


async def test_full_saga_makes_exactly_five_llm_calls() -> None:
    """Cost regression guard: the saga should not call the LLM more
    than once per LLM-driven agent. Distinctiveness uses embeddings,
    Hallucination Hunter is verifier-only — neither hits the LLM."""

    primary = FakeProvider("claude", [_task_aware_response] * 5)
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    main_conn = _build_main_conn()

    agents = {
        "call_analyst": CallAnalyst(router=router),
        "excellence_writer": ExcellenceWriter(router=router),
        "impact_writer": ImpactWriter(router=router),
        "implementation_writer": ImplementationWriter(router=router),
        "compliance_reviewer": ComplianceReviewer(
            router=router, conn=_build_compliance_conn()
        ),
        "distinctiveness_scorer": DistinctivenessScorerAgent(
            conn=_build_distinctiveness_conn()
        ),
        "hallucination_hunter": HallucinationHunter(verifier=_StubVerifier()),
    }

    saga = DraftGenerator(
        proposal_id=main_conn.proposal_id,
        agents=agents,
        publisher=SSEPublisher(main_conn.proposal_id),
        conn=main_conn,
    )
    await saga.run()

    tasks_called = sorted(req.task for req, _model, _key in primary.calls)
    assert tasks_called == sorted(
        [
            "call_analyst",
            "excellence_writer",
            "impact_writer",
            "implementation_writer",
            "compliance_reviewer",
        ]
    )
    assert len(primary.calls) == 5


async def test_writer_failure_routes_to_failed_recoverable() -> None:
    """If a writer's LLM call returns malformed JSON, the agent reports
    status='failed' and the saga maps that to ``failed_recoverable``."""

    # Replace the excellence response with broken JSON that will fail
    # the agent's parsing (writers don't always parse JSON, but the
    # behaviour we want here is that one writer step fails — easiest
    # way: have FakeProvider raise on the second LLM call).
    def script(
        request: LLMRequest, model: str, api_key: str
    ) -> LLMResponse | Exception:
        if request.task == "excellence_writer":
            from src.llm.base import LLMUnrecoverableError

            return LLMUnrecoverableError("simulated LLM 400")
        return _task_aware_response(request, model, api_key)

    primary = FakeProvider("claude", [script] * 5)
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    main_conn = _build_main_conn()
    agents = {
        "call_analyst": CallAnalyst(router=router),
        "excellence_writer": ExcellenceWriter(router=router),
        "impact_writer": ImpactWriter(router=router),
        "implementation_writer": ImplementationWriter(router=router),
        "compliance_reviewer": ComplianceReviewer(
            router=router, conn=_build_compliance_conn()
        ),
        "distinctiveness_scorer": DistinctivenessScorerAgent(
            conn=_build_distinctiveness_conn()
        ),
        "hallucination_hunter": HallucinationHunter(verifier=_StubVerifier()),
    }
    saga = DraftGenerator(
        proposal_id=main_conn.proposal_id,
        agents=agents,
        publisher=SSEPublisher(main_conn.proposal_id),
        conn=main_conn,
    )

    result = await saga.run()
    assert result.status == "failed_recoverable"
    # Implementation never ran (depends on E + I both succeeding).
    assert result.agent_outputs.get("implementation_writer") is None
    # Hallucination Hunter never ran either.
    assert result.agent_outputs.get("hallucination_hunter") is None


# ── Cleanup hint ───────────────────────────────────────────────────────


_ = (ProposalNotFoundError, pytest)  # keep optional imports referenced
