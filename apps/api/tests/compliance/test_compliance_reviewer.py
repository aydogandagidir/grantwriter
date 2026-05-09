"""Compliance Reviewer agent tests.

Pattern mirrors :mod:`tests.agents.test_call_analyst` (FakeProvider for
LLM) and :mod:`tests.agents.test_hallucination_hunter` (assembling
``previous_outputs`` for writer-driven agents). The agent is exercised
end-to-end without a database — the AI disclosure path is covered in
:mod:`tests.compliance.test_ai_disclosure`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from src.agents.base import AgentInput
from src.agents.compliance_reviewer import ComplianceReport, ComplianceReviewer
from src.programs.base import ValidationIssue

from tests.llm.conftest import FakeProvider, build_router, make_response

# ── Fixtures ────────────────────────────────────────────────────────────


def _full_he_draft_outputs(
    *,
    excellence_md: str | None = None,
    impact_md: str | None = None,
    implementation_md: str | None = None,
) -> dict[str, Any]:
    """Build the writer ``previous_outputs`` dict for a complete HE draft.

    Defaults match the clean fixture in
    ``tests/programs/test_horizon_eu_ria.py`` so we know it passes the
    rule layer; tests override individual sections to inject violations.
    """

    excellence = excellence_md or (
        "## 1.1 Objectives and ambition\n\nA concrete objective for digital twin systems.\n\n"
        "## 1.2 Methodology\n\nGender dimension is integrated.\n\n"
        "## 1.3 State of the art\n\nReferences here.\n\n"
        "## 1.4 Open science practices\n\nFAIR + DMP.\n"
    )
    impact = impact_md or (
        "## 2.1 Project's pathways towards impact\n\nKIPs detailed.\n\n"
        "## 2.2 Measures to maximise impact\n\nDNSH considered against the six environmental objectives.\n\n"
        "## 2.3 Summary canvas (key impact pathways)\n\nCanvas table.\n"
    )
    implementation = implementation_md or (
        "## 3.1 Work plan and resources\n\nWPs.\n\n"
        "## 3.2 Capacity of the participants\n\nTeam.\n\n"
        "## 3.3 Consortium as a whole\n\nGovernance.\n"
    )
    return {
        "excellence_writer": {
            "agent_id": "excellence_writer",
            "status": "completed",
            "output": {"excellence_md": excellence},
        },
        "impact_writer": {
            "agent_id": "impact_writer",
            "status": "completed",
            "output": {"impact_md": impact},
        },
        "implementation_writer": {
            "agent_id": "implementation_writer",
            "status": "completed",
            "output": {"implementation_md": implementation},
        },
    }


def _call_analyst_output(*, key_terms: list[str] | None = None) -> dict[str, Any]:
    """Stand-in Call Analyst output for ``previous_outputs["call_analyst"]``."""

    return {
        "agent_id": "call_analyst",
        "status": "completed",
        "output": {
            "eligibility": {
                "eligible_countries": ["EU MS"],
                "eligible_entities": ["legal entity"],
                "min_partners": 3,
                "min_countries": 3,
                "trl_range": [4, 6],
            },
            "scope_summary": "Digital twin platforms",
            "expected_outcomes": [],
            "expected_impacts": [],
            "evaluation_criteria": [],
            "page_limit": 45,
            "language_required": "en",
            "user_eligible": True,
            "user_eligibility_issues": [],
            "key_terms_to_use": key_terms or [],
            "deadlines": {"submission": "2026-09-15"},
        },
    }


_ALL_OK_LLM_JSON = json.dumps(
    {
        "dnsh": {"present": True, "severity": "ok", "explanation": "Full DNSH covered."},
        "gender_dimension": {
            "present": True,
            "severity": "ok",
            "explanation": "Integrated into methodology.",
        },
        "open_science": {
            "present": True,
            "severity": "ok",
            "explanation": "DMP and FAIR addressed.",
        },
    }
)


def _agent(
    *,
    canned_text: str = _ALL_OK_LLM_JSON,
    primary_script: list[Any] | None = None,
) -> tuple[ComplianceReviewer, FakeProvider]:
    """Build a ComplianceReviewer wired to a FakeProvider primary.

    By default the primary returns a single all-`ok` LLM response. Tests
    that don't expect any LLM call (TÜBİTAK path) override
    ``primary_script=[]`` to fail loudly if the agent calls the router.
    """

    if primary_script is None:
        primary_script = [
            make_response(
                text=canned_text,
                model="claude-sonnet-4-6",
                provider="claude",
                input_tokens=2000,
                output_tokens=200,
                cached_tokens=0,
                cost_usd=0.012,
            )
        ]
    primary = FakeProvider("claude", primary_script)
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})
    return ComplianceReviewer(router=router), primary


def _input(
    *,
    programme_id: str = "horizon_eu_ria",
    previous_outputs: dict[str, Any] | None = None,
    brief: dict[str, Any] | None = None,
    language: str = "en",
) -> AgentInput:
    return AgentInput(
        proposal_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        programme_id=programme_id,
        language=language,  # type: ignore[arg-type]
        brief=brief
        or {"partners": "ACME (NL, SME)\nBeta (DE, RTO)\nGamma (TR, SME)\n"},
        call={"call_text": ""},
        previous_outputs=previous_outputs or {},
    )


def _codes(report: ComplianceReport) -> list[str]:
    return [issue.code for issue in report.issues]


def _has(report: ComplianceReport, code: str) -> bool:
    return any(issue.code == code for issue in report.issues)


# ── Clean draft ─────────────────────────────────────────────────────────


async def test_clean_he_draft_returns_passed() -> None:
    agent, primary = _agent()
    previous = _full_he_draft_outputs()
    previous["call_analyst"] = _call_analyst_output(
        key_terms=["digital twin", "FAIR"]
    )

    output = await agent.run(_input(previous_outputs=previous))

    assert output.status == "completed"
    report = ComplianceReport.model_validate(output.output)
    assert report.passed is True
    assert report.issues == []
    assert report.compliance_score == 1.0
    assert report.ai_disclosure_text is None  # no conn provided
    assert output.cost_usd == 0.012
    assert output.tokens_used == {"input": 2000, "output": 200, "cached": 0}
    assert len(primary.calls) == 1
    sent_request, model, _ = primary.calls[0]
    assert model == "claude-sonnet-4-6"
    assert sent_request.cache_system is True


# ── Rule-layer violations ───────────────────────────────────────────────


async def test_he_draft_with_missing_subsection_blocks() -> None:
    agent, _ = _agent()
    previous = _full_he_draft_outputs(
        excellence_md=(
            "## 1.1 Objectives\n\nA.\n\n"
            "## 1.3 State of the art\n\nB.\n\n"  # 1.2 missing
            "## 1.4 Open science\n\nC.\n"
        )
    )
    previous["call_analyst"] = _call_analyst_output()

    output = await agent.run(_input(previous_outputs=previous))

    report = ComplianceReport.model_validate(output.output)
    assert _has(report, "missing_subsection")
    assert report.passed is False  # blocker present


async def test_he_draft_over_page_limit_blocks() -> None:
    agent, _ = _agent()
    long = " ".join(["word"] * 30_000)  # ~60 pages at 500 words/page
    previous = _full_he_draft_outputs(
        excellence_md=(
            f"## 1.1 Objectives\n\n{long}\n\n"
            "## 1.2 Methodology\n\n.\n\n"
            "## 1.3 State of the art\n\n.\n\n"
            "## 1.4 Open science\n\n.\n"
        )
    )
    previous["call_analyst"] = _call_analyst_output()

    output = await agent.run(_input(previous_outputs=previous))

    report = ComplianceReport.model_validate(output.output)
    assert _has(report, "page_limit_exceeded")
    blockers = [i for i in report.issues if i.severity == "blocker"]
    assert len(blockers) >= 1
    assert report.passed is False


# ── Key-term coverage ───────────────────────────────────────────────────


async def test_missing_key_terms_emit_warnings() -> None:
    agent, _ = _agent()
    previous = _full_he_draft_outputs()  # contains "digital twin"
    previous["call_analyst"] = _call_analyst_output(
        key_terms=["digital twin", "edge AI", "federated learning"]
    )

    output = await agent.run(_input(previous_outputs=previous))

    report = ComplianceReport.model_validate(output.output)
    missing = [i for i in report.issues if i.code == "missing_key_term"]
    assert len(missing) == 2
    en_messages = [i.message_en for i in missing]
    assert any("edge AI" in msg for msg in en_messages)
    assert any("federated learning" in msg for msg in en_messages)
    # Warnings only — passed should still be True if there are no blockers.
    assert report.passed is True


async def test_no_key_terms_means_no_coverage_check() -> None:
    """When the Call Analyst surfaces no key terms, the coverage check
    should be a silent no-op rather than emitting spurious warnings."""

    agent, _ = _agent()
    previous = _full_he_draft_outputs()
    previous["call_analyst"] = _call_analyst_output(key_terms=[])

    output = await agent.run(_input(previous_outputs=previous))

    report = ComplianceReport.model_validate(output.output)
    assert "missing_key_term" not in _codes(report)


# ── LLM depth checks (HE only) ──────────────────────────────────────────


async def test_he_llm_flags_inadequate_dnsh() -> None:
    """Substring rule passes (DNSH mentioned once) but LLM judges depth
    as shallow → ``dnsh_inadequate`` warning. ``missing_dnsh`` should NOT
    be present (substring rule satisfied)."""

    canned = json.dumps(
        {
            "dnsh": {
                "present": True,
                "severity": "warning",
                "explanation": "DNSH mentioned but not assessed against the six environmental objectives.",
            },
            "gender_dimension": {"present": True, "severity": "ok", "explanation": ""},
            "open_science": {"present": True, "severity": "ok", "explanation": ""},
        }
    )
    agent, _ = _agent(canned_text=canned)
    previous = _full_he_draft_outputs()
    previous["call_analyst"] = _call_analyst_output()

    output = await agent.run(_input(previous_outputs=previous))

    report = ComplianceReport.model_validate(output.output)
    assert _has(report, "dnsh_inadequate")
    assert not _has(report, "missing_dnsh")  # substring rule satisfied
    dnsh_issue = next(i for i in report.issues if i.code == "dnsh_inadequate")
    assert dnsh_issue.severity == "warning"
    assert dnsh_issue.section == "impact"
    assert dnsh_issue.suggestion is not None  # explanation populated


async def test_he_llm_blocker_demotes_passed() -> None:
    """LLM-emitted ``blocker`` severity flips ``passed=False`` even when
    rule layer is clean."""

    canned = json.dumps(
        {
            "dnsh": {"present": False, "severity": "blocker", "explanation": "Absent."},
            "gender_dimension": {"present": True, "severity": "ok", "explanation": ""},
            "open_science": {"present": True, "severity": "ok", "explanation": ""},
        }
    )
    agent, _ = _agent(canned_text=canned)
    previous = _full_he_draft_outputs()
    previous["call_analyst"] = _call_analyst_output()

    output = await agent.run(_input(previous_outputs=previous))

    report = ComplianceReport.model_validate(output.output)
    assert _has(report, "dnsh_inadequate")
    assert report.passed is False


async def test_he_llm_invalid_json_falls_back_to_rules() -> None:
    """Garbage from the LLM → emit one info issue, keep rule-based
    findings, never fail the agent."""

    agent, _ = _agent(canned_text="not JSON at all")
    previous = _full_he_draft_outputs(
        excellence_md=(
            # Missing 1.2 → rule-layer blocker
            "## 1.1 Objectives\n\n.\n\n"
            "## 1.3 State of the art\n\n.\n\n"
            "## 1.4 Open science\n\n.\n"
        )
    )
    previous["call_analyst"] = _call_analyst_output()

    output = await agent.run(_input(previous_outputs=previous))

    assert output.status == "completed"  # never failed
    report = ComplianceReport.model_validate(output.output)
    assert _has(report, "compliance_llm_unavailable")
    info_issues = [i for i in report.issues if i.code == "compliance_llm_unavailable"]
    assert info_issues[0].severity == "info"
    # Rule-layer findings still surface.
    assert _has(report, "missing_subsection")
    assert report.passed is False


# ── Programme dispatch ──────────────────────────────────────────────────


async def test_tubitak_draft_skips_llm_entirely() -> None:
    """Non-HE programmes should run rule-layer only — the FakeProvider
    has an empty script, so any LLM call would assertion-error."""

    agent, primary = _agent(primary_script=[])
    previous = {
        "call_analyst": {
            "agent_id": "call_analyst",
            "status": "completed",
            "output": {
                "eligibility": {},
                "scope_summary": "",
                "expected_outcomes": [],
                "expected_impacts": [],
                "evaluation_criteria": [],
                "page_limit": None,
                "language_required": "tr",
                "user_eligible": True,
                "user_eligibility_issues": [],
                "key_terms_to_use": [],
                "deadlines": {},
            },
        },
        # B2 way too short — rule-layer blocker for TÜBİTAK 1501
        "excellence_writer": {
            "agent_id": "excellence_writer",
            "status": "completed",
            "output": {
                "excellence_md": (
                    "## B1 Proje\n\nA.\n\n"
                    "## B2 Yenilikçi yönler\n\nÇok kısa.\n\n"
                    "## B3 Yöntem\n\n.\n"
                )
            },
        },
        "impact_writer": {
            "agent_id": "impact_writer",
            "status": "completed",
            "output": {"impact_md": ""},
        },
        "implementation_writer": {
            "agent_id": "implementation_writer",
            "status": "completed",
            "output": {"implementation_md": ""},
        },
    }

    output = await agent.run(
        _input(
            programme_id="tubitak_1501",
            language="tr",
            previous_outputs=previous,
            brief={"duration_months": 24},
        )
    )

    assert output.status == "completed"
    assert output.metadata["llm_check_skipped"] == "non_he_programme"
    assert primary.calls == []  # the agent never called the LLM
    report = ComplianceReport.model_validate(output.output)
    assert _has(report, "b2_too_short")
    assert report.passed is False


async def test_unknown_programme_skips_with_reason_code() -> None:
    agent, primary = _agent(primary_script=[])

    output = await agent.run(_input(programme_id="not_a_real_programme"))

    assert output.status == "skipped"
    assert output.metadata["reason_code"] == "unknown_programme"
    assert primary.calls == []


# ── Score / passed semantics ────────────────────────────────────────────


async def test_passed_true_with_only_warnings() -> None:
    """Warnings don't block — only blockers flip ``passed=False``."""

    agent, _ = _agent()
    previous = _full_he_draft_outputs()
    # Three missing key terms → three warnings; no blockers.
    previous["call_analyst"] = _call_analyst_output(
        key_terms=["nonexistent_term_a", "nonexistent_term_b", "nonexistent_term_c"]
    )

    output = await agent.run(_input(previous_outputs=previous))

    report = ComplianceReport.model_validate(output.output)
    warnings = [i for i in report.issues if i.severity == "warning"]
    assert len(warnings) >= 3
    assert all(i.severity != "blocker" for i in report.issues)
    assert report.passed is True
    # Score: 1.0 - 0.05 * 3 = 0.85
    assert report.compliance_score == 0.85


async def test_score_floors_at_zero() -> None:
    """Stacked blockers must not drive the score below zero — the
    heuristic uses ``max(0, ...)`` so the field always serialises."""

    canned = json.dumps(
        {
            "dnsh": {"present": False, "severity": "blocker", "explanation": ""},
            "gender_dimension": {
                "present": False,
                "severity": "blocker",
                "explanation": "",
            },
            "open_science": {
                "present": False,
                "severity": "blocker",
                "explanation": "",
            },
        }
    )
    agent, _ = _agent(canned_text=canned)
    previous = _full_he_draft_outputs(
        # Drop multiple subsections to stack rule-layer blockers too.
        excellence_md="## 1.1 Objectives\n\n.\n",
    )
    previous["call_analyst"] = _call_analyst_output()

    output = await agent.run(_input(previous_outputs=previous))

    report = ComplianceReport.model_validate(output.output)
    assert report.compliance_score >= 0.0
    assert report.passed is False


# ── stream / metadata ──────────────────────────────────────────────────


async def test_stream_yields_single_chunk() -> None:
    agent, _ = _agent()
    previous = _full_he_draft_outputs()
    previous["call_analyst"] = _call_analyst_output()

    events = [chunk async for chunk in agent.stream(_input(previous_outputs=previous))]
    assert len(events) == 1
    payload = json.loads(events[0])
    assert payload["agent_id"] == "compliance_reviewer"
    assert payload["status"] == "completed"


def test_agent_class_metadata() -> None:
    assert ComplianceReviewer.agent_id == "compliance_reviewer"
    assert ComplianceReviewer.version == "v1"
    assert ComplianceReviewer.requires_rag is False
    assert ComplianceReviewer.estimated_duration_seconds == 10


def test_agent_in_registry() -> None:
    """Confirm the registry export from src/agents/__init__.py."""
    from src.agents import AGENTS

    assert "compliance_reviewer" in AGENTS
    assert AGENTS["compliance_reviewer"] is ComplianceReviewer


# ── Type sanity ─────────────────────────────────────────────────────────


def test_validation_issue_serializes_round_trip() -> None:
    """Confirm the report's issues survive Pydantic round-trip — the
    agent's output is JSON-dumped through ``model_dump(mode='json')``
    and clients parse it back through ``ComplianceReport.model_validate``.
    """

    issue = ValidationIssue(
        severity="warning",
        section="impact",
        code="missing_dnsh",
        message_tr="DNSH yok",
        message_en="DNSH not mentioned",
    )
    report = ComplianceReport(
        passed=True, issues=[issue], compliance_score=0.95
    )
    payload = report.model_dump(mode="json")
    restored = ComplianceReport.model_validate(payload)
    assert restored.issues[0].code == "missing_dnsh"
