"""Hallucination Hunter — final-pass citation + claim verification agent.

Runs after the writer agents have produced their Markdown. Two layers:

1. **Citation verification** (ships in S2.D7.T1): every citation marker
   is resolved via :class:`~src.citations.CitationVerifier` against
   Crossref / OpenAlex. Fabricated / not-found markers flip the export
   to blocked.

2. **Claim verification** (S3.D13, optional): when an :class:`LLMRouter`
   is provided, a sample of up to 10 verified citations is run through
   a Sonnet check that asks "does the cited source plausibly support
   the claim sentence?". The pass-rate lands in
   :class:`HuntReport.claim_check_pass_rate`; a rate < 0.6 also flips
   the export to blocked.

Backward compat: ``HallucinationHunter(verifier)`` (no router) keeps
the S2.D7 behaviour exactly. The orchestrator's
:func:`build_default_agents` passes the router; tests that don't care
about claims pass ``router=None``.

Cost guard: max 10 LLM calls per run (one per sampled claim) — see
:data:`_CLAIM_SAMPLE_CAP`. At Sonnet 4.6 prices that's ~\\$0.01 per
draft, well below the per-citation budget.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.agents.prompts import load_prompt
from src.citations import Citation, CitationVerifier, VerificationResult, extract_citations
from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


HuntRecommendation = Literal["block_export", "ok"]
ClaimVerdict = Literal["supports", "contradicts", "unrelated"]
_FLAGGED_CITATION_CAP = 50
"""Cap how many flagged citations the report carries inline. The
underlying ``citations`` table is the source of truth; the report is
for at-a-glance UI use and bigger lists overwhelm both."""

_CLAIM_SAMPLE_CAP = 10
"""Max number of verified citations the claim-check stage samples.
Caps cost (~\\$0.001 per call × 10 = ~\\$0.01 per draft) and latency
(claims run sequentially today)."""

_CLAIM_PASS_RATE_BLOCK_THRESHOLD = 0.6
"""Below this ratio of supports / total_sampled, the recommendation
flips to ``block_export`` even when no citations were fabricated. The
threshold matches docs/06 §4.6's "supportive 60% of the time" guidance."""

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
    """Sample-based claim verification (S3.D13). ``None`` means the
    stage was skipped (no router, no verified citations, or LLM error)."""


class _ClaimSample(BaseModel):
    """One verified citation paired with the sentence it appears in."""

    model_config = ConfigDict(frozen=True)

    section: str
    citation_marker: str
    claim_sentence: str
    source_title: str
    source_authors: list[str]
    source_year: str


class _LLMClaimPayload(BaseModel):
    """Validated structure of the JSON returned by the claim-check prompt."""

    model_config = ConfigDict(extra="ignore")

    verdict: ClaimVerdict
    reason: str = ""


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

    def __init__(
        self,
        verifier: CitationVerifier,
        router: LLMRouter | None = None,
    ) -> None:
        """``router`` is optional for backward compat.

        When ``None``, the agent runs only the citation-verify layer
        (S2.D7 behaviour). When provided, a small LLM-judged claim
        sample also runs and ``claim_check_pass_rate`` is populated.
        """

        self._verifier = verifier
        self._router = router

    async def run(self, input: AgentInput) -> AgentOutput:
        started = time.monotonic()

        # 1. Pull every section's Markdown out of previous_outputs and
        #    extract citations per section so the flagged list stays
        #    section-attributed for the UI.
        per_section: list[tuple[str, str, list[Citation]]] = []
        for agent_id, key in _SECTION_KEYS:
            section_md = self._previous_section(input, agent_id, key)
            if not section_md:
                continue
            section_label = key.replace("_md", "")
            per_section.append(
                (section_label, section_md, extract_citations(section_md))
            )

        all_citations: list[Citation] = []
        section_for_index: list[str] = []
        section_md_for_index: list[str] = []
        for section_label, section_md, section_citations in per_section:
            for citation in section_citations:
                all_citations.append(citation)
                section_for_index.append(section_label)
                section_md_for_index.append(section_md)

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

        # 4. Optional claim-check stage.
        claim_pass_rate, llm_cost, llm_tokens = await self._maybe_run_claim_check(
            all_citations=all_citations,
            results=results,
            section_for_index=section_for_index,
            section_md_for_index=section_md_for_index,
            input=input,
        )

        recommendation = _decide_recommendation(
            fabricated=fabricated,
            not_found=not_found,
            claim_check_pass_rate=claim_pass_rate,
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
            claim_check_pass_rate=claim_pass_rate,
        )

        return _agent_output(
            report,
            started=started,
            input=input,
            cost_usd=llm_cost,
            tokens_used=llm_tokens,
        )

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

    # ── Claim-check stage ─────────────────────────────────────────────

    async def _maybe_run_claim_check(
        self,
        *,
        all_citations: list[Citation],
        results: list[VerificationResult],
        section_for_index: list[str],
        section_md_for_index: list[str],
        input: AgentInput,
    ) -> tuple[float | None, float, dict[str, int]]:
        """Run claim-verify if a router is wired and there are verified hits.

        Returns ``(claim_pass_rate, total_cost_usd, tokens_used_aggregate)``.
        ``claim_pass_rate`` is ``None`` when the stage was skipped or the
        LLM raised — graceful degradation, never blocks the agent.
        """

        if self._router is None:
            return None, 0.0, {}

        samples = _sample_claims(
            all_citations=all_citations,
            results=results,
            section_for_index=section_for_index,
            section_md_for_index=section_md_for_index,
            cap=_CLAIM_SAMPLE_CAP,
        )
        if not samples:
            return None, 0.0, {}

        try:
            template = load_prompt(
                programme="_shared",
                agent="hallucination_hunter",
                version=self.version,
            )
        except (FileNotFoundError, KeyError) as exc:
            logger.warning(
                "hallucination_hunter_prompt_load_failed",
                extra={"error": str(exc)},
            )
            return None, 0.0, {}

        supports = 0
        cost_total = 0.0
        tokens_total: dict[str, int] = {"input": 0, "output": 0, "cached": 0}

        for sample in samples:
            verdict, cost, tokens = await self._verify_one_claim(
                sample=sample, template=template, input=input
            )
            cost_total += cost
            for k, v in tokens.items():
                tokens_total[k] = tokens_total.get(k, 0) + v
            if verdict == "supports":
                supports += 1

        pass_rate = supports / len(samples)
        logger.info(
            "hallucination_hunter_claim_check",
            extra={
                "samples": len(samples),
                "supports": supports,
                "pass_rate": round(pass_rate, 3),
                "cost_usd": round(cost_total, 4),
            },
        )
        return pass_rate, cost_total, tokens_total

    async def _verify_one_claim(
        self,
        *,
        sample: _ClaimSample,
        template: str,
        input: AgentInput,
    ) -> tuple[ClaimVerdict | None, float, dict[str, int]]:
        """Issue one LLM call. Errors degrade to ``unrelated``-equivalent
        (counted as not-supports) so a flaky LLM doesn't pretend the
        rest of the sample passed.
        """

        prompt = template.format(
            claim_text=sample.claim_sentence,
            source_title=sample.source_title or "(no title resolved)",
            source_authors=", ".join(sample.source_authors)
            if sample.source_authors
            else "(no authors)",
            source_year=sample.source_year or "(unknown)",
        )
        request = LLMRequest(
            # Reuse the agent's parent task literal — the claim check is
            # a sub-stage of the hunter, not a distinct cost-tracked task.
            task="hallucination_hunter",
            tenant_id=input.tenant_id,
            proposal_id=input.proposal_id,
            system=prompt,
            messages=[
                LLMMessage(
                    role="user",
                    content="Return only the JSON object.",
                )
            ],
            cache_system=False,  # different per claim, caching is moot
            temperature=0.0,
        )

        assert self._router is not None  # caller gates on this

        try:
            response = await self._router.complete(request)
        except Exception as exc:
            logger.warning(
                "hallucination_hunter_claim_call_failed",
                extra={"error": str(exc)},
            )
            return None, 0.0, {}

        try:
            payload_dict = json.loads(_strip_code_fence(response.text))
        except json.JSONDecodeError:
            logger.warning(
                "hallucination_hunter_claim_invalid_json",
                extra={"text_preview": response.text[:120]},
            )
            return None, response.cost_usd, _tokens(response)

        try:
            payload = _LLMClaimPayload.model_validate(payload_dict)
        except ValidationError:
            logger.warning("hallucination_hunter_claim_schema_violation")
            return None, response.cost_usd, _tokens(response)

        return payload.verdict, response.cost_usd, _tokens(response)


# ── Module-level helpers ──────────────────────────────────────────────


def _agent_output(
    report: HuntReport,
    *,
    started: float,
    input: AgentInput,
    no_citations: bool = False,
    cost_usd: float = 0.0,
    tokens_used: dict[str, int] | None = None,
) -> AgentOutput:
    return AgentOutput(
        agent_id="hallucination_hunter",
        status="completed",
        output=report.model_dump(),
        duration_ms=int((time.monotonic() - started) * 1000),
        cost_usd=cost_usd,
        tokens_used=tokens_used or {},
        metadata={
            "programme_id": input.programme_id,
            "no_citations": no_citations,
        },
    )


def _decide_recommendation(
    *,
    fabricated: int,
    not_found: int,
    claim_check_pass_rate: float | None,
) -> HuntRecommendation:
    """Map the citation tally + claim pass-rate to the export gate.

    Either signal can flip to ``block_export``; both must clear for
    ``ok``. The claim threshold trips only when a rate is actually
    available — ``None`` means the stage was skipped, which is treated
    as a non-vote.
    """

    if (fabricated + not_found) > 0:
        return "block_export"
    if (
        claim_check_pass_rate is not None
        and claim_check_pass_rate < _CLAIM_PASS_RATE_BLOCK_THRESHOLD
    ):
        return "block_export"
    return "ok"


_SENTENCE_BOUNDARY = re.compile(r"[.!?]\s")


def _claim_sentence_for(marker: str, section_md: str) -> str | None:
    """Return the sentence in ``section_md`` containing ``marker``.

    The boundary detector is intentionally permissive — Markdown
    sentences with embedded citations don't always end with a period
    (heading lines, bullets), so we fall back to a 240-char window
    around the marker if no terminator is found.
    """

    idx = section_md.find(marker)
    if idx < 0:
        return None

    # Walk backwards to the last sentence terminator before the marker.
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(section_md[:idx]):
        start = match.end()

    # Walk forward to the next terminator after the marker.
    end_match = _SENTENCE_BOUNDARY.search(section_md[idx:])
    end = (
        idx + end_match.end()
        if end_match is not None
        else min(len(section_md), idx + 240)
    )

    sentence = section_md[start:end].strip()
    return sentence or None


def _sample_claims(
    *,
    all_citations: list[Citation],
    results: list[VerificationResult],
    section_for_index: list[str],
    section_md_for_index: list[str],
    cap: int,
) -> list[_ClaimSample]:
    """Build claim samples for verified citations only, capped at ``cap``.

    A verified citation without a recoverable claim sentence (rare —
    the marker should always appear in its section) is skipped.
    """

    samples: list[_ClaimSample] = []
    for citation, result, section_label, section_md in zip(
        all_citations,
        results,
        section_for_index,
        section_md_for_index,
        strict=True,
    ):
        if len(samples) >= cap:
            break
        if result.status != "verified":
            continue
        sentence = _claim_sentence_for(citation.raw_text, section_md)
        if not sentence:
            continue
        samples.append(
            _ClaimSample(
                section=section_label,
                citation_marker=citation.raw_text,
                claim_sentence=sentence,
                source_title=str(result.metadata.get("title") or ""),
                source_authors=list(citation.authors or []),
                source_year=str(citation.year) if citation.year else "",
            )
        )
    return samples


def _strip_code_fence(text: str) -> str:
    """Drop a leading / trailing Markdown code fence if present."""

    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
    if stripped.endswith("```"):
        stripped = stripped[: -len("```")]
    return stripped.strip()


def _tokens(response: Any) -> dict[str, int]:
    return {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
        "cached": response.usage.cached_tokens,
    }


def hunt_report_from_results(
    citations: list[Citation],
    results: list[VerificationResult],
    *,
    section_for_index: list[str] | None = None,
) -> HuntReport:
    """Standalone helper used by the batch Celery task — mirrors the
    aggregation in :meth:`HallucinationHunter.run` so the orchestrator
    can build a report without instantiating the agent. Claim-check
    is NOT run here (no LLM router available).
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
        recommendation=_decide_recommendation(
            fabricated=fabricated,
            not_found=not_found,
            claim_check_pass_rate=None,
        ),
    )


__all__ = ["HallucinationHunter", "HuntRecommendation", "HuntReport", "hunt_report_from_results"]
