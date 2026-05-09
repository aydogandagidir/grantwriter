"""Shared types for the RAG subsystem.

The :class:`Embedder` protocol is the seam every other RAG component
goes through. Production wires :class:`~src.rag.embedder.OpenAIEmbedder`;
tests / offline seed runs wire
:class:`~src.rag.embedder.DeterministicEmbedder`.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 3072 = text-embedding-3-large native dimension. Locked here so a
# misconfigured embedder fails loudly rather than silently inserting
# wrong-shaped vectors.
EMBEDDING_DIM: Literal[3072] = 3072


class Chunk(BaseModel):
    """A pre-embedding text chunk produced by the chunker."""

    model_config = ConfigDict(frozen=True)

    content: str
    section: str
    chunk_index: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedChunk(BaseModel):
    """A chunk returned by the retriever, with its similarity score."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    corpus_id: UUID
    section: str
    content: str
    score: float
    """Cosine similarity (1.0 = identical, 0.0 = orthogonal)."""
    title: str | None = None
    topic_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Embedder(Protocol):
    """Async embedder interface — single + batch methods."""

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


__all__ = ["Chunk", "EMBEDDING_DIM", "Embedder", "RetrievedChunk"]
