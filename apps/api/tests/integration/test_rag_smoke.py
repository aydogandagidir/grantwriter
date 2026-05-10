"""RAG quality + cost smoke tests.

Per Sprint 2 / Day 10 (docs/sprint-roadmap.md §2): a lightweight
suite that catches RAG / prompt-size regressions before they hit
production cost.

Two layers:
1. **Prompt-size guards** — assert each writer's system prompt for
   each programme stays under a budget (catches accidental prompt
   bloat that would inflate every LLM call's cost).
2. **RAG-context propagation** — assert that when the saga's
   ``previous_outputs.rag_context`` is populated, the writer's
   system prompt actually includes the retrieved chunks (catches
   regressions where RAG silently drops out of the call).

Live-DB retriever quality (3-5 chunks per query, similarity-rank
correctness) is covered separately by ``tests/rag/test_retriever.py``,
which seeds a real corpus in pgvector — that integration is gated on
``TEST_DATABASE_URL`` and out of scope for this smoke layer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from src.agents import ExcellenceWriter
from src.agents.base import AgentInput, AgentOutput
from src.llm.base import LLMRequest, LLMResponse, LLMUsage
from src.programs import REGISTRY
from tests.llm.conftest import FakeProvider, build_router

# Per-agent prompt-size budget (bytes) before any RAG context or brief
# is templated in. These bounds were chosen by measuring the current
# v1 prompts + ~25% headroom; tightening them is fine, loosening them
# requires a comment explaining what changed.
PROMPT_SIZE_BUDGET_BYTES: dict[str, int] = {
    "excellence_writer": 12_000,
    "impact_writer": 12_000,
    "implementation_writer": 12_000,
    "compliance_reviewer": 8_000,
    "call_analyst": 8_000,
}


# ── Prompt-size budget ─────────────────────────────────────────────────


@pytest.mark.parametrize("programme_id", sorted(REGISTRY))
def test_excellence_prompt_size_within_budget(programme_id: str) -> None:
    """Each programme's excellence_writer prompt must fit the budget.

    Catches prompt regressions like a developer pasting a giant
    inline example into v1.md. The budget is generous (~12KB) but
    finite — anything larger should be split into a separate file
    or moved to RAG context.
    """

    module = REGISTRY[programme_id]
    prompt_path = Path(module.get_prompt_path("excellence_writer"))
    raw = prompt_path.read_text(encoding="utf-8")
    budget = PROMPT_SIZE_BUDGET_BYTES["excellence_writer"]
    assert len(raw.encode("utf-8")) < budget, (
        f"{programme_id} excellence prompt is "
        f"{len(raw.encode('utf-8'))} bytes (budget {budget})"
    )


@pytest.mark.parametrize(
    "agent_id", ["impact_writer", "implementation_writer"]
)
def test_he_writer_prompts_within_budget(agent_id: str) -> None:
    """HE-specific budgets for the other two writers.

    Only HE / TÜBİTAK / KOSGEB / Cascade have these prompts today;
    skip the check if the file isn't present rather than fabricating
    a budget for a programme that hasn't shipped them yet.
    """

    budget = PROMPT_SIZE_BUDGET_BYTES[agent_id]
    for programme_id, module in REGISTRY.items():
        prompt_path = Path(module.get_prompt_path(agent_id))
        if not prompt_path.is_file():
            continue
        raw = prompt_path.read_text(encoding="utf-8")
        assert len(raw.encode("utf-8")) < budget, (
            f"{programme_id} {agent_id} prompt is "
            f"{len(raw.encode('utf-8'))} bytes (budget {budget})"
        )


def test_compliance_reviewer_prompt_within_budget() -> None:
    """The shared compliance reviewer prompt is loaded by every HE
    programme via ``load_prompt(programme="_shared", ...)``. One
    prompt, one budget."""

    from src.agents.prompts import load_prompt

    prompt = load_prompt(programme="_shared", agent="compliance_reviewer")
    budget = PROMPT_SIZE_BUDGET_BYTES["compliance_reviewer"]
    assert len(prompt.encode("utf-8")) < budget


# ── RAG context propagation ────────────────────────────────────────────


def _capture_request() -> tuple[FakeProvider, list[LLMRequest]]:
    """Build a FakeProvider that records every LLMRequest it sees."""

    captured: list[LLMRequest] = []

    def factory(
        request: LLMRequest, model: str, _api_key: str
    ) -> LLMResponse:
        captured.append(request)
        # Return a minimal valid Markdown body so the writer parses it.
        return LLMResponse(
            text=(
                "## 1.1 Objectives\n\n.\n\n"
                "## 1.2 Methodology\n\n.\n\n"
                "## 1.3 State of the art\n\n.\n\n"
                "## 1.4 Open science\n\n.\n"
            ),
            model=model,
            provider="claude",
            usage=LLMUsage(input_tokens=2000, output_tokens=200),
            cost_usd=0.05,
        )

    primary = FakeProvider("claude", [factory] * 5)
    return primary, captured


def _agent_input(*, rag_context: str | list[str] | None = None) -> AgentInput:
    previous: dict[str, Any] = {
        "call_analyst": {
            "agent_id": "call_analyst",
            "status": "completed",
            "output": {
                "scope_summary": "Digital twin pilot.",
                "key_terms_to_use": ["digital twin"],
            },
        },
    }
    if rag_context is not None:
        previous["rag_context"] = rag_context

    return AgentInput(
        proposal_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        programme_id="horizon_eu_ria",
        language="en",
        brief={"problem_statement": "EU manufacturers depend on imports."},
        call={"call_text": "HORIZON-CL4 fixture"},
        previous_outputs=previous,
    )


async def test_writer_includes_rag_chunks_in_system_prompt() -> None:
    """When ``previous_outputs.rag_context`` is a string, the writer's
    rendered system prompt must contain it. Catches regressions where
    RAG silently drops out of the LLM call."""

    primary, captured = _capture_request()
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    sentinel_chunk = "RAG_SENTINEL_a8f3c901_chunk_body"
    agent = ExcellenceWriter(router=router)
    result = await agent.run(_agent_input(rag_context=sentinel_chunk))

    assert isinstance(result, AgentOutput)
    assert len(captured) == 1
    sent = captured[0]
    assert sentinel_chunk in sent.system, (
        "RAG context did not propagate into the system prompt"
    )


async def test_writer_handles_missing_rag_context_gracefully() -> None:
    """No ``rag_context`` in previous_outputs → the writer still sends
    a valid prompt (with an empty retrieved-context block), no crash."""

    primary, captured = _capture_request()
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    agent = ExcellenceWriter(router=router)
    result = await agent.run(_agent_input(rag_context=None))

    assert result.status == "completed"
    assert len(captured) == 1
    # Prompt should be well-formed even with no RAG.
    assert "<retrieved_context>" in captured[0].system
    assert len(captured[0].system) > 1000  # not catastrophically truncated


async def test_writer_concatenates_rag_chunk_list() -> None:
    """rag_context can also be a list of chunks — the writer joins them
    with a separator before substitution. Catches regressions where the
    list form silently produces "[<chunks>]" string-conversion garbage."""

    primary, captured = _capture_request()
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    chunks = [
        "RAG_LIST_CHUNK_1_marker",
        "RAG_LIST_CHUNK_2_marker",
    ]
    agent = ExcellenceWriter(router=router)
    await agent.run(_agent_input(rag_context=chunks))

    sent_system = captured[0].system
    for chunk in chunks:
        assert chunk in sent_system
    # And no Python list repr leaked through.
    assert "['RAG" not in sent_system


# ── Token-usage cost guard ─────────────────────────────────────────────


async def test_writer_rendered_prompt_stays_under_cost_threshold() -> None:
    """End-to-end cost guard: rendered system prompt (template + RAG +
    brief) stays under a 32 KB ceiling. Anything bigger is a sign of
    bloat that needs review.

    32 KB at ~4 chars/token ≈ 8 K tokens → ~$0.12 per Opus 4.7 input
    call. Our writer budgets are designed around 12 K input tokens
    total (including RAG), so 8 K base prompt is the upper bound.
    """

    primary, captured = _capture_request()
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    # Realistic-sized RAG: ~2 KB per chunk × 5 chunks = 10 KB.
    rag_chunks = ["x" * 2000 for _ in range(5)]
    agent = ExcellenceWriter(router=router)
    await agent.run(_agent_input(rag_context=rag_chunks))

    rendered = captured[0].system
    rendered_size = len(rendered.encode("utf-8"))
    ceiling = 32 * 1024  # 32 KB
    assert rendered_size < ceiling, (
        f"Rendered system prompt is {rendered_size} bytes (ceiling {ceiling}); "
        "investigate prompt or RAG size before merging."
    )


# ── Async / fixture sanity for type checkers ───────────────────────────


_ = AsyncIterator  # suppress unused import (kept for parity with conftest)
