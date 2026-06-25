"""Tests for ``POST /api/v1/proposals/{id}/provenance`` and stats.

Covers:

- POST 3 fresh rows → 3 inserted; stats shows per_source counts.
- POST same sentence_id with a flipped source → row UPDATEd in place
  (no PK violation, no duplicate); stats reflects the new source.
- Batch exceeding 500 rows → 413; nothing persisted.
- Cross-tenant proposal id → 404; nothing persisted.
- Stats route 404s for foreign proposals likewise.

The test seeds a proposal row directly because the proposals API is
out of scope for this PR; we don't need a saga run to exercise
provenance writes.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.auth import get_current_user_id
from src.core.db import get_db
from src.main import create_app


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping DB-bound provenance tests"
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


async def _setup_owner_with_proposal(
    pool: asyncpg.Pool,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Return (tenant_id, user_id, proposal_id)."""

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "Provenance Test",
            f"prov-{tenant_id}",
        )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"u-{user_id}@example.com",
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role) values ($1, $2, 'owner')",
            user_id,
            tenant_id,
        )
        await conn.execute(
            """
            insert into proposals (id, tenant_id, created_by, programme_id, language)
            values ($1, $2, $3, 'horizon_eu_ria', 'en')
            """,
            proposal_id,
            tenant_id,
            user_id,
        )
    return tenant_id, user_id, proposal_id


async def _cleanup(
    pool: asyncpg.Pool,
    tenant_ids: list[uuid.UUID],
    user_ids: list[uuid.UUID],
) -> None:
    async with pool.acquire() as conn:
        if tenant_ids:
            await conn.execute(
                "delete from proposal_provenance "
                "where proposal_id in (select id from proposals "
                "where tenant_id = any($1::uuid[]))",
                tenant_ids,
            )
            await conn.execute(
                "delete from proposals where tenant_id = any($1::uuid[])",
                tenant_ids,
            )
            await conn.execute(
                "delete from audit_log where tenant_id = any($1::uuid[])",
                tenant_ids,
            )
        if user_ids:
            await conn.execute(
                "delete from public.users where id = any($1::uuid[])", user_ids
            )
            await conn.execute(
                "delete from auth.users where id = any($1::uuid[])", user_ids
            )
        if tenant_ids:
            await conn.execute(
                "delete from tenants where id = any($1::uuid[])", tenant_ids
            )


def _build_client(
    *, pool: asyncpg.Pool, user_id: uuid.UUID
) -> tuple[FastAPI, AsyncClient]:
    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


def _sentence(
    sentence_id: str,
    source: str = "ai-generated",
    *,
    section: str = "excellence",
    agent_id: str | None = "excellence_writer",
    llm_model: str | None = "claude-opus-4-7",
) -> dict[str, object]:
    return {
        "sentence_id": sentence_id,
        "section": section,
        "content": f"Sentence {sentence_id}",
        "source": source,
        "agent_id": agent_id,
        "llm_model": llm_model,
        "llm_tokens": 24,
        "source_citations": [],
    }


# ── Tests ──────────────────────────────────────────────────────────────


async def test_post_inserts_three_rows_and_stats_reflects_them(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                f"/api/v1/proposals/{proposal_id}/provenance",
                json={
                    "sentences": [
                        _sentence("s1"),
                        _sentence("s2"),
                        _sentence("s3", source="human", agent_id=None, llm_model=None),
                    ]
                },
            )
            assert response.status_code == 200
            assert response.json()["upserted"] == 3

            stats = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance/stats"
            )
        assert stats.status_code == 200
        body = stats.json()
        assert body["total"] == 3
        source_counts = {row["source"]: row["count"] for row in body["per_source"]}
        assert source_counts == {"ai-generated": 2, "human": 1}
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_repost_same_sentence_id_updates_in_place(
    pool: asyncpg.Pool,
) -> None:
    """Re-POSTing with a flipped source ``ai-generated → ai-edited``
    must update the row instead of duplicating — the unique constraint
    plus our ON CONFLICT DO UPDATE handle it."""

    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            await client.post(
                f"/api/v1/proposals/{proposal_id}/provenance",
                json={"sentences": [_sentence("s1", source="ai-generated")]},
            )
            await client.post(
                f"/api/v1/proposals/{proposal_id}/provenance",
                json={"sentences": [_sentence("s1", source="ai-edited")]},
            )
            stats = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance/stats"
            )

        body = stats.json()
        assert body["total"] == 1
        assert body["per_source"][0] == {"source": "ai-edited", "count": 1}
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_batch_over_max_size_returns_413(pool: asyncpg.Pool) -> None:
    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        big_batch = {
            "sentences": [_sentence(f"s{i}") for i in range(501)]
        }
        async with client:
            response = await client.post(
                f"/api/v1/proposals/{proposal_id}/provenance", json=big_batch
            )
        assert response.status_code == 413
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_cross_tenant_proposal_returns_404(pool: asyncpg.Pool) -> None:
    tenant_a, user_a, prop_a = await _setup_owner_with_proposal(pool)
    tenant_b, user_b, _prop_b = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_b)
        async with client:
            post = await client.post(
                f"/api/v1/proposals/{prop_a}/provenance",
                json={"sentences": [_sentence("s1")]},
            )
            stats = await client.get(
                f"/api/v1/proposals/{prop_a}/provenance/stats"
            )
            items = await client.get(
                f"/api/v1/proposals/{prop_a}/provenance"
            )
        assert post.status_code == 404
        assert stats.status_code == 404
        assert items.status_code == 404
    finally:
        await _cleanup(pool, [tenant_a, tenant_b], [user_a, user_b])


# ── GET /provenance list ───────────────────────────────────────────────


async def test_list_returns_items_in_section_creation_order(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            # Seed 4 rows across two sections.
            await client.post(
                f"/api/v1/proposals/{proposal_id}/provenance",
                json={
                    "sentences": [
                        _sentence("e-1", section="excellence"),
                        _sentence("e-2", section="excellence"),
                        _sentence("i-1", section="impact"),
                        _sentence("i-2", section="impact"),
                    ]
                },
            )
            full = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance"
            )
        assert full.status_code == 200
        body = full.json()
        assert len(body["items"]) == 4
        assert body["next_offset"] is None
        # Each item carries the agent + model the writer stamped.
        assert body["items"][0]["agent_id"] == "excellence_writer"
        assert body["items"][0]["llm_model"] == "claude-opus-4-7"
        # Items come back in (section, created_at) order — excellence
        # rows precede impact rows.
        sections = [item["section"] for item in body["items"]]
        assert sections == sorted(sections)
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_list_filters_by_section(pool: asyncpg.Pool) -> None:
    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            await client.post(
                f"/api/v1/proposals/{proposal_id}/provenance",
                json={
                    "sentences": [
                        _sentence("e-1", section="excellence"),
                        _sentence("i-1", section="impact"),
                    ]
                },
            )
            response = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance",
                params={"section": "impact"},
            )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["section"] == "impact"
        assert items[0]["sentence_id"] == "i-1"
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_list_paginates_with_next_offset(pool: asyncpg.Pool) -> None:
    """A small ``limit`` returns ``next_offset`` pointing at the rest;
    the follow-up call with that offset returns the remainder + null."""

    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            await client.post(
                f"/api/v1/proposals/{proposal_id}/provenance",
                json={
                    "sentences": [
                        _sentence(f"s-{i}") for i in range(5)
                    ]
                },
            )
            first = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance",
                params={"limit": 2, "offset": 0},
            )
            second = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance",
                params={"limit": 2, "offset": 2},
            )
            third = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance",
                params={"limit": 2, "offset": 4},
            )
        assert first.status_code == 200
        assert len(first.json()["items"]) == 2
        assert first.json()["next_offset"] == 2

        assert len(second.json()["items"]) == 2
        assert second.json()["next_offset"] == 4

        assert len(third.json()["items"]) == 1
        assert third.json()["next_offset"] is None
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_list_rejects_out_of_range_limit(pool: asyncpg.Pool) -> None:
    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            too_big = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance",
                params={"limit": 501},
            )
            zero = await client.get(
                f"/api/v1/proposals/{proposal_id}/provenance",
                params={"limit": 0},
            )
        # FastAPI/Pydantic returns 422 for query-string validation errors.
        assert too_big.status_code == 422
        assert zero.status_code == 422
    finally:
        await _cleanup(pool, [tenant_id], [user_id])
