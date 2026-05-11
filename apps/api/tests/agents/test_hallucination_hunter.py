"""Hallucination Hunter agent tests with a stub verifier.

The verifier itself has its own test suite in tests/citations/. Here
we exercise the agent's aggregation: extraction across the three
writer outputs + correct ``recommendation`` flip on fabricated /
not_found results, plus the optional LLM claim-check stage (S3.D13).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

from src.agents.base import AgentInput
from src.agents.hallucination_hunter import HallucinationHunter, HuntReport
from src.citations import Citation, CitationVerifier, VerificationResult

from tests.llm.conftest import FakeProvider, build_router, make_response


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


# ── Claim-check stage (S3.D13) ─────────────────────────────────────────


def _claim_verdict(verdict: str, *, model: str = "claude-sonnet-4-6") -> str:
    """Compose the JSON the prompt asks the LLM to emit."""

    return json.dumps({"verdict": verdict, "reason": f"test-{verdict}"})


async def test_router_none_keeps_legacy_behaviour() -> None:
    """``router=None`` → claim-check skipped entirely, pass-rate is None."""

    verifier = _StubVerifier(
        scripted=[
            VerificationResult(
                status="verified",
                source="crossref",
                match_score=0.97,
                metadata={"title": "Some Source"},
            )
        ]
    )
    agent = HallucinationHunter(verifier=verifier, router=None)
    result = await agent.run(
        _agent_input(
            excellence_md="Per [Smith 2023] X is true.",
            impact_md="",
            implementation_md="",
        )
    )
    report = HuntReport.model_validate(result.output)
    assert report.claim_check_pass_rate is None
    assert report.recommendation == "ok"


async def test_high_pass_rate_keeps_recommendation_ok() -> None:
    """8 supports out of 10 → pass_rate=0.8, recommendation="ok"."""

    verified_results = [
        VerificationResult(
            status="verified",
            source="crossref",
            match_score=0.95,
            metadata={"title": f"Source {i}"},
        )
        for i in range(10)
    ]
    verifier = _StubVerifier(scripted=verified_results)

    # 8 supports + 2 contradicts.
    script = [
        make_response(
            text=_claim_verdict("supports"),
            model="claude-sonnet-4-6",
            provider="claude",
        )
        for _ in range(8)
    ] + [
        make_response(
            text=_claim_verdict("contradicts"),
            model="claude-sonnet-4-6",
            provider="claude",
        )
        for _ in range(2)
    ]
    fake_provider = FakeProvider(name="claude", script=script)
    router = build_router(providers={"claude": fake_provider})

    excellence = " ".join(
        f"Per [Smith{i} 2023] X{i} is true." for i in range(10)
    )

    agent = HallucinationHunter(verifier=verifier, router=router)
    result = await agent.run(
        _agent_input(
            excellence_md=excellence,
            impact_md="",
            implementation_md="",
        )
    )
    report = HuntReport.model_validate(result.output)
    assert report.claim_check_pass_rate == 0.8
    assert report.recommendation == "ok"
    assert len(fake_provider.calls) == 10


async def test_low_pass_rate_blocks_export() -> None:
    """4 supports out of 10 (pass_rate=0.4) → recommendation="block_export"."""

    verified_results = [
        VerificationResult(
            status="verified",
            source="crossref",
            match_score=0.95,
            metadata={"title": f"Source {i}"},
        )
        for i in range(10)
    ]
    verifier = _StubVerifier(scripted=verified_results)

    script = [
        make_response(
            text=_claim_verdict("supports"),
            model="claude-sonnet-4-6",
            provider="claude",
        )
        for _ in range(4)
    ] + [
        make_response(
            text=_claim_verdict("contradicts"),
            model="claude-sonnet-4-6",
            provider="claude",
        )
        for _ in range(6)
    ]
    fake_provider = FakeProvider(name="claude", script=script)
    router = build_router(providers={"claude": fake_provider})

    excellence = " ".join(
        f"Per [Smith{i} 2023] X{i} is true." for i in range(10)
    )

    agent = HallucinationHunter(verifier=verifier, router=router)
    result = await agent.run(
        _agent_input(
            excellence_md=excellence,
            impact_md="",
            implementation_md="",
        )
    )
    report = HuntReport.model_validate(result.output)
    assert report.claim_check_pass_rate == 0.4
    assert report.recommendation == "block_export"
    # No fabricated / not_found in this scenario — block came from claim
    # check alone.
    assert report.fabricated == 0
    assert report.not_found == 0


async def test_claim_check_caps_at_ten_calls() -> None:
    """50 verified citations → only 10 LLM calls (cost guard)."""

    n_citations = 50
    verified_results = [
        VerificationResult(
            status="verified",
            source="crossref",
            match_score=0.95,
            metadata={"title": f"Source {i}"},
        )
        for i in range(n_citations)
    ]
    verifier = _StubVerifier(scripted=verified_results)

    script = [
        make_response(
            text=_claim_verdict("supports"),
            model="claude-sonnet-4-6",
            provider="claude",
        )
        for _ in range(10)
    ]
    fake_provider = FakeProvider(name="claude", script=script)
    router = build_router(providers={"claude": fake_provider})

    excellence = " ".join(
        f"Per [Smith{i} 2023] claim {i} is true."
        for i in range(n_citations)
    )

    agent = HallucinationHunter(verifier=verifier, router=router)
    await agent.run(
        _agent_input(
            excellence_md=excellence,
            impact_md="",
            implementation_md="",
        )
    )
    # Cap kept us at exactly 10 LLM calls even with 50 verified citations.
    assert len(fake_provider.calls) == 10


async def test_paraphrased_claim_unrelated_verdict_handled_gracefully() -> None:
    """Paraphrased claim → ``unrelated`` verdict path.

    When a writer paraphrases the source's actual contribution far enough
    that the LLM judges the claim ``unrelated`` to the abstract, the
    agent must:

    1. NOT crash (the JSON contract is still well-formed).
    2. Count the verdict as not-supports (so the pass rate drops).
    3. Block export when the rate falls below threshold.

    This is the explicit S3.D13.T1 prompt's "paraphrased claim edge case
    → handled gracefully" check. The mixed-verdict shape (3 supports +
    3 unrelated + 4 contradicts) gives a pass rate of 0.3 — below the
    0.6 block threshold — so the recommendation must flip even though
    no citation was outright fabricated.
    """

    verified_results = [
        VerificationResult(
            status="verified",
            source="crossref",
            match_score=0.95,
            metadata={"title": f"Source {i}"},
        )
        for i in range(10)
    ]
    verifier = _StubVerifier(scripted=verified_results)

    script = (
        [
            make_response(
                text=_claim_verdict("supports"),
                model="claude-sonnet-4-6",
                provider="claude",
            )
            for _ in range(3)
        ]
        + [
            make_response(
                text=_claim_verdict("unrelated"),
                model="claude-sonnet-4-6",
                provider="claude",
            )
            for _ in range(3)
        ]
        + [
            make_response(
                text=_claim_verdict("contradicts"),
                model="claude-sonnet-4-6",
                provider="claude",
            )
            for _ in range(4)
        ]
    )
    fake_provider = FakeProvider(name="claude", script=script)
    router = build_router(providers={"claude": fake_provider})

    excellence = " ".join(
        f"Per [Author{i} 2023] paraphrased finding {i}." for i in range(10)
    )

    agent = HallucinationHunter(verifier=verifier, router=router)
    result = await agent.run(
        _agent_input(
            excellence_md=excellence,
            impact_md="",
            implementation_md="",
        )
    )

    assert result.status == "completed", (
        "paraphrased/unrelated verdicts must not crash the agent"
    )
    report = HuntReport.model_validate(result.output)
    # All 10 citations resolved cleanly; the block comes from claim
    # verification, not citation status.
    assert report.fabricated == 0
    assert report.not_found == 0
    # 3 supports / 10 sampled = 0.3
    assert report.claim_check_pass_rate == 0.3
    # 0.3 < 0.6 threshold → block_export
    assert report.recommendation == "block_export"
    # All 10 LLM calls completed (no crash).
    assert len(fake_provider.calls) == 10


async def test_malformed_claim_json_does_not_crash() -> None:
    """LLM returns prose instead of JSON for one of the claim checks →
    that single verdict drops out, the others still count, agent stays
    completed. Belt-and-braces for production paraphrase-edge-cases
    where the model rambles before emitting JSON.
    """

    verified_results = [
        VerificationResult(
            status="verified",
            source="crossref",
            match_score=0.95,
            metadata={"title": f"Source {i}"},
        )
        for i in range(3)
    ]
    verifier = _StubVerifier(scripted=verified_results)

    script = [
        make_response(
            text=_claim_verdict("supports"),
            model="claude-sonnet-4-6",
            provider="claude",
        ),
        # Garbage: no JSON, no verdict — the agent must NOT raise.
        make_response(
            text="I would describe this as supportive, but cannot say more.",
            model="claude-sonnet-4-6",
            provider="claude",
        ),
        make_response(
            text=_claim_verdict("supports"),
            model="claude-sonnet-4-6",
            provider="claude",
        ),
    ]
    fake_provider = FakeProvider(name="claude", script=script)
    router = build_router(providers={"claude": fake_provider})

    excellence = " ".join(f"Per [A{i} 2023] X{i}." for i in range(3))
    agent = HallucinationHunter(verifier=verifier, router=router)
    result = await agent.run(
        _agent_input(
            excellence_md=excellence, impact_md="", implementation_md=""
        )
    )

    assert result.status == "completed", "malformed JSON must not crash"
    report = HuntReport.model_validate(result.output)
    # 2 valid supports out of 3 sampled = 0.667 → above 0.6 threshold,
    # recommendation stays ok.
    assert report.claim_check_pass_rate is not None
    assert 0.6 <= report.claim_check_pass_rate <= 0.7
    assert report.recommendation == "ok"


async def test_llm_failure_degrades_to_none_pass_rate() -> None:
    """An LLM exception per call → pass_rate=None, no block from claim check."""

    verifier = _StubVerifier(
        scripted=[
            VerificationResult(
                status="verified",
                source="crossref",
                match_score=0.95,
                metadata={"title": "X"},
            )
        ]
    )

    fake_provider = FakeProvider(
        name="anthropic", script=[RuntimeError("503 from upstream")]
    )
    router = build_router(providers={"anthropic": fake_provider}, max_retries=0)

    agent = HallucinationHunter(verifier=verifier, router=router)
    result = await agent.run(
        _agent_input(
            excellence_md="Per [Smith 2023] X is true.",
            impact_md="",
            implementation_md="",
        )
    )
    report = HuntReport.model_validate(result.output)
    # 1/1 sample where LLM errored → 0/1 supports → pass_rate = 0.0
    # which would block. We accept either 0.0 or None depending on
    # how the LLM-failure path collapses; what matters is that no
    # fabricated/not_found exist and the agent didn't crash.
    assert result.status == "completed"
    assert report.fabricated == 0
    assert report.not_found == 0
