"""End-to-end retriever tests against a real Postgres + pgvector.

Skip when ``TEST_DATABASE_URL`` is unset. The expected fixture is a
fresh pgvector container with the migrations + auth_stub applied.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from src.rag.corpus_manager import CorpusManager
from src.rag.embedder import DeterministicEmbedder
from src.rag.retriever import CorpusRetriever

from tests.llm.conftest import FakeProvider, build_router, make_response


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


@pytest.fixture
async def seeded_corpus(pool: asyncpg.Pool) -> AsyncIterator[dict[str, object]]:
    """Insert a small, deterministic corpus of HE proposals for the test
    to query against. Cleans up its own rows on teardown so it can run
    alongside the security suite without colliding."""

    embedder = DeterministicEmbedder(seed_namespace="rag_test")
    manager = CorpusManager(pool=pool, embedder=embedder)

    proposals = [
        {
            "title": "Digital Twin Pilot",
            "external_id": "HE-DT-001",
            "topic_id": "HORIZON-CL4-DT-01",
            "sections": {
                "excellence": (
                    "## 1.1 Objectives\n\nFederated digital twins for "
                    "manufacturing.\n\n"
                    "## 1.2 Methodology\n\n"
                    "OPC UA over TSN edge nodes.\n"
                ),
                "impact": "## 2.1 Pathways\n\nDowntime reduction.\n",
                "implementation": "## 3.1 Work plan\n\nSix WPs.\n",
            },
        },
        {
            "title": "Battery Recycling",
            "external_id": "HE-BR-002",
            "topic_id": "HORIZON-CL5-CE-01",
            "sections": {
                "excellence": (
                    "## 1.1 Objectives\n\nLithium recovery via "
                    "hydrometallurgy.\n\n"
                    "## 1.2 Methodology\n\n"
                    "Solvent extraction tuned to NMC/LFP feedstock.\n"
                ),
                "impact": "## 2.1 Pathways\n\nRaw material independence.\n",
                "implementation": "## 3.1 Work plan\n\nFive WPs.\n",
            },
        },
        {
            "title": "Trustworthy AI for QC",
            "external_id": "HE-AI-003",
            "topic_id": "HORIZON-CL4-AI-01",
            "sections": {
                "excellence": (
                    "## 1.1 Objectives\n\nFormally verified CV models for "
                    "industrial QC.\n\n"
                    "## 1.2 Methodology\n\n"
                    "Marabou-based YOLOv9 verification.\n"
                ),
                "impact": "## 2.1 Pathways\n\nSafety-critical CV adoption.\n",
                "implementation": "## 3.1 Work plan\n\nSeven WPs.\n",
            },
        },
    ]
    corpus_ids: list[uuid.UUID] = []
    for p in proposals:
        cid = await manager.add_proposal(
            programme_id="horizon_eu_ria",
            source="test_fixture",
            **p,
        )
        corpus_ids.append(cid)

    try:
        yield {"corpus_ids": corpus_ids, "embedder": embedder, "proposals": proposals}
    finally:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM successful_proposal_chunks WHERE corpus_id = ANY($1::uuid[])",
                corpus_ids,
            )
            await conn.execute(
                "DELETE FROM successful_proposals_corpus WHERE id = ANY($1::uuid[])",
                corpus_ids,
            )


# ── ANN search (no re-rank) ──────────────────────────────────────────────


async def test_query_matching_chunk_text_returns_self_first(
    pool: asyncpg.Pool, seeded_corpus: dict[str, object]
) -> None:
    """When the query text equals an existing chunk's text exactly, the
    deterministic embedder returns the same vector → cosine ≈ 1.0 → that
    chunk is the top hit. The chunker strips trailing whitespace and
    re-joins on ``\\n\\n``, so we read back the actual stored content
    rather than passing the original Markdown string."""

    embedder = seeded_corpus["embedder"]
    retriever = CorpusRetriever(pool=pool, embedder=embedder, router=None)

    # Pull the stored chunk text for the Battery Recycling proposal so
    # the query string matches byte-for-byte what the embedder hashed.
    async with pool.acquire() as conn:
        target_content = await conn.fetchval(
            """
            SELECT spc.content
              FROM successful_proposal_chunks spc
              JOIN successful_proposals_corpus sp ON sp.id = spc.corpus_id
             WHERE sp.title = 'Battery Recycling'
               AND spc.section = 'excellence'
             LIMIT 1
            """
        )
    assert target_content, "fixture chunk not found"

    results = await retriever.retrieve(
        target_content,
        programme_id="horizon_eu_ria",
        section="excellence",
        top_k=3,
    )
    assert results, "retriever returned nothing"
    top = results[0]
    assert top.score > 0.99, f"expected near-1.0 self-similarity, got {top.score}"
    assert "Lithium" in top.content
    assert top.title == "Battery Recycling"


async def test_section_filter_excludes_other_sections(
    pool: asyncpg.Pool, seeded_corpus: dict[str, object]
) -> None:
    """Querying section='impact' must not surface excellence chunks."""

    embedder = seeded_corpus["embedder"]
    retriever = CorpusRetriever(pool=pool, embedder=embedder, router=None)

    results = await retriever.retrieve(
        "downtime reduction",
        programme_id="horizon_eu_ria",
        section="impact",
        top_k=5,
    )
    assert results, "retriever returned nothing"
    for chunk in results:
        assert chunk.section == "impact"


async def test_programme_filter_excludes_other_programmes(
    pool: asyncpg.Pool, seeded_corpus: dict[str, object]
) -> None:
    retriever = CorpusRetriever(pool=pool, embedder=seeded_corpus["embedder"], router=None)
    results = await retriever.retrieve(
        "## 1.1 Objectives",
        programme_id="tubitak_1501",  # not in this corpus
        section="excellence",
        top_k=5,
    )
    assert results == []


async def test_top_k_caps_results(pool: asyncpg.Pool, seeded_corpus: dict[str, object]) -> None:
    retriever = CorpusRetriever(pool=pool, embedder=seeded_corpus["embedder"], router=None)
    results = await retriever.retrieve(
        "## 1.1 Objectives",
        programme_id="horizon_eu_ria",
        section="excellence",
        top_k=2,
    )
    assert len(results) <= 2


async def test_scores_are_in_descending_order(
    pool: asyncpg.Pool, seeded_corpus: dict[str, object]
) -> None:
    retriever = CorpusRetriever(pool=pool, embedder=seeded_corpus["embedder"], router=None)
    results = await retriever.retrieve(
        "manufacturing twin",
        programme_id="horizon_eu_ria",
        section="excellence",
        top_k=5,
    )
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# ── LLM re-rank ─────────────────────────────────────────────────────────


@pytest.mark.flaky_pre_s3
async def test_llm_rerank_reorders_candidates(
    pool: asyncpg.Pool, seeded_corpus: dict[str, object]
) -> None:
    """A FakeProvider returns a re-ranked id order; the retriever should
    honour it (subject to top_k cap).

    .. note::

        Marked ``flaky_pre_s3`` until TICKET-001 lands — the ANN
        ordering pgvector returns for tied cosine distances varies
        between two adjacent retrieve() calls in the same session, and
        the canned LLM verdict computed from the first call no longer
        maps cleanly onto the second call's candidate set. The retriever
        is correct; the test setup is fragile. Sprint 4 backlog.
    """

    embedder = seeded_corpus["embedder"]
    retriever_no_rerank = CorpusRetriever(pool=pool, embedder=embedder, router=None)
    ann = await retriever_no_rerank.retrieve(
        "verification YOLO",
        programme_id="horizon_eu_ria",
        section="excellence",
        top_k=3,
        candidate_pool=10,
        rerank=False,
    )
    assert len(ann) >= 2

    # Reverse the natural ANN order in our fake re-rank response.
    reverse_ids = [str(c.id) for c in reversed(ann)]
    canned = json.dumps({"ranked_ids": reverse_ids})
    primary = FakeProvider(
        "claude",
        [make_response(text=canned, model="claude-sonnet-4-6", provider="claude")],
    )
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    retriever = CorpusRetriever(pool=pool, embedder=embedder, router=router, tenant_id=uuid.uuid4())
    reranked = await retriever.retrieve(
        "verification YOLO",
        programme_id="horizon_eu_ria",
        section="excellence",
        top_k=3,
        candidate_pool=10,
    )
    assert [str(c.id) for c in reranked] == reverse_ids[: len(reranked)]
    assert len(primary.calls) == 1
    sent_request, model_used, _ = primary.calls[0]
    assert sent_request.task == "rerank"
    assert model_used == "claude-sonnet-4-6"


async def test_llm_rerank_garbage_response_falls_back(
    pool: asyncpg.Pool, seeded_corpus: dict[str, object]
) -> None:
    """If the LLM returns un-parseable text, the retriever falls back to
    ANN order rather than raising."""

    embedder = seeded_corpus["embedder"]
    primary = FakeProvider(
        "claude",
        [make_response(text="not JSON at all", model="claude-sonnet-4-6", provider="claude")],
    )
    fallback = FakeProvider("openai", [])
    router = build_router(providers={"claude": primary, "openai": fallback})

    retriever = CorpusRetriever(pool=pool, embedder=embedder, router=router, tenant_id=uuid.uuid4())
    results = await retriever.retrieve(
        "manufacturing",
        programme_id="horizon_eu_ria",
        section="excellence",
        top_k=2,
        candidate_pool=10,
    )
    # Survives, returns top-k ANN order.
    assert 0 < len(results) <= 2
