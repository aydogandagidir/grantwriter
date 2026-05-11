"""Compliance Reviewer — formal-rule + LLM depth check before export.

Per docs/06-agent-architecture.md §4.5. The agent runs after the writers
and in parallel with Distinctiveness; it surfaces formal-rule violations
the writers may have missed and (for Horizon Europe) auto-generates the
AI Disclosure required on Standard Application Form page 32.

Four layers of checking:

1. **Programme rule layer** — delegates to
   :meth:`BaseProgramModule.validate_draft`. Page limits, required
   subsections, programme-specific rules (TÜBİTAK B2 length, HE consortium
   size, etc.). Already implemented per programme.
2. **Programme-agnostic key-term coverage** — every term the Call
   Analyst extracted as ``key_terms_to_use`` should appear in the draft.
   Each missing term is one ``missing_key_term`` warning.
3. **HE-only DNSH rule layer** — deterministic substring + word-count
   gate over the impact section. Issue codes ``dnsh_too_short``,
   ``dnsh_objective_missing``, and ``dnsh_phrase_missing`` stack with
   the LLM ``dnsh_inadequate`` verdict (different namespaces — they
   don't shadow each other).
4. **HE-only LLM depth check** — Sonnet 4.6 reviews DNSH, gender
   dimension, and Open Science / DMP for substantive treatment. Mentioning
   "DNSH" once still passes the substring rule but the LLM may flag it as
   shallow — distinct issue codes (``dnsh_inadequate`` vs ``missing_dnsh``)
   so the two layers stack informatively.

Failure modes are deliberately graceful: an LLM JSON parse error degrades
to rule-based-only with a single ``compliance_llm_unavailable`` info issue
attached, rather than failing the whole agent.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agents.base import AgentInput, AgentOutput, AgentStatus, BaseAgent
from src.agents.prompts import load_prompt
from src.compliance.ai_disclosure import generate_ai_disclosure
from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter
from src.programs import get_module
from src.programs.base import BaseProgramModule, CallMetadata, Severity, ValidationIssue

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


_LLM_SEVERITY = Literal["ok", "info", "warning", "blocker"]


class _LLMCheckVerdict(BaseModel):
    """One of the three LLM-judged checks (DNSH / gender / open science)."""

    model_config = ConfigDict(extra="ignore")

    present: bool
    severity: _LLM_SEVERITY
    explanation: str = ""


class _LLMCheckPayload(BaseModel):
    """Validated structure of the JSON returned by the LLM prompt."""

    model_config = ConfigDict(extra="ignore")

    dnsh: _LLMCheckVerdict
    gender_dimension: _LLMCheckVerdict
    open_science: _LLMCheckVerdict


class ComplianceReport(BaseModel):
    """Top-level Compliance Reviewer output.

    ``passed`` is the export gate (zero blockers). ``compliance_score``
    is a heuristic for sorting / charts, NOT the gate — don't chain
    business logic on it.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    ai_disclosure_text: str | None = None
    compliance_score: float = 1.0


class ComplianceReviewer(BaseAgent):
    agent_id = "compliance_reviewer"
    name = "Compliance Reviewer"
    description = (
        "Runs formal-rule checks against the draft and (for HE) generates "
        "the AI disclosure from per-sentence provenance."
    )
    version = "v1"
    requires_rag = False
    estimated_duration_seconds = 10

    def __init__(
        self,
        router: LLMRouter,
        conn: asyncpg.Connection | None = None,
    ) -> None:
        self._router = router
        self._conn = conn

    # ── BaseAgent contract ─────────────────────────────────────────────

    async def run(self, input: AgentInput) -> AgentOutput:
        started = time.perf_counter()

        try:
            module = get_module(input.programme_id)
        except KeyError as exc:
            return self._skip_output(
                started, str(exc), reason_code="unknown_programme"
            )

        draft = self._assemble_draft(input)
        metadata = self._call_metadata(input)

        # 1. Programme rule layer.
        issues: list[ValidationIssue] = list(module.validate_draft(draft, metadata))

        # 2. Key-term coverage (every programme).
        issues.extend(_check_key_terms_coverage(draft, metadata.key_terms_to_use))

        # 3. HE-only DNSH rule layer (deterministic, stacks with LLM check).
        if _is_horizon_eu(input.programme_id):
            issues.extend(_check_dnsh_rules(draft))

        # 4. HE-only LLM depth checks.
        cost_usd = 0.0
        tokens_used: dict[str, int] = {}
        llm_check_skipped: str | None = None
        if _is_horizon_eu(input.programme_id):
            llm_issues, cost_usd, tokens_used = await self._llm_depth_checks(
                input, draft
            )
            issues.extend(llm_issues)
        else:
            llm_check_skipped = "non_he_programme"

        # 4. AI disclosure (gated on conn + provenance rows).
        ai_disclosure_text = await self._safe_generate_disclosure(input)

        # 5. Aggregate report.
        blockers = sum(1 for i in issues if i.severity == "blocker")
        warnings = sum(1 for i in issues if i.severity == "warning")
        score = max(0.0, 1.0 - 0.10 * blockers - 0.05 * warnings)
        report = ComplianceReport(
            passed=blockers == 0,
            issues=issues,
            ai_disclosure_text=ai_disclosure_text,
            compliance_score=score,
        )

        duration_ms = int((time.perf_counter() - started) * 1000)
        metadata_out: dict[str, Any] = {
            "programme_id": input.programme_id,
            "blockers": blockers,
            "warnings": warnings,
        }
        if llm_check_skipped is not None:
            metadata_out["llm_check_skipped"] = llm_check_skipped

        return AgentOutput(
            agent_id=self.agent_id,
            status="completed",
            output=report.model_dump(mode="json"),
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            tokens_used=tokens_used,
            metadata=metadata_out,
        )

    async def stream(self, input: AgentInput) -> AsyncIterator[str]:
        # Compliance Reviewer is a structured-JSON agent — one chunk is
        # enough; partial JSON would be useless to the consumer.
        result = await self.run(input)
        yield json.dumps(result.model_dump(mode="json"))

    # ── Internals ──────────────────────────────────────────────────────

    @staticmethod
    def _assemble_draft(input: AgentInput) -> dict[str, Any]:
        """Build the draft dict ``module.validate_draft`` consumes.

        Pulls the writer outputs from ``previous_outputs`` and exposes
        ``brief`` separately so HE's ``insufficient_consortium`` rule
        (which reads ``draft.brief.partners``) and TÜBİTAK's duration
        rule still fire under the agent harness.
        """

        draft: dict[str, Any] = {"brief": dict(input.brief or {})}
        for agent_id, key in (
            ("excellence_writer", "excellence_md"),
            ("impact_writer", "impact_md"),
            ("implementation_writer", "implementation_md"),
        ):
            prev = input.previous_outputs.get(agent_id)
            if isinstance(prev, dict):
                output = prev.get("output")
                if isinstance(output, dict):
                    draft[key] = output.get(key) or ""
                    continue
            draft[key] = ""
        return draft

    @staticmethod
    def _call_metadata(input: AgentInput) -> CallMetadata:
        """Reconstruct the Call Analyst metadata for ``validate_draft``.

        Falls back to an empty :class:`CallMetadata` if Call Analyst
        didn't run — callers like fixtures still want a stable shape.
        """

        prev = input.previous_outputs.get("call_analyst")
        payload: dict[str, Any] = {}
        if isinstance(prev, dict):
            output = prev.get("output")
            if isinstance(output, dict):
                payload = dict(output)

        # CallAnalystOutput uses ``language_required`` while CallMetadata
        # uses ``language``; map across the seam.
        if "language" not in payload and "language_required" in payload:
            payload["language"] = payload.get("language_required")

        try:
            return CallMetadata.model_validate(payload)
        except ValidationError:
            logger.warning("compliance_reviewer_call_metadata_invalid")
            return CallMetadata()

    async def _llm_depth_checks(
        self, input: AgentInput, draft: dict[str, Any]
    ) -> tuple[list[ValidationIssue], float, dict[str, int]]:
        """Run the Sonnet depth checks. Degrade to rule-only on any error."""

        try:
            template = load_prompt(
                programme="_shared", agent="compliance_reviewer", version=self.version
            )
            system = template.format(
                excellence_md=str(draft.get("excellence_md") or ""),
                impact_md=str(draft.get("impact_md") or ""),
                implementation_md=str(draft.get("implementation_md") or ""),
            )
        except (FileNotFoundError, KeyError) as exc:
            logger.exception("compliance_reviewer_prompt_load_failed")
            return [_llm_unavailable_issue(f"prompt load failed: {exc}")], 0.0, {}

        request = LLMRequest(
            task="compliance_reviewer",
            tenant_id=input.tenant_id,
            proposal_id=input.proposal_id,
            system=system,
            messages=[
                LLMMessage(
                    role="user",
                    content="Evaluate the three checks and return only the JSON object.",
                )
            ],
            cache_system=True,
            temperature=0.0,
        )

        try:
            response = await self._router.complete(request)
        except Exception as exc:
            logger.warning("compliance_reviewer_llm_failed", extra={"error": str(exc)})
            return [_llm_unavailable_issue(f"llm call failed: {exc}")], 0.0, {}

        try:
            payload_dict = json.loads(_strip_code_fence(response.text))
        except json.JSONDecodeError as exc:
            logger.warning(
                "compliance_reviewer_invalid_json",
                extra={"text_preview": response.text[:200], "error": str(exc)},
            )
            return (
                [_llm_unavailable_issue(f"LLM returned invalid JSON: {exc}")],
                response.cost_usd,
                _tokens(response),
            )

        try:
            payload = _LLMCheckPayload.model_validate(payload_dict)
        except ValidationError as exc:
            logger.warning(
                "compliance_reviewer_schema_violation",
                extra={"errors": exc.errors()[:3]},
            )
            return (
                [_llm_unavailable_issue(f"LLM JSON failed schema: {exc}")],
                response.cost_usd,
                _tokens(response),
            )

        return (
            list(_verdicts_to_issues(payload)),
            response.cost_usd,
            _tokens(response),
        )

    async def _safe_generate_disclosure(self, input: AgentInput) -> str | None:
        """Generate the AI disclosure if a DB connection is available.

        Failures are logged and swallowed — disclosure absence is never
        a blocker for the agent itself; the orchestrator decides whether
        to re-run later (e.g., when provenance lands).
        """

        if self._conn is None:
            return None
        try:
            return await generate_ai_disclosure(input.proposal_id, self._conn)
        except Exception:
            logger.exception(
                "compliance_reviewer_disclosure_failed",
                extra={"proposal_id": str(input.proposal_id)},
            )
            return None

    def _skip_output(
        self, started: float, message: str, *, reason_code: str
    ) -> AgentOutput:
        """Mirror the distinctiveness scorer skip pattern.

        AgentStatus only allows completed / failed / skipped; unknown
        programme is not a hard failure (the orchestrator may move on
        without compliance for an experimental programme), so it maps
        to skipped with a discriminating reason_code.
        """

        duration_ms = int((time.perf_counter() - started) * 1000)
        empty = ComplianceReport(passed=True, compliance_score=1.0)
        status: AgentStatus = "skipped"
        return AgentOutput(
            agent_id=self.agent_id,
            status=status,
            output=empty.model_dump(mode="json"),
            duration_ms=duration_ms,
            metadata={"reason": message, "reason_code": reason_code},
        )


# ── Helpers ────────────────────────────────────────────────────────────


def _is_horizon_eu(programme_id: str) -> bool:
    """HE programmes share the AI Disclosure + DNSH / gender / OS rules.

    Prefix match so future ``horizon_eu_ia``, ``horizon_eu_eic`` etc. opt
    in automatically without touching the agent.
    """

    return programme_id.startswith("horizon_eu")


# ── HE DNSH rule layer ────────────────────────────────────────────────


# The six environmental objectives from the EU Taxonomy DNSH article;
# every HE proposal's impact section must address each of them at least
# in passing. Lower-cased so substring matches stay permissive.
_DNSH_OBJECTIVES: tuple[str, ...] = (
    "climate change mitigation",
    "climate change adaptation",
    "water and marine resources",
    "circular economy",
    "pollution prevention",
    "biodiversity",
)
# Either spelling is fine — the canonical phrase shows up both ways
# across the HE templates we're targeting.
_DNSH_PHRASES: tuple[str, ...] = ("no significant harm", "do no significant harm")
_DNSH_MIN_WORDS = 200
"""HE templates expect a substantive DNSH treatment. Anything below 200
words in the impact section is almost certainly token DNSH coverage —
useful as a heuristic gate even before the LLM weighs in."""


def _check_dnsh_rules(draft: dict[str, Any]) -> list[ValidationIssue]:
    """Deterministic HE DNSH gate over the impact section.

    Stacks with :func:`_verdicts_to_issues`'s LLM-judged
    ``dnsh_inadequate``: the rule layer guarantees the basics
    (length + topic coverage + canonical phrase), the LLM judges depth.
    The two namespaces don't collide.

    Issue codes:
    - ``dnsh_too_short`` (warning) — impact_md word count below the
      threshold.
    - ``dnsh_objective_missing`` (warning) — one of the six EU
      Taxonomy environmental objectives is not mentioned at all.
    - ``dnsh_phrase_missing`` (info) — the "no significant harm"
      phrase is absent.
    """

    impact_md = str(draft.get("impact_md") or "")
    lower = impact_md.lower()
    issues: list[ValidationIssue] = []

    word_count = len(impact_md.split())
    if word_count < _DNSH_MIN_WORDS:
        issues.append(
            ValidationIssue(
                severity="warning",
                section="impact",
                code="dnsh_too_short",
                message_tr=(
                    f"DNSH bölümü çok kısa ({word_count} kelime, "
                    f"minimum {_DNSH_MIN_WORDS})."
                ),
                message_en=(
                    f"DNSH section is too short ({word_count} words, "
                    f"minimum {_DNSH_MIN_WORDS})."
                ),
            )
        )

    for objective in _DNSH_OBJECTIVES:
        if objective not in lower:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    section="impact",
                    code="dnsh_objective_missing",
                    message_tr=(
                        f"DNSH çevresel hedefi '{objective}' adres edilmemiş."
                    ),
                    message_en=(
                        f"DNSH environmental objective '{objective}' "
                        "not addressed."
                    ),
                )
            )

    if not any(p in lower for p in _DNSH_PHRASES):
        issues.append(
            ValidationIssue(
                severity="info",
                section="impact",
                code="dnsh_phrase_missing",
                message_tr=(
                    "'Do no significant harm' anahtar ifadesi geçmiyor."
                ),
                message_en=(
                    "'Do no significant harm' phrase not found."
                ),
            )
        )

    return issues


def _check_key_terms_coverage(
    draft: dict[str, Any], key_terms: list[str]
) -> list[ValidationIssue]:
    """Emit one warning per ``key_terms_to_use`` term missing from the draft.

    Case-insensitive substring match across the three section bodies. The
    Call Analyst surfaces these terms as the writer's "must-weave-in" list
    (per docs/06 §4.1 prompt); a missed term is a warning, not a blocker —
    it's a soft signal the proposal is drifting from the call.
    """

    if not key_terms:
        return []

    combined = (
        str(draft.get("excellence_md") or "")
        + "\n"
        + str(draft.get("impact_md") or "")
        + "\n"
        + str(draft.get("implementation_md") or "")
    ).lower()

    issues: list[ValidationIssue] = []
    for term in key_terms:
        term_str = str(term).strip()
        if not term_str:
            continue
        if term_str.lower() not in combined:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    section=None,
                    code="missing_key_term",
                    message_tr=(
                        f"Çağrı anahtar terimi '{term_str}' taslakta geçmiyor; "
                        "yazıcılar çağrıyla hizalı kalsın."
                    ),
                    message_en=(
                        f"Call key term '{term_str}' not found in draft; "
                        "writers should weave it in to stay aligned with the call."
                    ),
                )
            )
    return issues


def _verdicts_to_issues(payload: _LLMCheckPayload) -> list[ValidationIssue]:
    """Convert the three LLM verdicts into ValidationIssue rows.

    ``severity == "ok"`` rows are dropped (no issue to surface). The
    remaining rows map to programme-distinct issue codes so they don't
    collide with the substring-rule codes in
    :class:`HorizonEURIAModule.validate_draft`.
    """

    mapping: list[tuple[str, _LLMCheckVerdict, str, str, str, str]] = [
        (
            "dnsh_inadequate",
            payload.dnsh,
            "impact",
            "DNSH değerlendirmesi yetersiz",
            "DNSH assessment is shallow or incomplete",
            "DNSH",
        ),
        (
            "gender_inadequate",
            payload.gender_dimension,
            "excellence",
            "Toplumsal cinsiyet boyutu yetersiz",
            "Gender dimension not substantively integrated",
            "gender dimension",
        ),
        (
            "open_science_inadequate",
            payload.open_science,
            "excellence",
            "Açık Bilim / DMP yetersiz",
            "Open Science / DMP commitments are shallow",
            "Open Science",
        ),
    ]
    out: list[ValidationIssue] = []
    for code, verdict, section, msg_tr, msg_en, label in mapping:
        if verdict.severity == "ok":
            continue
        suggestion = verdict.explanation or None
        # The LLM's severity vocabulary aligns 1:1 with ValidationIssue's
        # except for "ok" (filtered above) — so this is just narrowing.
        severity_value: Severity = verdict.severity
        out.append(
            ValidationIssue(
                severity=severity_value,
                section=section,
                code=code,
                message_tr=msg_tr,
                message_en=f"{msg_en} ({label}).",
                suggestion=suggestion,
            )
        )
    return out


def _llm_unavailable_issue(reason: str) -> ValidationIssue:
    """Single info issue surfaced when the LLM check has to be skipped."""

    return ValidationIssue(
        severity="info",
        section=None,
        code="compliance_llm_unavailable",
        message_tr=f"LLM uyumluluk kontrolü atlandı: {reason}",
        message_en=f"LLM compliance check skipped: {reason}",
    )


def _strip_code_fence(text: str) -> str:
    """Remove a leading / trailing Markdown code fence if present.

    Mirrors :func:`src.agents.call_analyst._strip_code_fence`. Inlined
    rather than shared to avoid creating a util module on the strength
    of two callers; revisit if a third agent needs the same helper.
    """

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


# Re-export for the test suite.
_ = BaseProgramModule


__all__ = ["ComplianceReport", "ComplianceReviewer"]
