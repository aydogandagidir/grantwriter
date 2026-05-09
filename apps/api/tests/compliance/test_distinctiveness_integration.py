"""Integration test: full scorer path against live Postgres+pgvector.

Skipped automatically if DATABASE_URL is unreachable — see the live_db_pool
fixture in conftest.py. Run after `docker compose up -d` and migrations.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from src.compliance.distinctiveness import (
    DistinctivenessScorer,
    ProposalNotFoundError,
    ProposalNotReadyError,
)

pytestmark = pytest.mark.integration


# Minimal stub schema for the test — when the real proposals/calls migrations
# land in another sprint task this fixture goes away and the scorer queries the
# real tables unchanged.
_STUB_SCHEMA = """
create table if not exists calls (
  id uuid primary key default gen_random_uuid(),
  external_id text not null
);
create table if not exists proposals (
  id uuid primary key default gen_random_uuid(),
  call_id uuid references calls(id),
  draft jsonb not null default '{}'::jsonb,
  distinctiveness_score numeric(5,4)
);
"""


def _vec_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


@pytest.fixture
async def stub_proposal_schema(live_db_pool: Any) -> Any:
    async with live_db_pool.acquire() as conn:
        await conn.execute(_STUB_SCHEMA)
        await conn.execute("truncate cordis_funded_projects")
        await conn.execute("truncate proposals cascade")
        await conn.execute("truncate calls cascade")
    return live_db_pool


async def _seed_cordis(
    conn: Any, *, topic: str, abstracts_and_acronyms: list[tuple[str, str]]
) -> None:
    """Insert fake CORDIS rows. Each row's embedding is the hash of its abstract
    so similarity is fully determined by abstract text equality (or the
    deterministic_embedder applied to the user's excellence text)."""
    from tests.conftest import _hash_to_unit_vector

    for abstract, acronym in abstracts_and_acronyms:
        emb = _hash_to_unit_vector(abstract, dim=3072)
        await conn.execute(
            """
            insert into cordis_funded_projects
              (cordis_id, title, acronym, topic_ids, programme,
               start_date, end_date, abstract, abstract_embedding)
            values
              ($1, $2, $3, $4, 'HORIZON', current_date - interval '6 months',
               current_date + interval '18 months', $5, $6::halfvec(3072))
            """,
            f"cordis_{acronym}",
            f"{acronym} title",
            acronym,
            [topic],
            abstract,
            _vec_literal(emb),
        )


async def _create_proposal(conn: Any, *, topic: str, excellence_text: str) -> str:
    call_id = await conn.fetchval(
        "insert into calls (external_id) values ($1) returning id", topic
    )
    proposal_id = await conn.fetchval(
        "insert into proposals (call_id, draft) values ($1, jsonb_build_object('excellence_md', $2::text)) returning id",
        call_id,
        excellence_text,
    )
    return str(proposal_id)


async def test_returns_unknown_when_no_cordis_rows_in_topic(
    stub_proposal_schema: Any, deterministic_embedder: Any
) -> None:
    async with stub_proposal_schema.acquire() as conn:
        proposal_id = await _create_proposal(
            conn,
            topic="HORIZON-CL4-2026-EMPTY-01",
            excellence_text="A novel quantum-classical hybrid optimizer for routing problems.",
        )
        score = await DistinctivenessScorer().score(proposal_id, conn)
    assert score.level == "unknown"
    assert score.score is None


async def test_returns_critical_when_cordis_row_matches_user_text(
    stub_proposal_schema: Any, deterministic_embedder: Any
) -> None:
    text = "Edge AI for predictive maintenance in manufacturing assembly lines."
    async with stub_proposal_schema.acquire() as conn:
        await _seed_cordis(
            conn,
            topic="HORIZON-CL4-2026-DIGITAL-EDGE-01",
            abstracts_and_acronyms=[
                (text, "TWINFLOW"),
                ("Totally unrelated text about marine biology research.", "OCEANX"),
            ],
        )
        proposal_id = await _create_proposal(
            conn, topic="HORIZON-CL4-2026-DIGITAL-EDGE-01", excellence_text=text
        )
        score = await DistinctivenessScorer().score(proposal_id, conn)
    # Identical text → identical hash-vector → cosine == 1.0 → level critical.
    assert score.level == "critical"
    assert score.score is not None and score.score > 0.99
    assert score.similar_projects[0].acronym == "TWINFLOW"


async def test_raises_when_proposal_missing(
    stub_proposal_schema: Any, deterministic_embedder: Any
) -> None:
    bogus = uuid4()
    async with stub_proposal_schema.acquire() as conn:
        with pytest.raises(ProposalNotFoundError):
            await DistinctivenessScorer().score(bogus, conn)


async def test_raises_when_excellence_md_missing(
    stub_proposal_schema: Any, deterministic_embedder: Any
) -> None:
    async with stub_proposal_schema.acquire() as conn:
        call_id = await conn.fetchval(
            "insert into calls (external_id) values ('TOPIC-X') returning id"
        )
        proposal_id = await conn.fetchval(
            "insert into proposals (call_id, draft) values ($1, '{}'::jsonb) returning id",
            call_id,
        )
        with pytest.raises(ProposalNotReadyError):
            await DistinctivenessScorer().score(proposal_id, conn)


async def test_persists_score_to_proposals_table(
    stub_proposal_schema: Any, deterministic_embedder: Any
) -> None:
    text = "Photonic neural network accelerators for low-power inference."
    async with stub_proposal_schema.acquire() as conn:
        await _seed_cordis(
            conn,
            topic="TOPIC-PERSIST",
            abstracts_and_acronyms=[(text, "PHOTON")],
        )
        proposal_id = await _create_proposal(
            conn, topic="TOPIC-PERSIST", excellence_text=text
        )
        await DistinctivenessScorer().score(proposal_id, conn)
        stored = await conn.fetchval(
            "select distinctiveness_score from proposals where id = $1", proposal_id
        )
    assert stored is not None
    assert float(stored) > 0.99
