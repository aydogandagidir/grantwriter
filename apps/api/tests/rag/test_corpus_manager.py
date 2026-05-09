"""CorpusManager DB integration tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
from src.rag.corpus_manager import CorpusManager, _vector_literal
from src.rag.embedder import DeterministicEmbedder


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound RAG tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


def test_vector_literal_format() -> None:
    v = [0.1, 0.2, 0.3] + [0.0] * 3069
    s = _vector_literal(v)
    assert s.startswith("[0.1000000,0.2000000,0.3000000")
    assert s.endswith("]")


def test_vector_literal_validates_dim() -> None:
    with pytest.raises(ValueError):
        _vector_literal([0.1] * 1536)


async def test_add_proposal_inserts_corpus_and_chunks(pool: asyncpg.Pool) -> None:
    embedder = DeterministicEmbedder(seed_namespace="corpus_mgr_test")
    manager = CorpusManager(pool=pool, embedder=embedder)

    sections = {
        "excellence": (
            "## 1.1 Objectives\n\nA detailed objective.\n\n" "## 1.2 Methodology\n\nSome method.\n"
        ),
        "impact": "## 2.1 Pathway\n\nMeaningful impact.\n",
        "implementation": "## 3.1 Work plan\n\nDeliverable.\n",
    }
    corpus_id = await manager.add_proposal(
        programme_id="horizon_eu_ria",
        source="test_fixture",
        external_id="TEST-CM-001",
        title="Test Proposal",
        topic_id="HORIZON-TEST-01",
        funded_year=2024,
        budget_eur=1_000_000,
        sections=sections,
        metadata={"placeholder": True},
    )

    try:
        async with pool.acquire() as conn:
            corpus_row = await conn.fetchrow(
                "SELECT * FROM successful_proposals_corpus WHERE id = $1", corpus_id
            )
            assert corpus_row is not None
            assert corpus_row["title"] == "Test Proposal"
            assert corpus_row["funded_year"] == 2024

            chunks = await conn.fetch(
                "SELECT * FROM successful_proposal_chunks WHERE corpus_id = $1 "
                "ORDER BY section, chunk_index",
                corpus_id,
            )
            assert len(chunks) == 3  # one chunk per section for the small input
            sections_seen = {c["section"] for c in chunks}
            assert sections_seen == {"excellence", "impact", "implementation"}
            for c in chunks:
                # Embedding column non-null (pgvector strings start with '[').
                assert c["embedding"] is not None
                assert str(c["embedding"]).startswith("[")
    finally:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM successful_proposal_chunks WHERE corpus_id = $1", corpus_id
            )
            await conn.execute("DELETE FROM successful_proposals_corpus WHERE id = $1", corpus_id)


async def test_add_proposal_skips_empty_sections(pool: asyncpg.Pool) -> None:
    embedder = DeterministicEmbedder(seed_namespace="corpus_mgr_test")
    manager = CorpusManager(pool=pool, embedder=embedder)
    corpus_id = await manager.add_proposal(
        programme_id="horizon_eu_ria",
        source="test_fixture",
        external_id="TEST-CM-002",
        title="Empty Sections",
        sections={"excellence": "## 1.1\n\nReal content.\n", "impact": ""},
    )
    try:
        async with pool.acquire() as conn:
            chunks = await conn.fetch(
                "SELECT section FROM successful_proposal_chunks WHERE corpus_id = $1",
                corpus_id,
            )
            sections = [c["section"] for c in chunks]
            assert "impact" not in sections
            assert "excellence" in sections
    finally:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM successful_proposal_chunks WHERE corpus_id = $1", corpus_id
            )
            await conn.execute("DELETE FROM successful_proposals_corpus WHERE id = $1", corpus_id)
