"""Endpoint tests for the Sprint 4 MVP proposal CRUD surface.

Covers ``POST /api/v1/proposals``, ``GET /api/v1/proposals``,
``GET /api/v1/proposals/{id}``, ``PATCH /api/v1/proposals/{id}``,
``DELETE /api/v1/proposals/{id}`` plus the new ``programmes`` +
``calls`` catalog endpoints. The whole suite hits a real Postgres
through the ``pool`` fixture (skips when ``TEST_DATABASE_URL`` is
unset, like the rest of the tenant suite).
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
            "TEST_DATABASE_URL not set — skipping DB-bound proposal CRUD tests"
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


async def _make_user(
    pool: asyncpg.Pool,
    *,
    tenant_id: uuid.UUID | None = None,
    role: str = "owner",
) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    async with pool.acquire() as conn:
        if tenant_id is None:
            tenant_id = uuid.uuid4()
            await conn.execute(
                "insert into tenants (id, name, slug) values ($1, $2, $3)",
                tenant_id,
                f"Proposals Test {tenant_id.hex[:6]}",
                f"prop-{tenant_id.hex[:8]}",
            )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"prop-{user_id.hex[:8]}@example.com",
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role) "
            "values ($1, $2, $3)",
            user_id,
            tenant_id,
            role,
        )
    return tenant_id, user_id


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
                "delete from calls where raw_metadata->>'_test_tenant' "
                "= any($1::text[])",
                [str(tid) for tid in tenant_ids],
            )
            await conn.execute(
                "delete from audit_log where tenant_id = any($1::uuid[])",
                tenant_ids,
            )
        if user_ids:
            await conn.execute(
                "delete from public.users where id = any($1::uuid[])",
                user_ids,
            )
            await conn.execute(
                "delete from auth.users where id = any($1::uuid[])",
                user_ids,
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


# ── Programmes ─────────────────────────────────────────────────────────


async def test_programmes_endpoint_returns_seeded_catalog(
    pool: asyncpg.Pool,
) -> None:
    """The 5-row migration-003 seed (TÜBİTAK 1501 + 1507, KOSGEB,
    HE RIA, Cascade) must come back via the public catalog endpoint."""

    tenant_id, owner_id = await _make_user(pool)
    try:
        _, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.get("/api/v1/programmes")
        assert response.status_code == 200
        programmes = response.json()["programmes"]
        ids = {p["id"] for p in programmes}
        # Seed has 5 rows; assert the well-known ones we ship modules for.
        assert {"tubitak_1501", "tubitak_1507", "kosgeb_arge",
                "horizon_eu_ria", "cascade_funding"}.issubset(ids)
        # Ordering: funder asc, name_en asc (we don't assert exact order
        # but every row has funder populated).
        assert all(p["funder"] for p in programmes)
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


# ── Proposals: create + read ───────────────────────────────────────────


async def test_create_and_read_proposal_round_trip(pool: asyncpg.Pool) -> None:
    tenant_id, owner_id = await _make_user(pool)
    try:
        _, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            create_resp = await client.post(
                "/api/v1/proposals",
                json={
                    "programme_id": "horizon_eu_ria",
                    "language": "en",
                    "title": "Test HE pilot",
                    "brief": {"summary": "Initial brief"},
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            created = create_resp.json()
            proposal_id = created["id"]
            assert created["status"] == "draft"
            assert created["title"] == "Test HE pilot"
            assert created["brief"] == {"summary": "Initial brief"}

            # Re-read by id — full detail must match.
            get_resp = await client.get(f"/api/v1/proposals/{proposal_id}")
            assert get_resp.status_code == 200
            fetched = get_resp.json()
            assert fetched["id"] == proposal_id
            assert fetched["created_by"] == str(owner_id)
            assert fetched["tenant_id"] == str(tenant_id)

            # List shows it (summary only — no brief in summary view).
            list_resp = await client.get("/api/v1/proposals")
            assert list_resp.status_code == 200
            items = list_resp.json()["proposals"]
            assert len(items) == 1
            assert items[0]["id"] == proposal_id
            assert "brief" not in items[0]

        # Audit row recorded the creation.
        async with pool.acquire() as conn:
            audits = await conn.fetch(
                "select action from audit_log "
                "where tenant_id = $1 and action = 'proposal.created'",
                tenant_id,
            )
        assert len(audits) == 1
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_create_proposal_rejects_unknown_programme(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id = await _make_user(pool)
    try:
        _, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.post(
                "/api/v1/proposals",
                json={
                    "programme_id": "programme_that_does_not_exist",
                    "language": "tr",
                },
            )
        assert response.status_code == 422
        assert "does not exist" in response.json()["detail"]
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


# ── Proposals: cross-tenant guards ─────────────────────────────────────


async def test_get_proposal_404s_for_foreign_tenant(pool: asyncpg.Pool) -> None:
    """Tenant B's user must NOT see Tenant A's proposal (404, not 403)."""

    tenant_a, owner_a = await _make_user(pool)
    tenant_b, owner_b = await _make_user(pool)
    try:
        _, client_a = _build_client(pool=pool, user_id=owner_a)
        async with client_a:
            create_resp = await client_a.post(
                "/api/v1/proposals",
                json={"programme_id": "tubitak_1501", "language": "tr"},
            )
        proposal_id = create_resp.json()["id"]

        _, client_b = _build_client(pool=pool, user_id=owner_b)
        async with client_b:
            response = await client_b.get(f"/api/v1/proposals/{proposal_id}")
        assert response.status_code == 404
    finally:
        await _cleanup(pool, [tenant_a, tenant_b], [owner_a, owner_b])


# ── Proposals: update ──────────────────────────────────────────────────


async def test_patch_proposal_updates_only_supplied_fields(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id = await _make_user(pool)
    try:
        _, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            create = await client.post(
                "/api/v1/proposals",
                json={
                    "programme_id": "horizon_eu_ria",
                    "language": "en",
                    "title": "Original title",
                    "brief": {"summary": "v1"},
                },
            )
            proposal_id = create.json()["id"]

            # Patch only the brief — title must survive.
            patch = await client.patch(
                f"/api/v1/proposals/{proposal_id}",
                json={"brief": {"summary": "v2", "extra": "field"}},
            )
            assert patch.status_code == 200
            patched = patch.json()
            assert patched["title"] == "Original title"
            assert patched["brief"]["summary"] == "v2"
            assert patched["brief"]["extra"] == "field"

            # Status transition (draft → brief_complete) — allowed.
            patch_status = await client.patch(
                f"/api/v1/proposals/{proposal_id}",
                json={"status": "brief_complete"},
            )
            assert patch_status.status_code == 200
            assert patch_status.json()["status"] == "brief_complete"

            # Saga-managed status forbidden by Pydantic schema (422).
            patch_forbidden = await client.patch(
                f"/api/v1/proposals/{proposal_id}",
                json={"status": "generating"},
            )
            assert patch_forbidden.status_code == 422
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_patch_proposal_empty_body_rejected(pool: asyncpg.Pool) -> None:
    tenant_id, owner_id = await _make_user(pool)
    try:
        _, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            create = await client.post(
                "/api/v1/proposals",
                json={"programme_id": "kosgeb_arge", "language": "tr"},
            )
            patch = await client.patch(
                f"/api/v1/proposals/{create.json()['id']}",
                json={},
            )
        assert patch.status_code == 400
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


# ── Proposals: delete ──────────────────────────────────────────────────


async def test_delete_proposal_as_author_succeeds(pool: asyncpg.Pool) -> None:
    tenant_id, owner_id = await _make_user(pool, role="member")
    try:
        _, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            create = await client.post(
                "/api/v1/proposals",
                json={"programme_id": "tubitak_1501", "language": "tr"},
            )
            proposal_id = create.json()["id"]

            delete_resp = await client.delete(f"/api/v1/proposals/{proposal_id}")
            assert delete_resp.status_code == 204

            get_resp = await client.get(f"/api/v1/proposals/{proposal_id}")
            assert get_resp.status_code == 404
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_delete_proposal_as_non_author_member_forbidden(
    pool: asyncpg.Pool,
) -> None:
    """Two members of the same tenant — one creates, the other can't delete."""

    tenant_id, author_id = await _make_user(pool, role="member")
    _, other_member_id = await _make_user(
        pool, tenant_id=tenant_id, role="member"
    )
    try:
        _, author_client = _build_client(pool=pool, user_id=author_id)
        async with author_client:
            create = await author_client.post(
                "/api/v1/proposals",
                json={"programme_id": "horizon_eu_ria", "language": "en"},
            )
            proposal_id = create.json()["id"]

        _, other_client = _build_client(pool=pool, user_id=other_member_id)
        async with other_client:
            delete_resp = await other_client.delete(
                f"/api/v1/proposals/{proposal_id}"
            )
        assert delete_resp.status_code == 403
    finally:
        await _cleanup(pool, [tenant_id], [author_id, other_member_id])


async def test_delete_proposal_as_admin_succeeds(pool: asyncpg.Pool) -> None:
    """Admin who didn't create the proposal still allowed to delete."""

    tenant_id, author_id = await _make_user(pool, role="member")
    _, admin_id = await _make_user(pool, tenant_id=tenant_id, role="admin")
    try:
        _, author_client = _build_client(pool=pool, user_id=author_id)
        async with author_client:
            create = await author_client.post(
                "/api/v1/proposals",
                json={"programme_id": "tubitak_1501", "language": "tr"},
            )
            proposal_id = create.json()["id"]

        _, admin_client = _build_client(pool=pool, user_id=admin_id)
        async with admin_client:
            delete_resp = await admin_client.delete(
                f"/api/v1/proposals/{proposal_id}"
            )
        assert delete_resp.status_code == 204
    finally:
        await _cleanup(pool, [tenant_id], [author_id, admin_id])


# ── Calls ──────────────────────────────────────────────────────────────


async def test_create_and_list_call_manually(pool: asyncpg.Pool) -> None:
    """Pilot bridge — until the scraper lands, the operator seeds calls
    via POST /calls."""

    tenant_id, owner_id = await _make_user(pool)
    try:
        _, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            ext_id = f"test-{uuid.uuid4().hex[:8]}"
            create = await client.post(
                "/api/v1/calls",
                json={
                    "programme_id": "horizon_eu_ria",
                    "external_id": ext_id,
                    "title": "Test HE RIA call",
                    "language": "en",
                    "raw_metadata": {"_test_tenant": str(tenant_id)},
                },
            )
            assert create.status_code == 201, create.text
            body = create.json()
            assert body["source"] == "manual"
            assert body["status"] == "open"
            call_id = body["id"]

            # Duplicate external_id → 409.
            dup = await client.post(
                "/api/v1/calls",
                json={
                    "programme_id": "horizon_eu_ria",
                    "external_id": ext_id,
                    "title": "Duplicate",
                    "language": "en",
                    "raw_metadata": {"_test_tenant": str(tenant_id)},
                },
            )
            assert dup.status_code == 409

            # List + filter by programme.
            list_resp = await client.get(
                "/api/v1/calls", params={"programme_id": "horizon_eu_ria"}
            )
            assert list_resp.status_code == 200
            ids = {c["id"] for c in list_resp.json()["calls"]}
            assert call_id in ids
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])
