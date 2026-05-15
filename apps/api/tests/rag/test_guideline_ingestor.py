"""GuidelineIngestor DB integration tests.

Live Postgres + pgvector required (``TEST_DATABASE_URL``). The PDF
parser itself has its own unit tests in ``test_pdf_extractor.py``; here
we monkey-patch :func:`src.rag.guideline_ingestor.extract_pdf` so we
can drive the ingestor with a known :class:`PDFDocument` and exercise
the persistence + idempotency + retriever round-trip layers without
depending on real PDF byte fixtures.

HTTP is injected via :class:`httpx.MockTransport` — same pattern the
scraper unit tests use.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from src.rag.embedder import DeterministicEmbedder
from src.rag.guideline_ingestor import GuidelineIngestError, GuidelineIngestor
from src.rag.pdf_extractor import PDFDocument, PDFPage, PDFSection
from src.rag.retriever import CorpusRetriever


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound RAG tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    p = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert p is not None
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def programme_id(pool: asyncpg.Pool) -> AsyncIterator[str]:
    """Reserve a stable programme_id and clean up rows we created against it."""

    pid = "horizon_eu_ria"
    yield pid
    # Cascade-delete via funder_guidelines FK on guideline_chunks.
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM funder_guidelines WHERE source_url LIKE 'test://%' "
            "OR title LIKE 'GuidelineIngestorTest%'"
        )


async def _seed_call(pool: asyncpg.Pool, *, programme_id: str) -> UUID:
    """Create a calls-row to FK against, return its id. Caller cleans up."""

    async with pool.acquire() as conn:
        cid = await conn.fetchval(
            """
            INSERT INTO calls (
              programme_id, source, external_id, title, language,
              call_text, call_url, deadline,
              budget_total_eur, trl_min, trl_max,
              topic_keywords, raw_metadata, status
            ) VALUES (
              $1, 'manual', $2, $3, 'en',
              'Test call body', 'https://test/url', '2099-01-01',
              1000000, 4, 6,
              ARRAY['test']::text[], '{}'::jsonb, 'open'
            ) RETURNING id
            """,
            programme_id,
            f"guideline-test-{uuid4().hex[:12]}",
            "Guideline test call",
        )
    return UUID(str(cid))


async def _cleanup_call(pool: asyncpg.Pool, call_id: UUID) -> None:
    async with pool.acquire() as conn:
        # FK on funder_guidelines.call_id has ON DELETE CASCADE → chunks
        # disappear automatically.
        await conn.execute("DELETE FROM calls WHERE id = $1", call_id)


def _build_pdf_doc(*, body: str = "Default body text.") -> PDFDocument:
    """Synthesise a PDFDocument that the ingestor's chunker will accept."""

    pages = [PDFPage(index=0, text=body, is_blank=False)]
    sections = [PDFSection(title="EVALUATION CRITERIA", text=body, page_start=0, page_end=0)]
    return PDFDocument(
        page_count=1,
        pages=pages,
        sections=sections,
        full_text=body,
        truncated=False,
    )


def _mock_transport(*, status: int = 200, body: bytes = b"%PDF-mock") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


# ── happy path ──────────────────────────────────────────────────────


async def test_ingest_persists_guideline_and_chunks(pool: asyncpg.Pool, programme_id: str) -> None:
    call_id = await _seed_call(pool, programme_id=programme_id)
    try:
        embedder = DeterministicEmbedder(seed_namespace="ingest_happy")
        transport = _mock_transport(body=b"%PDF-fake-bytes-1")
        http = httpx.AsyncClient(transport=transport)
        ingestor = GuidelineIngestor(pool=pool, embedder=embedder, http_client=http)

        with patch(
            "src.rag.guideline_ingestor.extract_pdf",
            return_value=_build_pdf_doc(body="The evaluation criteria are X, Y, Z."),
        ):
            result = await ingestor.ingest(
                source_url="test://he-ria/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
                title="GuidelineIngestorTest happy",
            )
        await http.aclose()

        assert result.from_cache is False
        assert result.chunk_count >= 1
        assert result.page_count == 1
        assert result.section_count == 1
        assert result.file_hash, "expected sha256 hex"

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM funder_guidelines WHERE id = $1", result.guideline_id
            )
            assert row is not None
            assert row["call_id"] == call_id
            assert row["programme_id"] == programme_id
            assert row["title"] == "GuidelineIngestorTest happy"
            assert row["file_hash"] == result.file_hash
            assert row["page_count"] == 1

            chunks = await conn.fetch(
                "SELECT section, content, embedding FROM funder_guideline_chunks "
                "WHERE guideline_id = $1 ORDER BY chunk_index",
                result.guideline_id,
            )
            assert len(chunks) == result.chunk_count
            assert all(c["embedding"] is not None for c in chunks)
            assert all(c["section"] == "EVALUATION CRITERIA" for c in chunks)
    finally:
        await _cleanup_call(pool, call_id)


# ── idempotency ─────────────────────────────────────────────────────


async def test_ingest_is_idempotent_on_unchanged_pdf(pool: asyncpg.Pool, programme_id: str) -> None:
    """Same call_id + same PDF bytes → cache hit, no second insert."""

    call_id = await _seed_call(pool, programme_id=programme_id)
    try:
        embedder = DeterministicEmbedder(seed_namespace="ingest_idem")
        # Same body bytes → same sha256 → cache hit on second run.
        http = httpx.AsyncClient(transport=_mock_transport(body=b"%PDF-stable"))
        ingestor = GuidelineIngestor(pool=pool, embedder=embedder, http_client=http)

        with patch(
            "src.rag.guideline_ingestor.extract_pdf",
            return_value=_build_pdf_doc(body="Stable body content."),
        ):
            first = await ingestor.ingest(
                source_url="test://idem/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
                title="GuidelineIngestorTest idem",
            )
            second = await ingestor.ingest(
                source_url="test://idem/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
                title="GuidelineIngestorTest idem",
            )
        await http.aclose()

        assert first.from_cache is False
        assert second.from_cache is True
        assert first.guideline_id == second.guideline_id
        assert first.file_hash == second.file_hash
        # Second pass produced zero new chunks.
        assert second.chunk_count == 0

        async with pool.acquire() as conn:
            row_count = await conn.fetchval(
                "SELECT COUNT(*) FROM funder_guidelines WHERE call_id = $1", call_id
            )
            assert row_count == 1
    finally:
        await _cleanup_call(pool, call_id)


async def test_ingest_creates_new_row_when_pdf_bytes_change(
    pool: asyncpg.Pool, programme_id: str
) -> None:
    """Different bytes at the same URL → fresh sha256 → fresh row."""

    call_id = await _seed_call(pool, programme_id=programme_id)
    try:
        embedder = DeterministicEmbedder(seed_namespace="ingest_change")

        http_v1 = httpx.AsyncClient(transport=_mock_transport(body=b"%PDF-version-1"))
        http_v2 = httpx.AsyncClient(transport=_mock_transport(body=b"%PDF-version-2"))

        with patch(
            "src.rag.guideline_ingestor.extract_pdf",
            return_value=_build_pdf_doc(body="Version-changing body."),
        ):
            ingestor_v1 = GuidelineIngestor(pool=pool, embedder=embedder, http_client=http_v1)
            first = await ingestor_v1.ingest(
                source_url="test://change/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
                title="GuidelineIngestorTest change",
            )
            ingestor_v2 = GuidelineIngestor(pool=pool, embedder=embedder, http_client=http_v2)
            second = await ingestor_v2.ingest(
                source_url="test://change/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
                title="GuidelineIngestorTest change",
            )

        await http_v1.aclose()
        await http_v2.aclose()

        assert first.from_cache is False
        assert second.from_cache is False
        assert first.file_hash != second.file_hash
        assert first.guideline_id != second.guideline_id

        async with pool.acquire() as conn:
            row_count = await conn.fetchval(
                "SELECT COUNT(*) FROM funder_guidelines WHERE call_id = $1", call_id
            )
            assert row_count == 2
    finally:
        await _cleanup_call(pool, call_id)


# ── error paths ─────────────────────────────────────────────────────


async def test_ingest_raises_on_http_404(pool: asyncpg.Pool, programme_id: str) -> None:
    call_id = await _seed_call(pool, programme_id=programme_id)
    try:
        embedder = DeterministicEmbedder(seed_namespace="ingest_404")
        http = httpx.AsyncClient(transport=_mock_transport(status=404, body=b"not found"))
        ingestor = GuidelineIngestor(pool=pool, embedder=embedder, http_client=http)

        with pytest.raises(GuidelineIngestError, match="status_404"):
            await ingestor.ingest(
                source_url="test://missing/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
            )
        await http.aclose()
    finally:
        await _cleanup_call(pool, call_id)


async def test_ingest_raises_when_pdf_has_no_extractable_text(
    pool: asyncpg.Pool, programme_id: str
) -> None:
    call_id = await _seed_call(pool, programme_id=programme_id)
    try:
        embedder = DeterministicEmbedder(seed_namespace="ingest_empty")
        http = httpx.AsyncClient(transport=_mock_transport(body=b"%PDF-empty"))
        ingestor = GuidelineIngestor(pool=pool, embedder=embedder, http_client=http)

        empty_doc = PDFDocument(page_count=3, pages=[], sections=[], full_text="", truncated=False)
        with (
            patch(
                "src.rag.guideline_ingestor.extract_pdf",
                return_value=empty_doc,
            ),
            pytest.raises(GuidelineIngestError, match="no extractable text"),
        ):
            await ingestor.ingest(
                source_url="test://empty/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
            )
        await http.aclose()
    finally:
        await _cleanup_call(pool, call_id)


async def test_ingest_requires_programme_or_call(
    pool: asyncpg.Pool,
) -> None:
    embedder = DeterministicEmbedder(seed_namespace="ingest_args")
    ingestor = GuidelineIngestor(pool=pool, embedder=embedder)
    with pytest.raises(GuidelineIngestError, match="programme_id or call_id"):
        await ingestor.ingest(
            source_url="test://needs-anchor/guideline.pdf",
            programme_id=None,
            call_id=None,
        )


# ── retriever round-trip ────────────────────────────────────────────


async def test_retrieve_guideline_returns_ingested_chunks(
    pool: asyncpg.Pool, programme_id: str
) -> None:
    """Ingest → retrieve_guideline by call_id returns the inserted chunks."""

    call_id = await _seed_call(pool, programme_id=programme_id)
    try:
        # Shared embedder so the query maps onto the same vector space
        # as the inserted chunks (DeterministicEmbedder is namespace-
        # keyed, so identical text → identical vector).
        embedder = DeterministicEmbedder(seed_namespace="ingest_retrieve")
        http = httpx.AsyncClient(transport=_mock_transport(body=b"%PDF-retrieve"))
        ingestor = GuidelineIngestor(pool=pool, embedder=embedder, http_client=http)
        body = "The funder requires bilingual reporting and ethics approval."
        with patch(
            "src.rag.guideline_ingestor.extract_pdf",
            return_value=_build_pdf_doc(body=body),
        ):
            result = await ingestor.ingest(
                source_url="test://retrieve/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
                title="GuidelineIngestorTest retrieve",
            )
        await http.aclose()

        retriever = CorpusRetriever(pool=pool, embedder=embedder, router=None)
        chunks = await retriever.retrieve_guideline(
            body,
            call_id=call_id,
            programme_id=programme_id,
            top_k=3,
            rerank=False,
        )
        assert len(chunks) >= 1
        assert all(c.content for c in chunks)
        assert any(c.corpus_id == result.guideline_id for c in chunks)
        # The exact-text query against DeterministicEmbedder collides
        # with itself → cosine ~ 1.0.
        assert chunks[0].score > 0.9
    finally:
        await _cleanup_call(pool, call_id)


async def test_retrieve_guideline_requires_anchor(
    pool: asyncpg.Pool,
) -> None:
    embedder = DeterministicEmbedder(seed_namespace="ingest_no_anchor")
    retriever = CorpusRetriever(pool=pool, embedder=embedder)
    with pytest.raises(ValueError, match="call_id or programme_id"):
        await retriever.retrieve_guideline("query", top_k=3)


# ── miscellaneous ──────────────────────────────────────────────────


async def test_ingest_normalises_default_title_from_section(
    pool: asyncpg.Pool, programme_id: str
) -> None:
    """When no title is supplied, the ingestor picks the first non-body section."""

    call_id = await _seed_call(pool, programme_id=programme_id)
    try:
        embedder = DeterministicEmbedder(seed_namespace="ingest_title")
        http = httpx.AsyncClient(transport=_mock_transport(body=b"%PDF-title"))
        ingestor = GuidelineIngestor(pool=pool, embedder=embedder, http_client=http)
        with patch(
            "src.rag.guideline_ingestor.extract_pdf",
            return_value=_build_pdf_doc(body="Body for title-derivation."),
        ):
            result = await ingestor.ingest(
                source_url="test://title-default/guideline.pdf",
                programme_id=programme_id,
                call_id=call_id,
            )
        await http.aclose()

        async with pool.acquire() as conn:
            title: Any = await conn.fetchval(
                "SELECT title FROM funder_guidelines WHERE id = $1",
                result.guideline_id,
            )
        # The mocked PDFDocument exposes "EVALUATION CRITERIA" as the
        # first non-body section title.
        assert title == "EVALUATION CRITERIA"
    finally:
        await _cleanup_call(pool, call_id)
