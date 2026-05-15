"""GuidelineIngestor — fetch funder PDF → chunk → embed → persist.

One ingest per (call_id, file_hash). Re-running with an unchanged PDF
is a fast no-op short-circuit: we sha256 the bytes, check the partial
unique index ``uq_funder_guidelines_call_hash``, and skip the whole
extract/chunk/embed pipeline.

Per migration 20260515120000:
  - ``funder_guidelines`` carries the parent doc row.
  - ``funder_guideline_chunks`` holds the per-chunk embeddings.

The ingestor is HTTP-injected (``httpx.AsyncClient``) so tests can
swap a :class:`httpx.MockTransport` in without monkey-patching, mirroring
the pattern used by the scrapers in :mod:`src.scrapers`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
import httpx
from pypdf.errors import PdfReadError

from src.rag.base import EMBEDDING_DIM, Embedder
from src.rag.chunker import chunk_text
from src.rag.corpus_manager import _vector_literal
from src.rag.pdf_extractor import PDFDocument, extract_pdf

logger = logging.getLogger(__name__)

# Per CLAUDE.md performance section: PDF download is bounded so a
# misbehaving funder host can't stall the worker.
_DEFAULT_TIMEOUT_S = 30.0
# Documents larger than this are almost always scanned (image-only) PDFs
# that pypdf can't extract text from anyway. Bail before downloading
# 100MB of nothing.
_MAX_PDF_BYTES = 64 * 1024 * 1024


class GuidelineIngestError(Exception):
    """Domain error raised when a guideline can't be ingested."""


@dataclass(frozen=True)
class IngestResult:
    """Summary of one ingest call, suitable for Celery task return values."""

    guideline_id: UUID
    """Row id in ``funder_guidelines``. Set for both fresh and cache-hit paths."""

    from_cache: bool
    """``True`` if the (call_id, file_hash) was already on disk; no LLM cost."""

    file_hash: str
    """sha256 hex of the downloaded PDF bytes."""

    chunk_count: int
    """Number of rows inserted into ``funder_guideline_chunks`` (0 on cache hit)."""

    page_count: int
    """Total pages in the PDF — for monitoring + admin dashboard."""

    section_count: int
    """Number of distinct sections detected (0 on cache hit)."""


class GuidelineIngestor:
    """Persistence-side service: download → extract → chunk → embed → insert.

    The ingestor is stateless across calls; build one per worker process
    or one per request, both are fine.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        embedder: Embedder,
        http_client: httpx.AsyncClient | None = None,
        max_pages: int = 300,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._http_client = http_client
        self._max_pages = max_pages

    async def ingest(
        self,
        *,
        source_url: str,
        programme_id: str | None,
        call_id: UUID | None,
        document_type: str = "call_guideline",
        title: str | None = None,
        language: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """Download, parse, chunk, embed, insert. Idempotent on re-run."""

        if not source_url:
            raise GuidelineIngestError("source_url is required")
        if programme_id is None and call_id is None:
            raise GuidelineIngestError("either programme_id or call_id must be provided")

        pdf_bytes = await self._download(source_url)
        file_hash = hashlib.sha256(pdf_bytes).hexdigest()

        existing = await self._find_existing(
            call_id=call_id, source_url=source_url, file_hash=file_hash
        )
        if existing is not None:
            logger.info(
                "guideline_ingest_cache_hit",
                extra={
                    "guideline_id": str(existing.guideline_id),
                    "file_hash": file_hash,
                    "call_id": str(call_id) if call_id else None,
                },
            )
            return IngestResult(
                guideline_id=existing.guideline_id,
                from_cache=True,
                file_hash=file_hash,
                chunk_count=0,
                page_count=existing.page_count,
                section_count=0,
            )

        try:
            doc = extract_pdf(pdf_bytes, max_pages=self._max_pages)
        except PdfReadError as exc:
            raise GuidelineIngestError(f"pdf_parse_failed: {exc}") from exc

        if not doc.sections:
            raise GuidelineIngestError(
                f"pdf has no extractable text ({doc.page_count} pages, "
                "likely image-only/scanned)"
            )

        # Embed every chunk in one batch — fewer round-trips and lets
        # the embedder de-duplicate identical chunk content.
        all_chunks_per_section: list[tuple[str, list[Any]]] = []
        total_chunks = 0
        flat_texts: list[str] = []
        for section in doc.sections:
            section_chunks = chunk_text(section.text, section=section.title)
            if not section_chunks:
                continue
            all_chunks_per_section.append((section.title, section_chunks))
            flat_texts.extend(c.content for c in section_chunks)
            total_chunks += len(section_chunks)

        if total_chunks == 0:
            raise GuidelineIngestError("no chunks produced — pdf body too short")

        embeddings = await self._embedder.embed_batch(flat_texts)
        if len(embeddings) != total_chunks:
            raise GuidelineIngestError(
                f"embedder returned {len(embeddings)} vectors for " f"{total_chunks} chunks"
            )
        # Sanity-check vector shape — every embedder we wire returns
        # 3072-d, but a misconfigured fallback would silently corrupt
        # the corpus.
        if embeddings and len(embeddings[0]) != EMBEDDING_DIM:
            raise GuidelineIngestError(
                f"embedder produced wrong-dim vectors: " f"{len(embeddings[0])} != {EMBEDDING_DIM}"
            )

        guideline_id = await self._persist(
            source_url=source_url,
            programme_id=programme_id,
            call_id=call_id,
            document_type=document_type,
            title=title or _derive_title(doc, source_url),
            language=language,
            file_hash=file_hash,
            byte_size=len(pdf_bytes),
            page_count=doc.page_count,
            metadata=metadata or {},
            sections_with_chunks=all_chunks_per_section,
            embeddings=embeddings,
        )

        logger.info(
            "guideline_ingested",
            extra={
                "guideline_id": str(guideline_id),
                "file_hash": file_hash,
                "page_count": doc.page_count,
                "section_count": len(all_chunks_per_section),
                "chunk_count": total_chunks,
                "call_id": str(call_id) if call_id else None,
            },
        )
        return IngestResult(
            guideline_id=guideline_id,
            from_cache=False,
            file_hash=file_hash,
            chunk_count=total_chunks,
            page_count=doc.page_count,
            section_count=len(all_chunks_per_section),
        )

    # ── helpers ──────────────────────────────────────────────────────

    async def _download(self, url: str) -> bytes:
        """Fetch PDF bytes; raise GuidelineIngestError on HTTP failure."""

        client = self._http_client
        owns_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S, follow_redirects=True)
            owns_client = True
        try:
            response = await client.get(url, timeout=_DEFAULT_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise GuidelineIngestError(f"pdf_download_failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise GuidelineIngestError(f"pdf_download_status_{response.status_code}: {url}")
        body = response.content
        if len(body) > _MAX_PDF_BYTES:
            raise GuidelineIngestError(f"pdf too large: {len(body)} bytes > {_MAX_PDF_BYTES}")
        return body

    async def _find_existing(
        self,
        *,
        call_id: UUID | None,
        source_url: str,
        file_hash: str,
    ) -> _ExistingGuideline | None:
        """Match against either partial unique index — call-scoped or
        programme-scoped — depending on whether ``call_id`` is set.
        """

        async with self._pool.acquire() as conn:
            if call_id is not None:
                row = await conn.fetchrow(
                    "SELECT id, page_count FROM funder_guidelines "
                    "WHERE call_id = $1 AND file_hash = $2",
                    call_id,
                    file_hash,
                )
            else:
                row = await conn.fetchrow(
                    "SELECT id, page_count FROM funder_guidelines "
                    "WHERE call_id IS NULL AND source_url = $1 AND file_hash = $2",
                    source_url,
                    file_hash,
                )
        if row is None:
            return None
        return _ExistingGuideline(
            guideline_id=UUID(str(row["id"])),
            page_count=int(row["page_count"] or 0),
        )

    async def _persist(
        self,
        *,
        source_url: str,
        programme_id: str | None,
        call_id: UUID | None,
        document_type: str,
        title: str,
        language: str | None,
        file_hash: str,
        byte_size: int,
        page_count: int,
        metadata: dict[str, Any],
        sections_with_chunks: list[tuple[str, list[Any]]],
        embeddings: list[list[float]],
    ) -> UUID:
        meta_json = json.dumps(metadata)

        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO funder_guidelines
                  (programme_id, document_type, title, content, source_url,
                   call_id, file_hash, page_count, language, byte_size, metadata)
                VALUES
                  ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                RETURNING id
                """,
                programme_id,
                document_type,
                title,
                # The legacy `content` column is NOT NULL in migration 007.
                # We stuff a short marker — the real chunk text lives
                # downstairs.
                "(chunked)",
                source_url,
                call_id,
                file_hash,
                page_count,
                language,
                byte_size,
                meta_json,
            )
            guideline_id = UUID(str(row["id"]))

            insert_rows: list[tuple[Any, ...]] = []
            embedding_iter = iter(embeddings)
            for section_title, chunks in sections_with_chunks:
                for chunk in chunks:
                    embedding = next(embedding_iter)
                    insert_rows.append(
                        (
                            guideline_id,
                            section_title,
                            chunk.chunk_index,
                            chunk.content,
                            _vector_literal(embedding),
                            json.dumps(chunk.metadata),
                            int(chunk.metadata.get("token_count", 0)),
                        )
                    )

            await conn.executemany(
                """
                INSERT INTO funder_guideline_chunks
                  (guideline_id, section, chunk_index, content,
                   embedding, metadata, token_count)
                VALUES ($1, $2, $3, $4, $5::vector(3072), $6::jsonb, $7)
                """,
                insert_rows,
            )

            return guideline_id


@dataclass(frozen=True)
class _ExistingGuideline:
    guideline_id: UUID
    page_count: int


def _derive_title(doc: PDFDocument, fallback: str) -> str:
    """Pick a sensible title when the caller didn't supply one."""

    for section in doc.sections:
        if section.title and section.title != "body":
            return section.title[:200]
    return fallback.rsplit("/", 1)[-1][:200]


__all__ = ["GuidelineIngestError", "GuidelineIngestor", "IngestResult"]
