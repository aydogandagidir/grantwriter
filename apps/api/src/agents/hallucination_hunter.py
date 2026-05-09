"""Hallucination Hunter — final-pass citation verification agent.

Runs after the writer agents have produced their Markdown. Extracts every
citation marker from excellence / impact / implementation, fans them out
to the :class:`~src.citations.CitationVerifier`, and emits a
:class:`HuntReport` that the orchestrator + frontend treat as the
export-blocking gate (docs/06 §4.6).

S2.D7.T1 ships citation verification only. Sample-based **claim**
verification (LLM-cross-check that a claim's source actually contains
the claim, docs/06 §4.6) is a hook this report leaves room for: see the
``claim_check_pass_rate`` field which is currently always ``None``.
That part lands in S2.D8+ when the orchestrator wires the LLM router
into this agent.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.citations import Citation, CitationVerifier, VerificationResult, extract_citations

logger = logging.getLogger(__name__)


HuntRecommendation = Literal["block_export", "ok"]
_FLAGGED_CITATION_CAP = 50
"""Cap how many flagged citations the report carries inline. The
underlying ``citations`` table is the source of truth; the report is
for at-a-glance UI use and bigger lists overwhelm both."""

_SECTION_KEYS: list[tuple[str, str]] = [
    ("excellence_writer", "excellence_md"),
    ("impact_writer", "impact_md"),
    ("implementation_writer", "implementation_md"),
]


class HuntReport(BaseModel):
    """Top-level Hallucination Hunter output. Frontend renders the
    badges + decides whether to enable the export button from this."""

    model_config = ConfigDict(frozen=True)

    total_citations: int
    verified: int
    partial_match: int
    fabricated: int
    not_found: int
    errors: int
    verification_rate: float
    """``verified / max(1, total)`` — friendly UI metric, NOT used for
    the export decision (which is binary on ``recommendation``)."""
    flagged_citations: list[dict[str, Any]] = Field(default_factory=list)
    """One entry per non-verified citation: ``{raw_text, status,
    section, source, match_score, warning}``. Capped at 50 — the DB
    has the full list."""
    recommendation: HuntRecommendation
    claim_check_pass_rate: float | None = None
    """Sample-based claim verification (S2.D8+). None for v1."""


class HallucinationHunter(BaseAgent):
    agent_id = "hallucination_hunter"
    name = "Hallucination Hunter"
    description = (
        "Final-pass citation verification. Aggregates Crossref / OpenAlex "
        "results and flips export to blocked when fabricated citations are present."
    )
    version = "v1"
    requires_rag = False
    estimated_duration_seconds = 60

    def __init__(self, verifier: CitationVerifier) -> None:
        self._verifier = verifier

    async def run(self, input: AgentInput) -> AgentOutput:
        started = time.monotonic()

        # 1. Pull every section's Markdown out of previous_outputs and
        #    extract citations per section so the flagged list stays
        #    section-attributed for the UI.
        per_section: list[tuple[str, list[Citation]]] = []
        for agent_id, key in _SECTION_KEYS:
            section_md = self._previous_section(input, agent_id, key)
            if not section_md:
                continue
            per_section.append((key.replace("_md", ""), extract_citations(section_md)))

        all_citations: list[Citation] = []
        section_for_index: list[str] = []
        for section_label, section_citations in per_section:
            for citation in section_citations:
                all_citations.append(citation)
                section_for_index.append(section_label)

        if not all_citations:
            report = HuntReport(
                total_citations=0,
                verified=0,
                partial_match=0,
                fabricated=0,
                not_found=0,
                errors=0,
                verification_rate=1.0,
                recommendation="ok",
            )
            return _agent_output(report, started=started, input=input, no_citations=True)

        # 2. Verify the lot. The verifier semaphore caps concurrency.
        try:
            results = await self._verifier.verify_many(all_citations)
        except Exception as exc:
            logger.exception("hallucination_hunter_verify_many_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                metadata={"error": f"verification batch failed: {exc}"},
            )

        # 3. Tally + flag list.
        verified = sum(1 for r in results if r.status == "verified")
        partial = sum(1 for r in results if r.status == "partial_match")
        fabricated = sum(1 for r in results if r.status == "fabricated")
        not_found = sum(1 for r in results if r.status == "not_found")
        errors = sum(1 for r in results if r.status == "error")

        flagged: list[dict[str, Any]] = []
        for citation, result, section_label in zip(
            all_citations, results, section_for_index, strict=True
        ):
            if result.status == "verified":
                continue
            flagged.append(
                {
                    "raw_text": citation.raw_text,
                    "section": section_label,
                    "status": result.status,
                    "source": result.source,
                    "match_score": result.match_score,
                    "warning": result.warning,
                }
            )
            if len(flagged) >= _FLAGGED_CITATION_CAP:
                break

        recommendation: HuntRecommendation = (
            "block_export" if (fabricated + not_found) > 0 else "ok"
        )
        total = len(results)
        report = HuntReport(
            total_citations=total,
            verified=verified,
            partial_match=partial,
            fabricated=fabricated,
            not_found=not_found,
            errors=errors,
            verification_rate=verified / max(1, total),
            flagged_citations=flagged,
            recommendation=recommendation,
        )

        return _agent_output(report, started=started, input=input)

    async def stream(self, input: AgentInput) -> AsyncIterator[str]:
        result = await self.run(input)
        # Hunter is structured-JSON; one chunk is enough.
        yield result.output and str(result.output) or ""

    @staticmethod
    def _previous_section(input: AgentInput, agent_id: str, key: str) -> str:
        prev = input.previous_outputs.get(agent_id)
        if isinstance(prev, dict):
            output = prev.get("output")
            if isinstance(output, dict):
                return str(output.get(key) or "")
        return ""


def _agent_output(
    report: HuntReport,
    *,
    started: float,
    input: AgentInput,
    no_citations: bool = False,
) -> AgentOutput:
    return AgentOutput(
        agent_id="hallucination_hunter",
        status="completed",
        output=report.model_dump(),
        duration_ms=int((time.monotonic() - started) * 1000),
        metadata={
            "programme_id": input.programme_id,
            "no_citations": no_citations,
        },
    )


def hunt_report_from_results(
    citations: list[Citation],
    results: list[VerificationResult],
    *,
    section_for_index: list[str] | None = None,
) -> HuntReport:
    """Standalone helper used by the batch Celery task — mirrors the
    aggregation in :meth:`HallucinationHunter.run` so the orchestrator
    can build a report without instantiating the agent.
    """

    if section_for_index is None:
        section_for_index = ["unknown"] * len(citations)
    if len(citations) != len(results):
        raise ValueError("citations / results length mismatch")

    verified = sum(1 for r in results if r.status == "verified")
    partial = sum(1 for r in results if r.status == "partial_match")
    fabricated = sum(1 for r in results if r.status == "fabricated")
    not_found = sum(1 for r in results if r.status == "not_found")
    errors = sum(1 for r in results if r.status == "error")
    flagged: list[dict[str, Any]] = []
    for citation, result, section_label in zip(citations, results, section_for_index, strict=True):
        if result.status == "verified":
            continue
        flagged.append(
            {
                "raw_text": citation.raw_text,
                "section": section_label,
                "status": result.status,
                "source": result.source,
                "match_score": result.match_score,
                "warning": result.warning,
            }
        )
        if len(flagged) >= _FLAGGED_CITATION_CAP:
            break

    total = len(results)
    return HuntReport(
        total_citations=total,
        verified=verified,
        partial_match=partial,
        fabricated=fabricated,
        not_found=not_found,
        errors=errors,
        verification_rate=verified / max(1, total),
        flagged_citations=flagged,
        recommendation="block_export" if (fabricated + not_found) > 0 else "ok",
    )


__all__ = ["HallucinationHunter", "HuntRecommendation", "HuntReport", "hunt_report_from_results"]
