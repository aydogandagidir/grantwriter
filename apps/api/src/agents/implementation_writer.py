"""Implementation Writer — Section 3 of a grant proposal.

Same shape as :class:`~src.agents.impact_writer.ImpactWriter`. Reads the
Excellence + Impact summaries from ``previous_outputs`` so it can lay
work packages and budget against committed objectives. Structured
fields (``work_packages``, ``budget``, ``risks``) are best-effort
extracted from the Markdown for v1 — the orchestrator (S2+) will move
to a structured-output prompt as we tighten the loop.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.agents.excellence_writer import (
    ExtractedCitation,
    _split_by_heading,
    _strip_code_fence,
    _word_count,
    extract_citations,
)
from src.agents.prompts import load_prompt_from_path
from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter
from src.programs import get_module

logger = logging.getLogger(__name__)


class ImplementationWriterOutput(BaseModel):
    """Validated output of the Implementation Writer agent."""

    model_config = ConfigDict(extra="ignore")

    implementation_md: str
    subsections: dict[str, str] = Field(default_factory=dict)
    work_packages: list[dict[str, Any]] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    citations_used: list[ExtractedCitation] = Field(default_factory=list)
    key_terms_used: list[str] = Field(default_factory=list)
    word_count: int = 0
    estimated_page_count: int = 0


class ImplementationWriter(BaseAgent):
    agent_id = "implementation_writer"
    name = "Implementation Writer"
    description = "Writes the Implementation section of a grant proposal."
    version = "v1"
    requires_rag = True
    estimated_duration_seconds = 45

    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def run(self, input: AgentInput) -> AgentOutput:
        try:
            module = get_module(input.programme_id)
        except KeyError as exc:
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                metadata={"error": f"unknown programme: {exc}"},
            )

        try:
            template = load_prompt_from_path(module.get_prompt_path(self.agent_id))
        except FileNotFoundError as exc:
            logger.warning(
                "implementation_writer_no_prompt_for_programme",
                extra={"programme_id": input.programme_id},
            )
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                metadata={
                    "error": (f"no Implementation Writer prompt for {input.programme_id}: {exc}")
                },
            )

        call_metadata = self._call_metadata(input)
        excellence_summary = self._previous_section(input, "excellence_writer", "excellence_md")
        impact_summary = self._previous_section(input, "impact_writer", "impact_md")
        key_terms = call_metadata.get("key_terms_to_use", []) or []
        rag_context = self._rag_context(input)

        try:
            system = template.format(
                brief=json.dumps(input.brief, ensure_ascii=False, indent=2),
                call_metadata=json.dumps(call_metadata, ensure_ascii=False, indent=2),
                excellence_summary=excellence_summary,
                impact_summary=impact_summary,
                rag_context=rag_context,
                key_terms_to_use=", ".join(str(t) for t in key_terms),
                language=input.language,
            )
        except KeyError as exc:
            logger.exception("implementation_writer_template_substitution_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                metadata={"error": f"prompt template missing key: {exc}"},
            )

        request = LLMRequest(
            task="implementation_writer",
            tenant_id=input.tenant_id,
            proposal_id=input.proposal_id,
            system=system,
            messages=[LLMMessage(role="user", content="Write the Implementation section.")],
            cache_system=True,
        )

        started = time.monotonic()
        try:
            response = await self._router.complete(request)
        except Exception as exc:
            logger.exception("implementation_writer_llm_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                metadata={"error": f"llm call failed: {exc}"},
            )
        duration_ms = int((time.monotonic() - started) * 1000)

        implementation_md = _strip_code_fence(response.text)
        layout = module.subsection_map.get("implementation", [])
        subsections = _split_by_heading(implementation_md, layout)
        citations = extract_citations(implementation_md)
        words = _word_count(implementation_md)
        estimated_pages = max(1, words // 500)

        # Structured fields are best-effort: the orchestrator may already
        # have these from a brief field, or a downstream pass will fill
        # them. We surface empty defaults here.
        validated = ImplementationWriterOutput(
            implementation_md=implementation_md,
            subsections=subsections,
            work_packages=[],
            budget={},
            risks=[],
            citations_used=citations,
            key_terms_used=[t for t in key_terms if str(t).lower() in implementation_md.lower()],
            word_count=words,
            estimated_page_count=estimated_pages,
        )

        return AgentOutput(
            agent_id=self.agent_id,
            status="completed",
            output=validated.model_dump(),
            citations_extracted=[c.model_dump() for c in citations],
            duration_ms=duration_ms,
            cost_usd=response.cost_usd,
            tokens_used={
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
                "cached": response.usage.cached_tokens,
            },
            metadata={
                "model": response.model,
                "provider": response.provider,
                "used_byok": response.used_byok,
                "programme_id": input.programme_id,
            },
        )

    async def stream(self, input: AgentInput) -> AsyncIterator[str]:
        result = await self.run(input)
        body = str(result.output.get("implementation_md", "")) or ""
        if not body:
            yield ""
            return
        split_at = body.find("\n\n")
        if split_at == -1:
            yield body
            return
        yield body[: split_at + 2]
        yield body[split_at + 2 :]

    @staticmethod
    def _call_metadata(input: AgentInput) -> dict[str, Any]:
        prev = input.previous_outputs.get("call_analyst")
        if isinstance(prev, dict):
            output = prev.get("output")
            if isinstance(output, dict):
                return output
        return {}

    @staticmethod
    def _previous_section(input: AgentInput, agent_id: str, key: str) -> str:
        prev = input.previous_outputs.get(agent_id)
        if isinstance(prev, dict):
            output = prev.get("output")
            if isinstance(output, dict):
                return str(output.get(key) or "")
        return ""

    @staticmethod
    def _rag_context(input: AgentInput) -> str:
        rag = input.previous_outputs.get("rag_context")
        if isinstance(rag, str):
            return rag
        if isinstance(rag, list):
            return "\n\n---\n\n".join(str(c) for c in rag)
        return ""


__all__ = ["ImplementationWriter", "ImplementationWriterOutput"]
