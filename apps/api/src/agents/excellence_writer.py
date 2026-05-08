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
from src.agents.prompts import load_prompt
from src.llm.base import LLMMessage, LLMRequest
from src.llm.router import LLMRouter

logger = logging.getLogger(__name__)


# ── Output schema ───────────────────────────────────────────────────────


class ExtractedCitation(BaseModel):
    """A regex-extracted citation marker. Verification is the Hallucination
    Hunter's job (S2) — at this stage all entries are unverified raw text."""

    model_config = ConfigDict(frozen=True)

    raw_text: str
    verified: bool = False


class ExcellenceWriterOutput(BaseModel):
    """Validated output of the Excellence Writer agent."""

    model_config = ConfigDict(extra="ignore")

    excellence_md: str
    subsections: dict[str, str] = Field(default_factory=dict)
    citations_used: list[ExtractedCitation] = Field(default_factory=list)
    key_terms_used: list[str] = Field(default_factory=list)
    word_count: int = 0
    estimated_page_count: int = 0


# ── Citation extraction ─────────────────────────────────────────────────

# Bracketed author-year, e.g. "[Smith 2023]", "[Aydın et al., 2024]",
# "[Smith and Jones, 2023]" — anything inside square brackets ending with a
# 4-digit year. Allowed Turkish characters are explicit.
_BRACKET_AUTHOR_YEAR = re.compile(r"\[[A-ZÇĞİÖŞÜa-zçğıöşü][^\[\]]{1,80}\d{4}[a-z]?\]")
# Numbered citations [1], [12]
_BRACKET_NUMERIC = re.compile(r"\[\d{1,3}\]")
# Parenthesised author-year, e.g. "(Smith 2023)", "(Aydın 2024)"
_PAREN_AUTHOR_YEAR = re.compile(r"\([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü\s.,&]{1,60}\d{4}[a-z]?\)")


def extract_citations(text: str) -> list[ExtractedCitation]:
    """Pull every citation marker out of ``text`` as raw, unverified entries.

    Hallucination Hunter (S2) re-extracts and verifies against Crossref /
    OpenAlex. We deliberately keep this loose — false positives here are
    cheap; false negatives later are expensive.
    """

    seen: set[str] = set()
    out: list[ExtractedCitation] = []
    for pattern in (_BRACKET_AUTHOR_YEAR, _BRACKET_NUMERIC, _PAREN_AUTHOR_YEAR):
        for match in pattern.findall(text):
            if match not in seen:
                seen.add(match)
                out.append(ExtractedCitation(raw_text=match))
    return out


# ── Subsection splitting ────────────────────────────────────────────────


def _split_by_heading(md: str, heading_keys: list[str]) -> dict[str, str]:
    """Map subsection key (e.g. ``"B2_yenilikci_yonleri"``) → body text.

    The prompt instructs the LLM to produce ``## B1 …``, ``## B2 …`` etc.
    We match the heading prefix (``B1``, ``B2``, …) up to the next match
    or EOF. Trailing whitespace is stripped per body.
    """

    if not md.strip():
        return {key: "" for key in heading_keys}

    # Build a regex that finds any of the prefixes (B1, B2, B3, B4).
    prefixes = sorted({k.split("_", 1)[0] for k in heading_keys}, key=len, reverse=True)
    pattern = re.compile(
        r"^##\s+(" + "|".join(re.escape(p) for p in prefixes) + r")\b.*$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(md))

    bodies: dict[str, str] = {key: "" for key in heading_keys}
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


# ── Programme-specific subsection layout ────────────────────────────────

# Mirror of programs/<programme_id>.subsection_map["excellence"] but pinned
# here for v1 because the program-modules layer doesn't exist yet (S2).
# Once src/programs/ lands, this constant moves there and gets read via
# ``get_module(programme_id).subsection_map["excellence"]``.
_SUBSECTION_LAYOUT: dict[str, list[str]] = {
    "tubitak_1501": [
        "B1_proje_konusu_ve_amaclari",
        "B2_yenilikci_yonleri",
        "B3_yontem_ve_teknik",
        "B4_literature_review",
    ],
}


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
            template = load_prompt(
                programme=input.programme_id,
                agent="excellence_writer",
                version=self.version,
            )
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
        layout = _SUBSECTION_LAYOUT.get(input.programme_id, [])
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
