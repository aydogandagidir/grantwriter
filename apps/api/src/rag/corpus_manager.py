"""CorpusManager — adds successful proposals to the RAG corpus.

One ingest per proposal: chunk each section, embed all chunks in batch,
insert one ``successful_proposals_corpus`` row + N
``successful_proposal_chunks`` rows, all in a single transaction so a
half-ingested proposal never poisons retrieval.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from src.rag.base import EMBEDDING_DIM, Embedder
from src.rag.chunker import chunk_text

logger = logging.getLogger(__name__)


def _vector_literal(vector: list[float]) -> str:
    """Serialize a float vector for asyncpg + pgvector.

    pgvector accepts the textual ``'[v1,v2,...]'`` form as input to a
    ``vector(N)`` cast, so we send strings rather than registering a
    custom asyncpg codec (avoids a pgvector-python dep).
    """

    if len(vector) != EMBEDDING_DIM:
        raise ValueError(f"vector length {len(vector)} does not match expected {EMBEDDING_DIM}")
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


class CorpusManager:
    def __init__(self, *, pool: asyncpg.Pool, embedder: Embedder) -> None:
        self._pool = pool
        self._embedder = embedder

    async def add_proposal(
        self,
        *,
        programme_id: str,
        source: str,
        external_id: str | None = None,
        title: str | None = None,
        topic_id: str | None = None,
        funded_year: int | None = None,
        budget_eur: float | None = None,
        sections: dict[str, str],
        metadata: dict[str, Any] | None = None,
    ) -> UUID:
        """Ingest one successful proposal — chunked + embedded + persisted.

        Returns the ``successful_proposals_corpus.id`` of the new row.
        Empty sections are skipped (no chunks emitted), but the corpus
        row is created either way so admin tools can see the ingest.
        """

        meta_json = json.dumps(metadata or {})

        async with self._pool.acquire() as conn, conn.transaction():
            corpus_id_raw = await conn.fetchval(
                """
                INSERT INTO successful_proposals_corpus
                  (programme_id, source, external_id, title, topic_id,
                   funded_year, budget_eur, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                RETURNING id
                """,
                programme_id,
                source,
                external_id,
                title,
                topic_id,
                funded_year,
                budget_eur,
                meta_json,
            )
            corpus_id = UUID(str(corpus_id_raw))

            for section, body in sections.items():
                if not body or not body.strip():
                    continue
                chunks = chunk_text(body, section=section)
                if not chunks:
                    continue
                embeddings = await self._embedder.embed_batch([c.content for c in chunks])
                if len(embeddings) != len(chunks):
                    raise RuntimeError(
                        "embedder returned wrong number of vectors "
                        f"({len(embeddings)} for {len(chunks)} chunks)"
                    )

                rows = [
                    (
                        corpus_id,
                        chunk.section,
                        chunk.chunk_index,
                        chunk.content,
                        _vector_literal(embedding),
                        json.dumps(chunk.metadata),
                    )
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ]
                await conn.executemany(
                    """
                    INSERT INTO successful_proposal_chunks
                      (corpus_id, section, chunk_index, content, embedding, metadata)
                    VALUES ($1, $2, $3, $4, $5::vector(3072), $6::jsonb)
                    """,
                    rows,
                )

            logger.info(
                "corpus_proposal_ingested",
                extra={
                    "corpus_id": str(corpus_id),
                    "programme_id": programme_id,
                    "source": source,
                    "title": title,
                    "section_count": sum(1 for body in sections.values() if body.strip()),
                },
            )
            return corpus_id


__all__ = ["CorpusManager"]
