"""Impact Writer — Section 2 of a grant proposal (HE) / equivalent.

Mirrors :class:`~src.agents.excellence_writer.ExcellenceWriter` in shape:
loads the prompt by programme via the registry, runs the LLM through the
router (task=``impact_writer`` → Sonnet/Opus per docs/06 §7.2 — actually
Opus 4.7, see ``TASK_ROUTES``), splits the rendered Markdown into the
programme's impact subsections, and regex-extracts citations.

For HE the subsections are 2.1 / 2.2 / 2.3 (per docs/07 §3.2). For
TÜBİTAK they're C1 / C2 / C3 — but no TÜBİTAK Impact prompt ships in
S2.D6.T1; that comes later.
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


class ImpactWriterOutput(BaseModel):
    """Validated output of the Impact Writer agent."""

    model_config = ConfigDict(extra="ignore")

    impact_md: str
    subsections: dict[str, str] = Field(default_factory=dict)
    citations_used: list[ExtractedCitation] = Field(default_factory=list)
    key_terms_used: list[str] = Field(default_factory=list)
    word_count: int = 0
    estimated_page_count: int = 0


class ImpactWriter(BaseAgent):
    agent_id = "impact_writer"
    name = "Impact Writer"
    description = "Writes the Impact section of a grant proposal."
    version = "v1"
    requires_rag = True
    estimated_duration_seconds = 30

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
                "impact_writer_no_prompt_for_programme",
                extra={"programme_id": input.programme_id},
            )
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                metadata={"error": f"no Impact Writer prompt for {input.programme_id}: {exc}"},
            )

        call_metadata = self._call_metadata(input)
        excellence_summary = self._previous_section(input, "excellence_writer", "excellence_md")
        key_terms = call_metadata.get("key_terms_to_use", []) or []
        rag_context = self._rag_context(input)

        try:
            system = template.format(
                brief=json.dumps(input.brief, ensure_ascii=False, indent=2),
                call_metadata=json.dumps(call_metadata, ensure_ascii=False, indent=2),
                excellence_summary=excellence_summary,
                rag_context=rag_context,
                key_terms_to_use=", ".join(str(t) for t in key_terms),
                language=input.language,
            )
        except KeyError as exc:
            logger.exception("impact_writer_template_substitution_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                metadata={"error": f"prompt template missing key: {exc}"},
            )

        request = LLMRequest(
            task="impact_writer",
            tenant_id=input.tenant_id,
            proposal_id=input.proposal_id,
            system=system,
            messages=[LLMMessage(role="user", content="Write the Impact section.")],
            cache_system=True,
        )

        started = time.monotonic()
        try:
            response = await self._router.complete(request)
        except Exception as exc:
            logger.exception("impact_writer_llm_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                metadata={"error": f"llm call failed: {exc}"},
            )
        duration_ms = int((time.monotonic() - started) * 1000)

        impact_md = _strip_code_fence(response.text)
        layout = module.subsection_map.get("impact", [])
        subsections = _split_by_heading(impact_md, layout)
        citations = extract_citations(impact_md)
        words = _word_count(impact_md)
        estimated_pages = max(1, words // 500)

        validated = ImpactWriterOutput(
            impact_md=impact_md,
            subsections=subsections,
            citations_used=citations,
            key_terms_used=[t for t in key_terms if str(t).lower() in impact_md.lower()],
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
        body = str(result.output.get("impact_md", "")) or ""
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


__all__ = ["ImpactWriter", "ImpactWriterOutput"]
