"""Tests for ``GET /api/v1/proposals/{id}`` — the editor-shell payload.

Skips when ``TEST_DATABASE_URL`` is unset (DB-bound).

Covers:
- 200 returns id + title + status + per-section draft markdown.
- ``draft`` defaults to empty strings when ``proposals.draft`` is null.
- Cross-tenant proposal → 404 (no enumeration leak — same as the
  provenance endpoints).
"""

from __future__ import annotations

import json
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


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping DB-bound proposal-read tests"
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
    *,
    draft: dict[str, str] | None = None,
    title: str = "Pilot proposal",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "Proposal Read Test",
            f"pr-{tenant_id}",
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
            insert into proposals (
              id, tenant_id, created_by, programme_id, language, title, draft
            ) values ($1, $2, $3, 'horizon_eu_ria', 'en', $4, $5::jsonb)
            """,
            proposal_id,
            tenant_id,
            user_id,
            title,
            json.dumps(draft) if draft is not None else None,
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


async def test_get_returns_header_and_per_section_markdown(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(
        pool,
        draft={
            "excellence_md": "# Excellence\n\nFirst sentence. Second.",
            "impact_md": "## Impact\n\nClaim.",
            "implementation_md": "",
        },
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.get(f"/api/v1/proposals/{proposal_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(proposal_id)
        assert body["title"] == "Pilot proposal"
        assert body["language"] == "en"
        assert body["programme_id"] == "horizon_eu_ria"
        assert body["draft"]["excellence_md"].startswith("# Excellence")
        assert "Claim." in body["draft"]["impact_md"]
        assert body["draft"]["implementation_md"] == ""
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_get_returns_empty_strings_when_draft_is_null(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id, proposal_id = await _setup_owner_with_proposal(
        pool, draft=None
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.get(f"/api/v1/proposals/{proposal_id}")
        assert response.status_code == 200
        draft = response.json()["draft"]
        assert draft == {
            "excellence_md": "",
            "impact_md": "",
            "implementation_md": "",
        }
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_cross_tenant_proposal_returns_404(pool: asyncpg.Pool) -> None:
    tenant_a, user_a, prop_a = await _setup_owner_with_proposal(pool)
    tenant_b, user_b, _prop_b = await _setup_owner_with_proposal(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_b)
        async with client:
            response = await client.get(f"/api/v1/proposals/{prop_a}")
        assert response.status_code == 404
    finally:
        await _cleanup(pool, [tenant_a, tenant_b], [user_a, user_b])
