"""Chunker unit tests — section-aware, paragraph-respecting, overlapping."""

from __future__ import annotations

from itertools import pairwise

from src.rag.chunker import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_OVERLAP_TOKENS,
    DEFAULT_TARGET_TOKENS,
    chunk_text,
    count_tokens,
)


def _word_paragraph(words: int) -> str:
    """Build a paragraph of ``words`` short tokens (~1 token each)."""

    return " ".join(["alpha"] * words)


def test_empty_input_yields_no_chunks() -> None:
    assert chunk_text("", section="excellence") == []
    assert chunk_text("   \n\n   \n\n", section="excellence") == []


def test_single_short_paragraph_yields_one_chunk() -> None:
    chunks = chunk_text("Just one paragraph.", section="excellence")
    assert len(chunks) == 1
    assert chunks[0].section == "excellence"
    assert chunks[0].chunk_index == 0
    assert chunks[0].content == "Just one paragraph."
    assert chunks[0].metadata["paragraph_count"] == 1


def test_paragraph_count_tokens_uses_tiktoken() -> None:
    """count_tokens should match a known tiktoken result for a short string."""

    assert count_tokens("hello world") >= 1
    # 'alpha' encodes to a single token under cl100k_base.
    assert count_tokens(_word_paragraph(50)) == 50


def test_chunks_respect_max_token_budget() -> None:
    # 4 paragraphs of ~500 tokens each → must split into multiple chunks.
    paragraphs = "\n\n".join(_word_paragraph(500) for _ in range(4))
    chunks = chunk_text(paragraphs, section="impact")
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.metadata["token_count"] <= DEFAULT_MAX_TOKENS


def test_chunks_carry_overlap_tail() -> None:
    """The first paragraph of chunk N+1 should equal the last paragraph
    of chunk N (within the overlap budget)."""

    paragraphs = [_word_paragraph(450) for _ in range(5)]
    chunks = chunk_text("\n\n".join(paragraphs), section="excellence")
    assert len(chunks) >= 2
    # The overlap design uses whole paragraphs only — verify continuity.
    for prev, nxt in pairwise(chunks):
        prev_paras = prev.content.split("\n\n")
        next_paras = nxt.content.split("\n\n")
        assert next_paras[0] == prev_paras[-1], "overlap tail not preserved"


def test_oversized_paragraph_emits_standalone_chunk() -> None:
    """A single paragraph exceeding max_tokens is emitted whole rather
    than being split mid-sentence."""

    huge = _word_paragraph(DEFAULT_MAX_TOKENS + 200)
    chunks = chunk_text(huge, section="excellence")
    assert len(chunks) == 1
    assert chunks[0].metadata["oversized"] is True


def test_default_overlap_is_under_max() -> None:
    assert DEFAULT_OVERLAP_TOKENS < DEFAULT_MAX_TOKENS
    assert DEFAULT_TARGET_TOKENS <= DEFAULT_MAX_TOKENS


def test_section_metadata_is_preserved() -> None:
    chunks = chunk_text(
        "Para one.\n\nPara two.",
        section="implementation",
        metadata={"corpus_source": "EC_publications"},
    )
    for c in chunks:
        assert c.section == "implementation"
        assert c.metadata.get("corpus_source") == "EC_publications"
