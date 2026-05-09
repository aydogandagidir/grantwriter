"""Hallucination Hunter agent tests with a stub verifier.

The verifier itself has its own test suite in tests/citations/. Here
we exercise the agent's aggregation: extraction across the three
writer outputs + correct ``recommendation`` flip on fabricated /
not_found results.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from src.agents.base import AgentInput
from src.agents.hallucination_hunter import HallucinationHunter, HuntReport
from src.citations import Citation, CitationVerifier, VerificationResult


class _StubVerifier(CitationVerifier):
    """Skips HTTP entirely — returns a script of pre-canned results."""

    def __init__(self, scripted: list[VerificationResult]) -> None:
        self._scripted = list(scripted)
        self._calls: list[Citation] = []

    async def verify(self, citation: Citation) -> VerificationResult:
        self._calls.append(citation)
        return self._scripted.pop(0)

    async def verify_many(self, citations: Sequence[Citation]) -> list[VerificationResult]:
        out: list[VerificationResult] = []
        for c in citations:
            out.append(await self.verify(c))
        return out

    @property
    def call_count(self) -> int:
        return len(self._calls)


def _agent_input(*, excellence_md: str, impact_md: str, implementation_md: str) -> AgentInput:
    def _wrap(agent_id: str, key: str, body: str) -> dict[str, object]:
        return {
            "agent_id": agent_id,
            "status": "completed",
            "output": {key: body},
        }

    return AgentInput(
        proposal_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        programme_id="horizon_eu_ria",
        language="en",
        brief={"title": "Test"},
        call={"call_text": ""},
        previous_outputs={
            "excellence_writer": _wrap("excellence_writer", "excellence_md", excellence_md),
            "impact_writer": _wrap("impact_writer", "impact_md", impact_md),
            "implementation_writer": _wrap(
                "implementation_writer", "implementation_md", implementation_md
            ),
        },
    )


async def test_block_export_when_any_citation_is_fabricated() -> None:
    excellence = "Per [Smith 2023] this is established."
    impact = "Per [Imaginary 2099] this is also true."
    implementation = "And see (Demir 2022) for the canonical work."

    verifier = _StubVerifier(
        scripted=[
            VerificationResult(status="verified", source="crossref", match_score=0.97),
            VerificationResult(status="fabricated", source="doi_direct"),
            VerificationResult(status="partial_match", source="crossref", match_score=0.7),
        ]
    )
    agent = HallucinationHunter(verifier=verifier)
    result = await agent.run(
        _agent_input(
            excellence_md=excellence,
            impact_md=impact,
            implementation_md=implementation,
        )
    )

    assert result.status == "completed"
    report = HuntReport.model_validate(result.output)
    assert report.total_citations == 3
    assert report.verified == 1
    assert report.fabricated == 1
    assert report.partial_match == 1
    assert report.recommendation == "block_export"
    assert verifier.call_count == 3

    flagged_raws = [item["raw_text"] for item in report.flagged_citations]
    assert "[Imaginary 2099]" in flagged_raws
    assert "(Demir 2022)" in flagged_raws
    # Verified ones are NOT flagged.
    assert "[Smith 2023]" not in flagged_raws


async def test_recommendation_ok_when_all_verified() -> None:
    excellence = "Per [Smith 2023] this is established."
    impact = "Per [Aydın 2024] this is also true."
    implementation = "And see (Demir 2022) for the canonical work."

    verifier = _StubVerifier(
        scripted=[
            VerificationResult(status="verified", source="crossref", match_score=0.97),
            VerificationResult(status="verified", source="openalex", match_score=0.92),
            VerificationResult(status="verified", source="doi_direct", match_score=1.0),
        ]
    )
    agent = HallucinationHunter(verifier=verifier)
    result = await agent.run(
        _agent_input(
            excellence_md=excellence,
            impact_md=impact,
            implementation_md=implementation,
        )
    )

    report = HuntReport.model_validate(result.output)
    assert report.recommendation == "ok"
    assert report.fabricated == 0
    assert report.not_found == 0
    expected_total = 3
    assert report.verification_rate == 1.0
    assert report.total_citations == expected_total


async def test_no_citations_returns_ok_with_zero_total() -> None:
    verifier = _StubVerifier(scripted=[])
    agent = HallucinationHunter(verifier=verifier)
    result = await agent.run(
        _agent_input(
            excellence_md="No citations here.",
            impact_md="Or here.",
            implementation_md="Or here.",
        )
    )
    report = HuntReport.model_validate(result.output)
    assert report.total_citations == 0
    assert report.recommendation == "ok"
    assert verifier.call_count == 0


async def test_not_found_also_blocks_export() -> None:
    """`not_found` is the OpenAlex no-match terminal state and counts
    as a blocker just like `fabricated`."""

    verifier = _StubVerifier(scripted=[VerificationResult(status="not_found", source="openalex")])
    agent = HallucinationHunter(verifier=verifier)
    result = await agent.run(
        _agent_input(
            excellence_md="Per [Ghost 2023] this is wrong.",
            impact_md="",
            implementation_md="",
        )
    )
    report = HuntReport.model_validate(result.output)
    assert report.recommendation == "block_export"
    assert report.not_found == 1
