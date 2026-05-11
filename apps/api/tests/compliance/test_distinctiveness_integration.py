"""Integration test: full scorer path against live Postgres+pgvector.

Skipped automatically if ``TEST_DATABASE_URL`` is unset (TICKET-002 dropped
the generic ``bluedev`` fallback) — see the ``live_db_pool`` fixture in
``conftest.py``. CI sets the env to the throwaway ``bluedev_test`` database;
locally, point it at a dedicated test DB before running this suite:

    export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/bluedev_test
    bash scripts/apply_migrations.sh "$TEST_DATABASE_URL" --strict
    poetry run pytest tests/compliance/test_distinctiveness_integration.py

Uses the real production schema (tenants → auth.users → public.users → calls →
proposals → cordis_funded_projects). The connection is opened as the postgres
superuser, which bypasses RLS — these tests exercise the scorer logic against
a fully-populated DB, not the security boundary. RLS is covered by the
dedicated suite in tests/security/.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from src.compliance.distinctiveness import (
    DistinctivenessScorer,
    ProposalNotFoundError,
    ProposalNotReadyError,
)

pytestmark = pytest.mark.integration


def _vec_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


@pytest.fixture
async def db_conn(live_db_pool: Any) -> Any:
    """Acquire a single connection and reset the rows we touch.

    Each integration test is independent — we wipe the rows we own at the
    start (idempotent) so test order doesn't matter and a previous failure
    doesn't pollute the next run.
    """
    async with live_db_pool.acquire() as conn:
        # Order matters: child rows first.
        await conn.execute("truncate cordis_funded_projects")
        await conn.execute(
            "truncate proposal_provenance, citations, proposal_versions, proposal_comments cascade"
        )
        await conn.execute("truncate proposals cascade")
        await conn.execute("truncate calls cascade")
        # public.users → auth.users via FK; clean test-owned rows only.
        await conn.execute("delete from public.users where role = 'owner'")
        await conn.execute("delete from auth.users where email like 'distinct-test-%'")
        await conn.execute("delete from tenants where slug like 'distinct-test-%'")
        yield conn


async def _seed_cordis(
    conn: Any, *, topic: str, abstracts_and_acronyms: list[tuple[str, str]]
) -> None:
    """Insert fake CORDIS rows. Each row's embedding is the hash of its abstract
    so similarity is fully determined by abstract text equality."""
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
            f"cordis_{acronym}_{uuid.uuid4().hex[:6]}",
            f"{acronym} title",
            acronym,
            [topic],
            abstract,
            _vec_literal(emb),
        )


async def _create_proposal_with_call(
    conn: Any, *, topic: str, excellence_text: str | None
) -> str:
    """Create the full chain: tenant → auth.users → public.users → call → proposal.

    Returns the proposal id as a string. The connection is superuser so RLS is
    bypassed; we still satisfy every NOT NULL and FK constraint so the assertion
    surface matches production.
    """
    suffix = uuid.uuid4().hex[:8]
    tenant_id = await conn.fetchval(
        "insert into tenants (name, slug) values ($1, $2) returning id",
        f"distinct-test-tenant-{suffix}",
        f"distinct-test-{suffix}",
    )
    user_id = uuid.uuid4()
    await conn.execute(
        "insert into auth.users (id, email) values ($1, $2)",
        user_id,
        f"distinct-test-{suffix}@example.com",
    )
    await conn.execute(
        "insert into public.users (id, tenant_id, role) values ($1, $2, 'owner')",
        user_id,
        tenant_id,
    )
    call_id = await conn.fetchval(
        """
        insert into calls (programme_id, source, external_id, title, language, status)
        values ('horizon_eu_ria', 'manual', $1, 'Test Call', 'en', 'open')
        returning id
        """,
        topic,
    )

    draft_jsonb = (
        "{}" if excellence_text is None else json.dumps({"excellence_md": excellence_text})
    )
    proposal_id = await conn.fetchval(
        """
        insert into proposals (tenant_id, created_by, programme_id, language, call_id, draft, status)
        values ($1, $2, 'horizon_eu_ria', 'en', $3, $4::jsonb, 'draft')
        returning id
        """,
        tenant_id,
        user_id,
        call_id,
        draft_jsonb,
    )
    return str(proposal_id)


async def test_returns_unknown_when_no_cordis_rows_in_topic(
    db_conn: Any, deterministic_embedder: Any
) -> None:
    proposal_id = await _create_proposal_with_call(
        db_conn,
        topic="HORIZON-CL4-2026-EMPTY-01",
        excellence_text="A novel quantum-classical hybrid optimizer for routing problems.",
    )
    score = await DistinctivenessScorer().score(proposal_id, db_conn)
    assert score.level == "unknown"
    assert score.score is None


async def test_returns_critical_when_cordis_row_matches_user_text(
    db_conn: Any, deterministic_embedder: Any
) -> None:
    text = "Edge AI for predictive maintenance in manufacturing assembly lines."
    await _seed_cordis(
        db_conn,
        topic="HORIZON-CL4-2026-DIGITAL-EDGE-01",
        abstracts_and_acronyms=[
            (text, "TWINFLOW"),
            ("Totally unrelated text about marine biology research.", "OCEANX"),
        ],
    )
    proposal_id = await _create_proposal_with_call(
        db_conn,
        topic="HORIZON-CL4-2026-DIGITAL-EDGE-01",
        excellence_text=text,
    )
    score = await DistinctivenessScorer().score(proposal_id, db_conn)
    # Identical text → identical hash-vector → cosine == 1.0 → level critical.
    assert score.level == "critical"
    assert score.score is not None and score.score > 0.99
    assert score.similar_projects[0].acronym == "TWINFLOW"


async def test_raises_when_proposal_missing(
    db_conn: Any, deterministic_embedder: Any
) -> None:
    bogus = uuid.uuid4()
    with pytest.raises(ProposalNotFoundError):
        await DistinctivenessScorer().score(bogus, db_conn)


async def test_raises_when_excellence_md_missing(
    db_conn: Any, deterministic_embedder: Any
) -> None:
    proposal_id = await _create_proposal_with_call(
        db_conn,
        topic="TOPIC-NO-EXCELLENCE",
        excellence_text=None,  # leaves draft as `{}`
    )
    with pytest.raises(ProposalNotReadyError):
        await DistinctivenessScorer().score(proposal_id, db_conn)


async def test_persists_score_to_proposals_table(
    db_conn: Any, deterministic_embedder: Any
) -> None:
    text = "Photonic neural network accelerators for low-power inference."
    await _seed_cordis(
        db_conn,
        topic="TOPIC-PERSIST",
        abstracts_and_acronyms=[(text, "PHOTON")],
    )
    proposal_id = await _create_proposal_with_call(
        db_conn,
        topic="TOPIC-PERSIST",
        excellence_text=text,
    )
    await DistinctivenessScorer().score(proposal_id, db_conn)
    stored = await db_conn.fetchval(
        "select distinctiveness_score from proposals where id = $1", proposal_id
    )
    assert stored is not None
    assert float(stored) > 0.99
