"""Call Analyst tests.

Three required scenarios from the task spec, plus two scaffolding tests
that protect the moving parts (prompt loader, JSON-failure path).

All LLM traffic is mocked via :class:`tests.llm.conftest.FakeProvider`.
The tests assert the agent's contract: given canned LLM JSON, produce a
typed :class:`CallAnalystOutput`-shaped :class:`AgentOutput`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from src.agents import CallAnalyst
from src.agents.base import AgentInput
from src.agents.call_analyst import CallAnalystOutput
from src.agents.prompts import load_prompt

from tests.llm.conftest import FakeProvider, build_router, make_response

CALLS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "calls"


# ── Helpers ──────────────────────────────────────────────────────────────


def _read_fixture(name: str) -> str:
    return (CALLS_DIR / name).read_text(encoding="utf-8")


def _agent_with_canned_json(canned_json: str) -> tuple[CallAnalyst, FakeProvider]:
    """Wire a CallAnalyst whose router answers with the given canned JSON."""

    primary = FakeProvider(
        "claude",
        [
            make_response(
                text=canned_json,
                model="claude-opus-4-7",
                provider="claude",
                input_tokens=4_200,
                output_tokens=600,
                cached_tokens=0,
                cost_usd=0.105,
            )
        ],
    )
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})
    return CallAnalyst(router=router), primary


def _make_input(
    *,
    fixture: str,
    programme_id: str,
    language: str = "en",
    brief: dict[str, object] | None = None,
) -> AgentInput:
    return AgentInput(
        proposal_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        programme_id=programme_id,
        language=language,  # type: ignore[arg-type]
        brief=brief or {"summary": "Project summary for testing."},
        call={"call_text": _read_fixture(fixture)},
        previous_outputs={},
    )


# ── Required scenarios ──────────────────────────────────────────────────


async def test_tubitak_1501_structured_output() -> None:
    canned = json.dumps(
        {
            "eligibility": {
                "eligible_countries": ["TR"],
                "eligible_entities": ["SME", "büyük ölçekli sermaye şirketi"],
                "min_partners": None,
                "min_countries": None,
                "trl_range": None,
            },
            "scope_summary": (
                "TÜBİTAK 1501 — Türkiye'de yerleşik sanayi şirketlerine "
                "yenilikçi AR-GE projeleri için destek."
            ),
            "expected_outcomes": ["Yenilikçi ürün veya süreç prototipi"],
            "expected_impacts": ["Ticarileşme ve pazara giriş"],
            "evaluation_criteria": [
                {"criterion": "Yenilikçilik ve özgünlük", "weight": None, "sub_criteria": []},
                {"criterion": "Teknik uygulanabilirlik", "weight": None, "sub_criteria": []},
                {"criterion": "Ticarileşme potansiyeli", "weight": None, "sub_criteria": []},
            ],
            "page_limit": None,
            "language_required": "tr",
            "user_eligible": True,
            "user_eligibility_issues": [],
            "key_terms_to_use": [
                "sanayi AR-GE",
                "yenilikçi ürün",
                "ticarileşme",
                "patent",
                "KOBİ",
            ],
            "deadlines": {"submission": "2026-09-30"},
        }
    )
    agent, primary = _agent_with_canned_json(canned)

    result = await agent.run(
        _make_input(
            fixture="tubitak_1501_sample.txt",
            programme_id="tubitak_1501",
            language="tr",
            brief={
                "summary": "AI-destekli üretim hattı optimizasyonu",
                "country": "TR",
                "entity_type": "SME",
            },
        )
    )

    assert result.status == "completed"
    assert result.agent_id == "call_analyst"
    assert result.duration_ms >= 0
    assert result.cost_usd == 0.105
    assert result.tokens_used == {"input": 4200, "output": 600, "cached": 0}

    parsed = CallAnalystOutput.model_validate(result.output)
    assert parsed.user_eligible is True
    assert parsed.language_required == "tr"
    assert parsed.page_limit is None
    assert "TR" in parsed.eligibility.eligible_countries
    assert len(parsed.evaluation_criteria) == 3

    assert len(primary.calls) == 1
    sent_request, model_used, _ = primary.calls[0]
    assert model_used == "claude-opus-4-7"
    assert sent_request.cache_system is True
    assert sent_request.temperature == 0.0


async def test_horizon_eu_page_limit_extraction() -> None:
    canned = json.dumps(
        {
            "eligibility": {
                "eligible_countries": ["EU MS", "Associated Countries", "Türkiye"],
                "eligible_entities": ["legal entity"],
                "min_partners": 3,
                "min_countries": 3,
                "trl_range": [4, 6],
            },
            "scope_summary": "Next-generation digital twin platforms for manufacturing.",
            "expected_outcomes": [
                "Open-source digital twin runtime",
                "Validated industrial pilots",
            ],
            "expected_impacts": ["25% reduction in downtime"],
            "evaluation_criteria": [
                {"criterion": "Excellence", "weight": 0.5, "sub_criteria": []},
                {"criterion": "Impact", "weight": 0.3, "sub_criteria": []},
                {"criterion": "Implementation", "weight": 0.2, "sub_criteria": []},
            ],
            "page_limit": 45,
            "language_required": "en",
            "user_eligible": True,
            "user_eligibility_issues": [],
            "key_terms_to_use": [
                "digital twin",
                "edge AI",
                "federated learning",
                "TRL 4-6",
                "OPC UA",
            ],
            "deadlines": {
                "submission": "2026-09-15",
                "ga_signature": "2027-03-01",
            },
        }
    )
    agent, _ = _agent_with_canned_json(canned)

    result = await agent.run(
        _make_input(
            fixture="horizon_eu_sample.txt",
            programme_id="horizon_eu_ria",
            language="en",
            brief={
                "summary": "Industrial digital-twin pilot for steel manufacturing",
                "country": "TR",
                "consortium_size": 4,
            },
        )
    )

    assert result.status == "completed"
    parsed = CallAnalystOutput.model_validate(result.output)
    assert parsed.page_limit == 45
    assert parsed.language_required == "en"
    assert parsed.eligibility.trl_range == (4, 6)
    assert parsed.eligibility.min_partners == 3
    assert parsed.deadlines["submission"] == "2026-09-15"


async def test_user_eligibility_issues_populated() -> None:
    canned = json.dumps(
        {
            "eligibility": {
                "eligible_countries": ["EU MS", "Associated Countries"],
                "eligible_entities": ["SME", "large enterprise"],
                "min_partners": 4,
                "min_countries": 4,
                "trl_range": [7, 9],
            },
            "scope_summary": "Industry-led circular-economy battery recycling pilots.",
            "expected_outcomes": ["Operational recycling pilot at TRL 9"],
            "expected_impacts": ["Reduction of critical raw materials demand"],
            "evaluation_criteria": [{"criterion": "Excellence", "weight": 0.5, "sub_criteria": []}],
            "page_limit": 30,
            "language_required": "en",
            "user_eligible": False,
            "user_eligibility_issues": [
                "Universities are not eligible as beneficiaries per Eligibility "
                "Section; the user's brief states entity_type=university."
            ],
            "key_terms_to_use": ["circular economy", "battery recycling"],
            "deadlines": {"submission": "2026-11-20"},
        }
    )
    agent, _ = _agent_with_canned_json(canned)

    result = await agent.run(
        _make_input(
            fixture="eligibility_mismatch_sample.txt",
            programme_id="horizon_eu_ria",
            language="en",
            brief={
                "summary": "Lithium battery recycling research from a university lab",
                "country": "TR",
                "entity_type": "university",
            },
        )
    )

    assert result.status == "completed"
    parsed = CallAnalystOutput.model_validate(result.output)
    assert parsed.user_eligible is False
    assert len(parsed.user_eligibility_issues) >= 1
    assert "university" in parsed.user_eligibility_issues[0].lower()


# ── Scaffolding tests ───────────────────────────────────────────────────


def test_load_prompt_resolves() -> None:
    template = load_prompt(programme="_shared", agent="call_analyst")
    assert template
    assert "{brief_summary}" in template
    assert "{call_text}" in template
    formatted = template.format(brief_summary="X", call_text="Y")
    assert "X" in formatted and "Y" in formatted


async def test_invalid_json_returns_failed_status() -> None:
    agent, primary = _agent_with_canned_json("this is not JSON")

    result = await agent.run(
        _make_input(fixture="tubitak_1501_sample.txt", programme_id="tubitak_1501", language="tr")
    )

    assert result.status == "failed"
    assert "invalid JSON" in result.metadata["error"]
    assert result.cost_usd == 0.105  # cost still surfaced even on failure
    assert len(primary.calls) == 1
