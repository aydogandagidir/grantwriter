"""Persist sentence-level provenance for saga-generated sections.

Runs after the writers finish (Excellence / Impact / Implementation).
Each writer's markdown is split into sentences, each sentence gets a
deterministic ``sentence_id``, and the batch is upserted into
``proposal_provenance`` so the FE provenance panel + the HE AI
disclosure auto-fill see the section as ``ai-generated``.

Best-effort: any DB / parsing hiccup logs + swallows so a provenance
write never breaks the saga's main path. Mirrors the auto-snapshot
contract added in Sprint 4 carry-over.

The splitter is intentionally simple — paragraph + regex on terminal
punctuation. Heavy NLP (spaCy) is overkill for the EU/TR scientific
prose the writers produce, and we already gain accuracy from the
markdown structure (headings get their own sentence, bullets stay
intact).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from src.agents.base import AgentOutput

logger = logging.getLogger(__name__)


# Maps writer agent_id → (section name, markdown key in AgentOutput).
WRITER_SECTIONS: dict[str, tuple[str, str]] = {
    "excellence_writer": ("excellence", "excellence_md"),
    "impact_writer": ("impact", "impact_md"),
    "implementation_writer": ("implementation", "implementation_md"),
}


# Match end-of-sentence punctuation followed by whitespace OR end-of-line.
# Keeps the punctuation attached to the preceding sentence.
_SENTENCE_TERMINATOR = re.compile(r"(?<=[.!?])\s+(?=[A-ZĞÜŞİÖÇ0-9])")
# Collapse internal whitespace so the resulting sentence is uniform.
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class SentenceRow:
    """One row destined for ``proposal_provenance`` upsert."""

    section: str
    sentence_id: str
    content: str
    source: str
    agent_id: str | None
    llm_model: str | None
    llm_tokens: int | None


def split_into_sentences(markdown: str) -> list[str]:
    """Naive markdown-aware sentence splitter.

    Strategy:
    1. Split on blank lines → paragraphs.
    2. For each paragraph, treat heading lines (start with ``#``) and
       bullet list items as standalone sentences.
    3. For the remaining body, split on the terminal-punctuation regex.

    Returns a list of trimmed, non-empty sentences in document order.
    """

    if not markdown:
        return []

    paragraphs = [p.strip() for p in markdown.split("\n\n") if p.strip()]
    out: list[str] = []
    for paragraph in paragraphs:
        # Heading: whole line is one sentence.
        if paragraph.lstrip().startswith("#"):
            out.append(_normalise(paragraph))
            continue
        # Bullet list: each line is its own "sentence".
        if all(_is_bullet_line(line) for line in paragraph.splitlines()):
            for line in paragraph.splitlines():
                cleaned = _normalise(line.lstrip("-* ").strip())
                if cleaned:
                    out.append(cleaned)
            continue
        # Body prose: regex-split on terminals.
        joined = " ".join(line.strip() for line in paragraph.splitlines())
        parts = _SENTENCE_TERMINATOR.split(joined)
        for part in parts:
            cleaned = _normalise(part)
            if cleaned:
                out.append(cleaned)
    return out


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _is_bullet_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(("- ", "* ", "+ "))


def _hash_sentence_id(proposal_id: UUID, section: str, index: int, content: str) -> str:
    """Deterministic ID — re-running the saga upserts onto the same rows.

    Includes the content hash so a writer revising the wording in a
    fresh run gets a fresh row instead of mismatching the old one.
    """

    digest = hashlib.sha1(
        f"{proposal_id}:{section}:{index}:{content}".encode()
    ).hexdigest()[:24]
    return f"saga-{section}-{index:04d}-{digest}"


def build_rows(
    *,
    proposal_id: UUID,
    agent_outputs: dict[str, AgentOutput],
    source: str = "ai-generated",
) -> list[SentenceRow]:
    """Turn the writer outputs into the rows we'll insert."""

    rows: list[SentenceRow] = []
    for agent_id, (section, content_key) in WRITER_SECTIONS.items():
        output = agent_outputs.get(agent_id)
        if output is None or output.status != "completed":
            continue
        markdown = str(output.output.get(content_key) or "").strip()
        if not markdown:
            continue
        sentences = split_into_sentences(markdown)
        if not sentences:
            continue
        llm_model = _extract_model(output.metadata)
        llm_tokens = _extract_tokens(output.tokens_used)
        for idx, sentence in enumerate(sentences):
            rows.append(
                SentenceRow(
                    section=section,
                    sentence_id=_hash_sentence_id(
                        proposal_id, section, idx, sentence
                    ),
                    content=sentence,
                    source=source,
                    agent_id=agent_id,
                    llm_model=llm_model,
                    llm_tokens=llm_tokens,
                )
            )
    return rows


def _extract_model(metadata: dict[str, Any]) -> str | None:
    """Pull the LLM model identifier from the writer's metadata.

    Writers stash the model under ``metadata.model`` (router fills it
    in). We accept ``llm_model`` as an alias for forward-compat with
    future agents.
    """

    for key in ("model", "llm_model"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_tokens(tokens_used: dict[str, int]) -> int | None:
    """Total tokens (input + output) for the writer call.

    ``llm_tokens`` on the provenance row records the *whole-section*
    spend, not per-sentence — that's all the auditor needs.
    """

    if not tokens_used:
        return None
    total = sum(int(v) for v in tokens_used.values() if isinstance(v, int))
    return total or None


async def record(
    conn: asyncpg.Connection,
    *,
    proposal_id: UUID,
    agent_outputs: dict[str, AgentOutput],
) -> int:
    """Best-effort upsert. Returns the number of rows touched."""

    try:
        return await _record_unsafe(conn, proposal_id, agent_outputs)
    except Exception:
        logger.exception(
            "provenance_recorder_unexpected_failure",
            extra={"proposal_id": str(proposal_id)},
        )
        return 0


async def _record_unsafe(
    conn: asyncpg.Connection,
    proposal_id: UUID,
    agent_outputs: dict[str, AgentOutput],
) -> int:
    rows = build_rows(proposal_id=proposal_id, agent_outputs=agent_outputs)
    if not rows:
        return 0

    await conn.executemany(
        """
        insert into proposal_provenance (
          proposal_id, section, sentence_id, content,
          source, agent_id, llm_model, llm_tokens
        ) values (
          $1, $2, $3, $4, $5, $6, $7, $8
        )
        on conflict (proposal_id, sentence_id) do update
          set content   = excluded.content,
              section   = excluded.section,
              source    = excluded.source,
              agent_id  = excluded.agent_id,
              llm_model = excluded.llm_model,
              llm_tokens = excluded.llm_tokens
        """,
        _row_tuples(proposal_id, rows),
    )
    logger.info(
        "saga_provenance_recorded",
        extra={
            "proposal_id": str(proposal_id),
            "row_count": len(rows),
            "sections": sorted({r.section for r in rows}),
        },
    )
    return len(rows)


def _row_tuples(
    proposal_id: UUID, rows: Sequence[SentenceRow]
) -> Iterable[tuple[Any, ...]]:
    for row in rows:
        yield (
            proposal_id,
            row.section,
            row.sentence_id,
            row.content,
            row.source,
            row.agent_id,
            row.llm_model,
            row.llm_tokens,
        )


__all__ = [
    "SentenceRow",
    "WRITER_SECTIONS",
    "build_rows",
    "record",
    "split_into_sentences",
]
