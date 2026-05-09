"""Section-aware semantic chunker.

Per docs/04 §2.3: target 800–1200 tokens, 200-token overlap, paragraph
boundaries respected, section tag attached to every emitted chunk.
Tokenisation uses tiktoken with the encoder for the embedding model
(``cl100k_base`` for ``text-embedding-3-large``); the heuristic
"4 chars / token" fallback is unreliable enough to ruin retrieval
quality, so the dep is non-optional.
"""

from __future__ import annotations

from typing import Any

import tiktoken

from src.rag.base import Chunk

DEFAULT_TARGET_TOKENS = 1000
DEFAULT_MAX_TOKENS = 1200
DEFAULT_OVERLAP_TOKENS = 200
# Encoder used by text-embedding-3-large + GPT-4 family.
_ENCODING_NAME = "cl100k_base"
_encoder = tiktoken.get_encoding(_ENCODING_NAME)


def count_tokens(text: str) -> int:
    """Return the tiktoken token count for ``text`` under cl100k_base."""

    if not text:
        return 0
    return len(_encoder.encode(text, disallowed_special=()))


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; trim and drop empties."""

    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _overlap_tail(paragraphs: list[str], overlap_tokens: int) -> tuple[list[str], int]:
    """Return the trailing paragraphs that sum to ~``overlap_tokens``.

    Walks backwards through ``paragraphs``; takes whole paragraphs only
    so the tail doesn't end mid-sentence.
    """

    if overlap_tokens <= 0 or not paragraphs:
        return [], 0
    tail: list[str] = []
    total = 0
    for para in reversed(paragraphs):
        if total >= overlap_tokens:
            break
        tail.insert(0, para)
        total += count_tokens(para)
    return tail, total


def chunk_text(
    text: str,
    *,
    section: str,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    """Chunk ``text`` for one section.

    Algorithm:
      1. Split into paragraphs by blank lines.
      2. Accumulate paragraphs until adding the next would exceed
         ``max_tokens``; emit a chunk and start the next one with an
         overlap tail of the last paragraph(s).
      3. If a *single* paragraph exceeds ``max_tokens`` (rare in real
         proposals), emit it as its own chunk — splitting mid-paragraph
         hurts retrieval quality more than an oversized chunk does.

    Empty input → empty list. The caller decides whether to insert
    nothing or to flag a missing-section warning.
    """

    if max_tokens < target_tokens:
        raise ValueError("max_tokens must be >= target_tokens")
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be < max_tokens")
    metadata = dict(metadata or {})

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[Chunk] = []
    current_paras: list[str] = []
    current_tokens = 0
    chunk_index = 0

    def _emit() -> None:
        nonlocal current_paras, current_tokens, chunk_index
        if not current_paras:
            return
        body = "\n\n".join(current_paras)
        chunks.append(
            Chunk(
                content=body,
                section=section,
                chunk_index=chunk_index,
                metadata={
                    **metadata,
                    "paragraph_count": len(current_paras),
                    "token_count": current_tokens,
                },
            )
        )
        chunk_index += 1
        current_paras, current_tokens = _overlap_tail(current_paras, overlap_tokens)

    for para in paragraphs:
        para_tokens = count_tokens(para)

        # A single oversized paragraph: emit any pending chunk first,
        # then this paragraph standalone (no overlap tail to start with).
        if para_tokens > max_tokens:
            _emit()
            chunks.append(
                Chunk(
                    content=para,
                    section=section,
                    chunk_index=chunk_index,
                    metadata={
                        **metadata,
                        "paragraph_count": 1,
                        "token_count": para_tokens,
                        "oversized": True,
                    },
                )
            )
            chunk_index += 1
            current_paras = []
            current_tokens = 0
            continue

        if current_tokens + para_tokens > max_tokens and current_paras:
            _emit()

        current_paras.append(para)
        current_tokens += para_tokens

        if current_tokens >= target_tokens and current_tokens >= max_tokens - 50:
            # Close to the cap; emit before another paragraph pushes us over.
            _emit()

    _emit()
    return chunks


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_OVERLAP_TOKENS",
    "DEFAULT_TARGET_TOKENS",
    "chunk_text",
    "count_tokens",
]
