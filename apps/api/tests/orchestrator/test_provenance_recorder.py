"""Unit tests for the saga's sentence-level provenance recorder.

Three layers:

A. **split_into_sentences** — handles prose, headings, bullets,
   blank-line paragraph breaks, and degenerate input (empty / pure
   whitespace).

B. **build_rows** — walks ``WRITER_SECTIONS`` and produces one
   ``SentenceRow`` per sentence per completed writer. Failed /
   skipped writers contribute nothing.

C. **record** — DB-bound; verifies upsert behaviour and that a
   re-run with edited content updates the existing rows in place.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from src.agents.base import AgentOutput
from src.orchestrator.provenance_recorder import (
    build_rows,
    record,
    split_into_sentences,
)


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


# ── Splitter ───────────────────────────────────────────────────────────


def test_split_returns_empty_list_for_empty_input() -> None:
    assert split_into_sentences("") == []
    assert split_into_sentences("   \n  \n   ") == []


def test_split_keeps_each_terminal_punctuation_intact() -> None:
    md = "First sentence. Second sentence! Third sentence?"
    assert split_into_sentences(md) == [
        "First sentence.",
        "Second sentence!",
        "Third sentence?",
    ]


def test_split_treats_headings_as_standalone_sentences() -> None:
    md = "# Excellence\n\n## Sub-section\n\nBody first. Body second."
    sentences = split_into_sentences(md)
    assert sentences[0] == "# Excellence"
    assert sentences[1] == "## Sub-section"
    assert "Body first." in sentences
    assert "Body second." in sentences


def test_split_breaks_bullet_list_into_one_sentence_per_item() -> None:
    md = "- First bullet item.\n- Second bullet item.\n- Third item."
    assert split_into_sentences(md) == [
        "First bullet item.",
        "Second bullet item.",
        "Third item.",
    ]


def test_split_collapses_internal_whitespace() -> None:
    """Single-paragraph input with messy spacing gets normalised to a
    single internal space + still splits on terminal punctuation."""

    md = "Line one with  multiple   spaces.\nLine two follows."
    assert split_into_sentences(md) == [
        "Line one with multiple spaces.",
        "Line two follows.",
    ]


# ── Row builder ────────────────────────────────────────────────────────


def _writer_output(
    *,
    agent_id: str,
    content_key: str,
    markdown: str,
    status: str = "completed",
    model: str = "anthropic/claude-opus-4-7",
    tokens: int = 1200,
) -> AgentOutput:
    return AgentOutput(
        agent_id=agent_id,
        status=status,  # type: ignore[arg-type]
        output={content_key: markdown},
        metadata={"model": model},
        tokens_used={"input": tokens // 2, "output": tokens // 2},
    )


def test_build_rows_only_includes_completed_writers() -> None:
    pid = uuid.uuid4()
    outputs = {
        "excellence_writer": _writer_output(
            agent_id="excellence_writer",
            content_key="excellence_md",
            markdown="Strong claim. Supporting evidence.",
        ),
        "impact_writer": _writer_output(
            agent_id="impact_writer",
            content_key="impact_md",
            markdown="Should not appear.",
            status="failed",
        ),
    }
    rows = build_rows(proposal_id=pid, agent_outputs=outputs)
    assert {r.section for r in rows} == {"excellence"}
    assert all(r.source == "ai-generated" for r in rows)
    assert all(r.agent_id == "excellence_writer" for r in rows)


def test_build_rows_carries_model_and_tokens_through() -> None:
    pid = uuid.uuid4()
    outputs = {
        "impact_writer": _writer_output(
            agent_id="impact_writer",
            content_key="impact_md",
            markdown="One sentence.",
            model="anthropic/claude-sonnet-4-6",
            tokens=2000,
        ),
    }
    rows = build_rows(proposal_id=pid, agent_outputs=outputs)
    assert len(rows) == 1
    row = rows[0]
    assert row.llm_model == "anthropic/claude-sonnet-4-6"
    assert row.llm_tokens == 2000
    assert row.section == "impact"


def test_build_rows_skips_writers_with_empty_output() -> None:
    pid = uuid.uuid4()
    outputs = {
        "excellence_writer": _writer_output(
            agent_id="excellence_writer",
            content_key="excellence_md",
            markdown="",
        ),
    }
    assert build_rows(proposal_id=pid, agent_outputs=outputs) == []


def test_sentence_ids_are_deterministic_for_same_content() -> None:
    pid = uuid.uuid4()
    out = _writer_output(
        agent_id="excellence_writer",
        content_key="excellence_md",
        markdown="Identical content. Stays stable.",
    )
    first = build_rows(proposal_id=pid, agent_outputs={"excellence_writer": out})
    second = build_rows(proposal_id=pid, agent_outputs={"excellence_writer": out})
    assert [r.sentence_id for r in first] == [r.sentence_id for r in second]


def test_sentence_ids_change_when_content_changes() -> None:
    pid = uuid.uuid4()
    out_v1 = _writer_output(
        agent_id="excellence_writer",
        content_key="excellence_md",
        markdown="Version one wording.",
    )
    out_v2 = _writer_output(
        agent_id="excellence_writer",
        content_key="excellence_md",
        markdown="Version two wording.",
    )
    a = build_rows(proposal_id=pid, agent_outputs={"excellence_writer": out_v1})
    b = build_rows(proposal_id=pid, agent_outputs={"excellence_writer": out_v2})
    assert a[0].sentence_id != b[0].sentence_id


# ── DB-bound upsert ────────────────────────────────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping DB-bound provenance recorder tests"
        )
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


async def _seed_proposal(conn: asyncpg.Connection) -> uuid.UUID:
    """Create a minimal proposals row pointing at a throwaway tenant."""

    tenant_id = await conn.fetchval(
        "insert into tenants(name, plan) values ($1, 'starter') returning id",
        f"saga-prov-tenant-{uuid.uuid4()}",
    )
    proposal_id = await conn.fetchval(
        """
        insert into proposals(tenant_id, program_id, title, language, status)
        values ($1, 'tubitak_1501', 'Saga prov test', 'tr', 'draft_in_progress')
        returning id
        """,
        tenant_id,
    )
    return proposal_id


async def _cleanup(pool: asyncpg.Pool, proposal_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        # Delete the tenant — proposals + provenance cascade.
        tenant_id = await conn.fetchval(
            "select tenant_id from proposals where id = $1", proposal_id
        )
        if tenant_id is not None:
            await conn.execute("delete from tenants where id = $1", tenant_id)


async def test_record_persists_rows_and_is_idempotent(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        proposal_id = await _seed_proposal(conn)

    try:
        outputs = {
            "excellence_writer": _writer_output(
                agent_id="excellence_writer",
                content_key="excellence_md",
                markdown="The objective is to demonstrate X. Methodology follows Y.",
            ),
            "impact_writer": _writer_output(
                agent_id="impact_writer",
                content_key="impact_md",
                markdown="Expected impact: 30% productivity gain.",
            ),
        }
        async with pool.acquire() as conn:
            count_first = await record(
                conn, proposal_id=proposal_id, agent_outputs=outputs
            )
            count_second = await record(
                conn, proposal_id=proposal_id, agent_outputs=outputs
            )

        assert count_first == 3
        assert count_second == 3  # upsert touched the same rows

        async with pool.acquire() as conn:
            db_total = await conn.fetchval(
                "select count(*) from proposal_provenance where proposal_id = $1",
                proposal_id,
            )
        assert db_total == 3
    finally:
        await _cleanup(pool, proposal_id)


async def test_record_writes_through_sources_agent_and_model(
    pool: asyncpg.Pool,
) -> None:
    async with pool.acquire() as conn:
        proposal_id = await _seed_proposal(conn)
    try:
        outputs = {
            "implementation_writer": _writer_output(
                agent_id="implementation_writer",
                content_key="implementation_md",
                markdown="Work plan: phase one.",
                model="anthropic/claude-opus-4-7",
                tokens=900,
            ),
        }
        async with pool.acquire() as conn:
            await record(conn, proposal_id=proposal_id, agent_outputs=outputs)
            row = await conn.fetchrow(
                "select section, source, agent_id, llm_model, llm_tokens "
                "from proposal_provenance where proposal_id = $1",
                proposal_id,
            )
        assert row is not None
        assert row["section"] == "implementation"
        assert row["source"] == "ai-generated"
        assert row["agent_id"] == "implementation_writer"
        assert row["llm_model"] == "anthropic/claude-opus-4-7"
        assert row["llm_tokens"] == 900
    finally:
        await _cleanup(pool, proposal_id)


async def test_record_swallows_db_failures_and_returns_zero() -> None:
    """If the connection raises, ``record`` logs + returns 0 — never bubbles."""

    class _BoomConn:
        async def executemany(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated DB outage")

    outputs = {
        "excellence_writer": _writer_output(
            agent_id="excellence_writer",
            content_key="excellence_md",
            markdown="One sentence here.",
        ),
    }
    count = await record(
        _BoomConn(),  # type: ignore[arg-type]
        proposal_id=uuid.uuid4(),
        agent_outputs=outputs,
    )
    assert count == 0
