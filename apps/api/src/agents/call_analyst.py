"""Call Analyst — the first agent in the orchestrator's saga.

Parses a grant call document into structured metadata that every
downstream agent consumes via ``previous_outputs.call_metadata``. Per
docs/06 §4.1 it uses Claude Opus 4.7 at temperature 0 (defaults from
``TASK_ROUTES["call_analyst"]``); the agent does not override either —
the router is the single source of truth on model selection.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.agents.prompts import load_prompt
from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


class Eligibility(BaseModel):
    eligible_countries: list[str] = Field(default_factory=list)
    eligible_entities: list[str] = Field(default_factory=list)
    min_partners: int | None = None
    min_countries: int | None = None
    trl_range: tuple[int, int] | None = None


class EvaluationCriterion(BaseModel):
    criterion: str
    weight: float | None = None
    sub_criteria: list[str] = Field(default_factory=list)


class CallAnalystOutput(BaseModel):
    """Validated structured output of the Call Analyst.

    ``extra="ignore"`` so a stray field from the LLM does not fail the
    whole proposal. Required fields stay required.
    """

    model_config = ConfigDict(extra="ignore")

    eligibility: Eligibility
    scope_summary: str
    expected_outcomes: list[str] = Field(default_factory=list)
    expected_impacts: list[str] = Field(default_factory=list)
    evaluation_criteria: list[EvaluationCriterion] = Field(default_factory=list)
    page_limit: int | None = None
    language_required: Literal["tr", "en"] | None = None
    user_eligible: bool
    user_eligibility_issues: list[str] = Field(default_factory=list)
    key_terms_to_use: list[str] = Field(default_factory=list)
    deadlines: dict[str, str] = Field(default_factory=dict)


def _strip_code_fence(text: str) -> str:
    """Remove a leading / trailing Markdown code fence if present.

    Anthropic occasionally wraps JSON in ```json ... ``` despite explicit
    instructions otherwise. Strip a single fence pair; don't try anything
    clever inside the body.
    """

    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
    if stripped.endswith("```"):
        stripped = stripped[: -len("```")]
    return stripped.strip()


class CallAnalyst(BaseAgent):
    agent_id = "call_analyst"
    name = "Call Analyst"
    description = "Parses grant call documents into structured metadata."
    version = "v1"
    requires_rag = False
    estimated_duration_seconds = 10

    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def run(self, input: AgentInput) -> AgentOutput:
        brief_summary = str(input.brief.get("summary") or "")
        call_text = str(input.call.get("call_text") or "")

        try:
            template = load_prompt(programme="_shared", agent="call_analyst", version=self.version)
            system = template.format(brief_summary=brief_summary, call_text=call_text)
        except (FileNotFoundError, KeyError) as exc:
            logger.exception("call_analyst_prompt_load_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                metadata={"error": f"prompt load failed: {exc}"},
            )

        request = LLMRequest(
            task="call_analyst",
            tenant_id=input.tenant_id,
            proposal_id=input.proposal_id,
            system=system,
            messages=[
                LLMMessage(
                    role="user",
                    content="Extract the structured metadata as specified.",
                )
            ],
            cache_system=True,
            temperature=0.0,
        )

        started = time.monotonic()
        try:
            response = await self._router.complete(request)
        except Exception as exc:
            logger.exception("call_analyst_llm_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                metadata={"error": f"llm call failed: {exc}"},
            )
        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            payload = json.loads(_strip_code_fence(response.text))
        except json.JSONDecodeError as exc:
            logger.warning(
                "call_analyst_invalid_json",
                extra={"text_preview": response.text[:200], "error": str(exc)},
            )
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                duration_ms=duration_ms,
                cost_usd=response.cost_usd,
                tokens_used=_tokens(response),
                metadata={"error": f"invalid JSON from LLM: {exc}"},
            )

        try:
            validated = CallAnalystOutput.model_validate(payload)
        except ValidationError as exc:
            logger.warning("call_analyst_schema_violation", extra={"error": str(exc)})
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                duration_ms=duration_ms,
                cost_usd=response.cost_usd,
                tokens_used=_tokens(response),
                metadata={"error": "schema_violation", "details": exc.errors()},
            )

        return AgentOutput(
            agent_id=self.agent_id,
            status="completed",
            output=validated.model_dump(),
            duration_ms=duration_ms,
            cost_usd=response.cost_usd,
            tokens_used=_tokens(response),
            metadata={
                "model": response.model,
                "provider": response.provider,
                "used_byok": response.used_byok,
            },
        )

    async def stream(self, input: AgentInput) -> AsyncIterator[str]:
        """Structured-JSON agents stream their output as a single chunk.

        Token-streaming a partial JSON document is worse than waiting —
        the consumer can't act on a half-formed object. Writer agents
        (Excellence / Impact / Implementation) override this with real
        delta streaming.
        """

        result = await self.run(input)
        yield json.dumps(result.output)


def _tokens(response: Any) -> dict[str, int]:
    return {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
        "cached": response.usage.cached_tokens,
    }


__all__ = ["CallAnalyst", "CallAnalystOutput", "Eligibility", "EvaluationCriterion"]
