"""Excellence Writer — programme-aware writer agent.

Per-programme prompts live at
``src/agents/prompts/<programme_id>/excellence_writer/<version>.md``.
The agent picks the prompt by ``input.programme_id`` at runtime — no
``if programme_id == "..."`` branches in agent code, per docs/07 §1.

S1.D4.T2 ships only the TÜBİTAK 1501 prompt; other programmes raise
``FileNotFoundError`` until their prompt is added (S2.D6 onwards).

For v1 RAG retrieval is OPTIONAL — the agent reads
``input.previous_outputs.get("rag_context")`` if present, otherwise
substitutes an empty string into the prompt template. The Hallucination
Hunter (S2) verifies citations end-of-saga; here we only regex-extract
them as raw text.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agents.base import AgentInput, AgentOutput, BaseAgent
from src.agents.prompts import load_prompt_from_path
from src.citations import Citation as _Citation
from src.citations import extract_citations as _extract_citations
from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter
from src.programs import get_module

logger = logging.getLogger(__name__)

# Back-compat re-exports — the citation regex + Pydantic model moved to
# src/citations/extractors.py in S2.D7.T1. Tests and sibling writer
# agents that imported these names from here still work.
ExtractedCitation = _Citation
extract_citations = _extract_citations


# ── Output schema ───────────────────────────────────────────────────────


class ExcellenceWriterOutput(BaseModel):
    """Validated output of the Excellence Writer agent."""

    model_config = ConfigDict(extra="ignore")

    excellence_md: str
    subsections: dict[str, str] = Field(default_factory=dict)
    citations_used: list[ExtractedCitation] = Field(default_factory=list)
    key_terms_used: list[str] = Field(default_factory=list)
    word_count: int = 0
    estimated_page_count: int = 0


# ── Subsection splitting ────────────────────────────────────────────────


def _split_by_heading(md: str, heading_keys: list[str]) -> dict[str, str]:
    """Map subsection key (e.g. ``"B2_yenilikci_yonleri"``) → body text.

    The prompt instructs the LLM to produce ``## B1 …``, ``## B2 …`` etc.
    We match the heading prefix (``B1``, ``B2``, …) up to the next match
    or EOF. Trailing whitespace is stripped per body.
    """

    if not md.strip():
        return dict.fromkeys(heading_keys, "")

    # Build a regex that finds any of the prefixes (B1, B2, B3, B4).
    prefixes = sorted({k.split("_", 1)[0] for k in heading_keys}, key=len, reverse=True)
    pattern = re.compile(
        r"^##\s+(" + "|".join(re.escape(p) for p in prefixes) + r")\b.*$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(md))

    bodies: dict[str, str] = dict.fromkeys(heading_keys, "")
    for i, m in enumerate(matches):
        prefix = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[start:end].strip()
        # Map prefix → first matching subsection key (e.g. "B2" → "B2_yenilikci_yonleri")
        for key in heading_keys:
            if key.startswith(prefix + "_") and not bodies[key]:
                bodies[key] = body
                break
    return bodies


# ── Agent ───────────────────────────────────────────────────────────────


def _word_count(text: str) -> int:
    """Plain-text word count. Strips Markdown heading markers and citation
    brackets so the count reflects the prose the panel actually reads."""

    cleaned = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\[[^\[\]]+\]|\([^()]+\d{4}[a-z]?\)", "", cleaned)
    return len(cleaned.split())


class ExcellenceWriter(BaseAgent):
    agent_id = "excellence_writer"
    name = "Excellence Writer"
    description = "Writes the Excellence section of a grant proposal."
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
                "excellence_writer_no_prompt_for_programme",
                extra={"programme_id": input.programme_id},
            )
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                metadata={"error": f"no Excellence Writer prompt for {input.programme_id}: {exc}"},
            )

        call_metadata = self._call_metadata(input)
        key_terms = call_metadata.get("key_terms_to_use", []) or []
        rag_context = self._rag_context(input)

        try:
            system = template.format(
                brief=json.dumps(input.brief, ensure_ascii=False, indent=2),
                call_metadata=json.dumps(call_metadata, ensure_ascii=False, indent=2),
                rag_context=rag_context,
                key_terms_to_use=", ".join(str(t) for t in key_terms),
                language=input.language,
            )
        except KeyError as exc:
            logger.exception("excellence_writer_template_substitution_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                metadata={"error": f"prompt template missing key: {exc}"},
            )

        request = LLMRequest(
            task="excellence_writer",
            tenant_id=input.tenant_id,
            proposal_id=input.proposal_id,
            system=system,
            messages=[
                LLMMessage(
                    role="user",
                    content="Excellence bölümünü (B1-B4) yaz.",
                )
            ],
            cache_system=True,
        )

        started = time.monotonic()
        try:
            response = await self._router.complete(request)
        except Exception as exc:
            logger.exception("excellence_writer_llm_failed")
            return AgentOutput(
                agent_id=self.agent_id,
                status="failed",
                duration_ms=int((time.monotonic() - started) * 1000),
                metadata={"error": f"llm call failed: {exc}"},
            )
        duration_ms = int((time.monotonic() - started) * 1000)

        excellence_md = _strip_code_fence(response.text)
        layout = module.subsection_map.get("excellence", [])
        subsections = _split_by_heading(excellence_md, layout)
        citations = extract_citations(excellence_md)
        words = _word_count(excellence_md)
        # ~500 words per A4 page in TÜBİTAK style; rough heuristic for v1.
        estimated_pages = max(1, words // 500)

        validated = ExcellenceWriterOutput(
            excellence_md=excellence_md,
            subsections=subsections,
            citations_used=citations,
            key_terms_used=[t for t in key_terms if str(t).lower() in excellence_md.lower()],
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
        """Yield the rendered Excellence section.

        Real token-by-token streaming via the LLMRouter is wired in S2
        when the orchestrator + SSE endpoint land. For v1 we keep the
        :class:`BaseAgent.stream` interface honest by yielding the
        completed Markdown body in two chunks (subsections-only header
        first, then the rest) so a downstream SSE consumer sees more
        than a single oversized event.
        """

        result = await self.run(input)
        body = str(result.output.get("excellence_md", "")) or ""
        if not body:
            yield ""
            return
        # Split at the first blank line so the consumer gets an early
        # "first paragraph" event without further LLMRouter changes.
        split_at = body.find("\n\n")
        if split_at == -1:
            yield body
            return
        yield body[: split_at + 2]
        yield body[split_at + 2 :]

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _call_metadata(input: AgentInput) -> dict[str, Any]:
        """Pull Call Analyst output from previous_outputs.

        The orchestrator (S2+) places each agent's AgentOutput dict under
        its agent_id. Tests pass it directly. Missing data → empty dict
        (the prompt instructs the LLM to handle this by leaning on the
        brief).
        """

        prev = input.previous_outputs.get("call_analyst")
        if isinstance(prev, dict):
            output = prev.get("output")
            if isinstance(output, dict):
                return output
        return {}

    @staticmethod
    def _rag_context(input: AgentInput) -> str:
        """RAG context string. Empty for v1 (corpus seeded in S2.D6)."""

        rag = input.previous_outputs.get("rag_context")
        if isinstance(rag, str):
            return rag
        if isinstance(rag, list):
            # Pre-formatted chunks list — concatenate.
            return "\n\n---\n\n".join(str(c) for c in rag)
        return ""


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1 :]
    if stripped.endswith("```"):
        stripped = stripped[: -len("```")]
    return stripped.strip()


__all__ = [
    "ExcellenceWriter",
    "ExcellenceWriterOutput",
    "ExtractedCitation",
    "extract_citations",
]
