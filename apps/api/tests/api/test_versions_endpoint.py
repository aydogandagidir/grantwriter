"""Tests for the proposal version snapshot/restore endpoints.

Covers:

- POST snapshot → version_number=1, audit row written.
- Successive POST → version_number=2.
- GET list → newest-first, no snapshot bodies.
- POST restore v1 → new version (n+1), proposals.draft updated to v1's
  snapshot, older versions intact.
- Cross-tenant: a foreign proposal id returns 404, never 403.

Skips when ``TEST_DATABASE_URL`` is unset.
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
            "TEST_DATABASE_URL not set — skipping DB-bound version tests"
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


# ── DB helpers ─────────────────────────────────────────────────────────


async def _make_user(
    pool: asyncpg.Pool,
    *,
    tenant_id: uuid.UUID | None = None,
    role: str = "member",
) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    async with pool.acquire() as conn:
        if tenant_id is None:
            tenant_id = uuid.uuid4()
            await conn.execute(
                "insert into tenants (id, name, slug) values ($1, $2, $3)",
                tenant_id,
                "Versions Test",
                f"ver-{tenant_id}",
            )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"u-{user_id}@example.com",
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role) values ($1, $2, $3)",
            user_id,
            tenant_id,
            role,
        )
    return tenant_id, user_id


async def _make_proposal(
    pool: asyncpg.Pool,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    draft: dict[str, str] | None = None,
) -> uuid.UUID:
    proposal_id = uuid.uuid4()
    payload = draft or {"excellence_md": "v1 body"}
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into proposals (
              id, tenant_id, programme_id, title, language, status,
              brief, draft, created_by
            ) values (
              $1, $2, 'horizon_eu_ria', 'Test Proposal', 'en', 'draft',
              '{}'::jsonb, $3::jsonb, $4
            )
            """,
            proposal_id,
            tenant_id,
            json.dumps(payload),
            user_id,
        )
    return proposal_id


async def _cleanup(
    pool: asyncpg.Pool,
    *,
    tenant_ids: list[uuid.UUID],
    user_ids: list[uuid.UUID],
    proposal_ids: list[uuid.UUID] | None = None,
) -> None:
    async with pool.acquire() as conn:
        if proposal_ids:
            await conn.execute(
                "delete from proposal_versions where proposal_id = any($1::uuid[])",
                proposal_ids,
            )
            await conn.execute(
                "delete from proposals where id = any($1::uuid[])", proposal_ids
            )
        if tenant_ids:
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

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


# ── Tests ──────────────────────────────────────────────────────────────


async def test_post_snapshot_starts_at_v1_and_increments(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id = await _make_user(pool, role="member")
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=user_id
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            r1 = await client.post(
                f"/api/v1/proposals/{proposal_id}/versions",
                json={"comment": "before edit"},
            )
            assert r1.status_code == 201, r1.text
            assert r1.json()["version_number"] == 1

            r2 = await client.post(
                f"/api/v1/proposals/{proposal_id}/versions", json={}
            )
            assert r2.status_code == 201
            assert r2.json()["version_number"] == 2

        # Audit rows.
        async with pool.acquire() as conn:
            audits = await conn.fetch(
                "select action, diff::text as d from audit_log "
                "where tenant_id = $1 and action = 'proposal.version_created'",
                tenant_id,
            )
        assert len(audits) == 2
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[user_id],
            proposal_ids=[proposal_id],
        )


async def test_get_list_omits_snapshot_body(pool: asyncpg.Pool) -> None:
    tenant_id, user_id = await _make_user(pool)
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=user_id
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            await client.post(
                f"/api/v1/proposals/{proposal_id}/versions",
                json={"comment": "v1"},
            )
            await client.post(
                f"/api/v1/proposals/{proposal_id}/versions",
                json={"comment": "v2"},
            )
            response = await client.get(
                f"/api/v1/proposals/{proposal_id}/versions"
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert len(body["versions"]) == 2
            # Newest first.
            assert body["versions"][0]["version_number"] == 2
            assert body["versions"][1]["version_number"] == 1
            # Body / snapshot intentionally omitted.
            for row in body["versions"]:
                assert "draft_snapshot" not in row
                assert "snapshot" not in row
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[user_id],
            proposal_ids=[proposal_id],
        )


async def test_restore_creates_new_version_and_updates_draft(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, user_id = await _make_user(pool)
    # Initial draft.
    proposal_id = await _make_proposal(
        pool,
        tenant_id=tenant_id,
        user_id=user_id,
        draft={"excellence_md": "version 1 content"},
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            # Snapshot v1 = "version 1 content"
            await client.post(
                f"/api/v1/proposals/{proposal_id}/versions", json={}
            )
            # Mutate the draft on the proposal directly.
            async with pool.acquire() as conn:
                await conn.execute(
                    "update proposals set draft = $1::jsonb where id = $2",
                    json.dumps({"excellence_md": "version 2 content"}),
                    proposal_id,
                )
            # Snapshot v2 = "version 2 content"
            await client.post(
                f"/api/v1/proposals/{proposal_id}/versions", json={}
            )
            # Restore v1 → new v3 with v1's content.
            restore = await client.post(
                f"/api/v1/proposals/{proposal_id}/versions/1/restore"
            )
            assert restore.status_code == 200, restore.text
            body = restore.json()
            assert body["restored_from_version"] == 1
            assert body["new_version_number"] == 3

        # Inspect DB state.
        async with pool.acquire() as conn:
            current_draft = await conn.fetchval(
                "select draft from proposals where id = $1", proposal_id
            )
            versions = await conn.fetch(
                "select version_number, draft_snapshot from proposal_versions "
                "where proposal_id = $1 order by version_number",
                proposal_id,
            )
        # Coerce jsonb returns into dicts robustly.
        if isinstance(current_draft, str):
            current_draft = json.loads(current_draft)
        assert current_draft["excellence_md"] == "version 1 content"
        assert {row["version_number"] for row in versions} == {1, 2, 3}
        # v3's snapshot mirrors v1's body.
        v3 = next(r for r in versions if r["version_number"] == 3)
        snapshot = v3["draft_snapshot"]
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        assert snapshot["excellence_md"] == "version 1 content"
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[user_id],
            proposal_ids=[proposal_id],
        )


async def test_cross_tenant_access_returns_404(pool: asyncpg.Pool) -> None:
    tenant_a, user_a = await _make_user(pool)
    tenant_b, user_b = await _make_user(pool)
    proposal_a = await _make_proposal(
        pool, tenant_id=tenant_a, user_id=user_a
    )
    try:
        # B tries to snapshot A's proposal.
        _app, client = _build_client(pool=pool, user_id=user_b)
        async with client:
            response = await client.post(
                f"/api/v1/proposals/{proposal_a}/versions", json={}
            )
            assert response.status_code == 404

            list_response = await client.get(
                f"/api/v1/proposals/{proposal_a}/versions"
            )
            assert list_response.status_code == 404

            restore_response = await client.post(
                f"/api/v1/proposals/{proposal_a}/versions/1/restore"
            )
            assert restore_response.status_code == 404
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_a, tenant_b],
            user_ids=[user_a, user_b],
            proposal_ids=[proposal_a],
        )


async def test_restore_unknown_version_returns_404(pool: asyncpg.Pool) -> None:
    tenant_id, user_id = await _make_user(pool)
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=user_id
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            # No version_number=99 exists.
            response = await client.post(
                f"/api/v1/proposals/{proposal_id}/versions/99/restore"
            )
            assert response.status_code == 404
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[user_id],
            proposal_ids=[proposal_id],
        )
