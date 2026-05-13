"""Tests for the self-service onboarding endpoint.

Covers:

- POST /api/v1/onboarding creates a tenant + public.users row atomically
  and writes a ``tenant.created`` audit event.
- 409 if the caller already belongs to a tenant (duplicate user row).
- 409 if the slug is already taken (unique constraint violation).
- 422 if the slug format is invalid.
- 422 if the request body is missing or has bad fields.

Skips when ``TEST_DATABASE_URL`` is unset.
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
            "TEST_DATABASE_URL not set — skipping DB-bound onboarding tests"
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


async def _make_auth_only_user(
    pool: asyncpg.Pool, *, email: str | None = None
) -> tuple[uuid.UUID, str]:
    """Create an auth.users row only — user who signed up but never onboarded."""
    user_id = uuid.uuid4()
    user_email = email or f"u-{user_id}@example.com"
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            user_email,
        )
    return user_id, user_email


async def _make_full_user(
    pool: asyncpg.Pool,
    *,
    role: str = "owner",
    email: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Create a tenant + auth.users + public.users (already onboarded)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user_email = email or f"u-{user_id}@example.com"
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "Existing Tenant",
            f"existing-{tenant_id}",
        )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            user_email,
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role) values ($1, $2, $3)",
            user_id,
            tenant_id,
            role,
        )
    return tenant_id, user_id, user_email


async def _cleanup(
    pool: asyncpg.Pool,
    tenant_ids: list[uuid.UUID],
    user_ids: list[uuid.UUID],
) -> None:
    async with pool.acquire() as conn:
        if tenant_ids:
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
    """Build app + httpx client with dependency overrides."""
    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


# ── Happy path ─────────────────────────────────────────────────────────


async def test_onboarding_creates_tenant_and_user(pool: asyncpg.Pool) -> None:
    """New auth-only user creates workspace → 201, tenant + user created."""
    user_id, user_email = await _make_auth_only_user(pool)
    slug = f"test-{user_id.hex[:12]}"
    created_tenant_ids: list[uuid.UUID] = []
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding",
                json={
                    "name": "My Test Workspace",
                    "slug": slug,
                    "preferred_language": "en",
                },
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["tenant_name"] == "My Test Workspace"
        assert body["tenant_slug"] == slug
        assert body["role"] == "owner"
        assert body["user_id"] == str(user_id)
        created_tenant_ids.append(uuid.UUID(body["tenant_id"]))

        # Verify DB state
        async with pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "select tenant_id, role, preferred_language from public.users where id = $1",
                user_id,
            )
            tenant_row = await conn.fetchrow(
                "select name, slug, plan from tenants where id = $1",
                created_tenant_ids[0],
            )
            audit_rows = await conn.fetch(
                "select action, diff::text as d from audit_log where tenant_id = $1",
                created_tenant_ids[0],
            )

        assert user_row is not None
        assert user_row["role"] == "owner"
        assert user_row["preferred_language"] == "en"
        assert tenant_row is not None
        assert tenant_row["name"] == "My Test Workspace"
        assert tenant_row["slug"] == slug
        assert tenant_row["plan"] == "starter"  # default plan
        assert len(audit_rows) == 1
        assert audit_rows[0]["action"] == "tenant.created"
    finally:
        await _cleanup(pool, created_tenant_ids, [user_id])


async def test_onboarding_defaults_to_turkish(pool: asyncpg.Pool) -> None:
    """When preferred_language is omitted, defaults to 'tr'."""
    user_id, _ = await _make_auth_only_user(pool)
    slug = f"tr-{user_id.hex[:12]}"
    created_tenant_ids: list[uuid.UUID] = []
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding",
                json={"name": "Türkçe Workspace", "slug": slug},
            )
        assert response.status_code == 201
        created_tenant_ids.append(uuid.UUID(response.json()["tenant_id"]))

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "select preferred_language from public.users where id = $1",
                user_id,
            )
        assert row is not None
        assert row["preferred_language"] == "tr"
    finally:
        await _cleanup(pool, created_tenant_ids, [user_id])


# ── Conflict cases ─────────────────────────────────────────────────────


async def test_onboarding_409_if_already_has_tenant(pool: asyncpg.Pool) -> None:
    """User who already belongs to a workspace cannot create another one."""
    tenant_id, user_id, _ = await _make_full_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding",
                json={"name": "Second WS", "slug": f"s-{user_id.hex[:12]}"},
            )
        assert response.status_code == 409
        assert "already belongs" in response.json()["detail"]
    finally:
        await _cleanup(pool, [tenant_id], [user_id])


async def test_onboarding_409_if_slug_already_taken(pool: asyncpg.Pool) -> None:
    """Duplicate slug → 409 with meaningful error message."""
    user_a_id, _ = await _make_auth_only_user(pool)
    user_b_id, _ = await _make_auth_only_user(pool)
    shared_slug = f"dup-{uuid.uuid4().hex[:10]}"
    created_tenant_ids: list[uuid.UUID] = []
    try:
        # First user takes the slug
        _app, client_a = _build_client(pool=pool, user_id=user_a_id)
        async with client_a:
            resp_a = await client_a.post(
                "/api/v1/onboarding",
                json={"name": "First", "slug": shared_slug},
            )
        assert resp_a.status_code == 201
        created_tenant_ids.append(uuid.UUID(resp_a.json()["tenant_id"]))

        # Second user attempts the same slug
        _app2, client_b = _build_client(pool=pool, user_id=user_b_id)
        async with client_b:
            resp_b = await client_b.post(
                "/api/v1/onboarding",
                json={"name": "Second", "slug": shared_slug},
            )
        assert resp_b.status_code == 409
        assert "slug" in resp_b.json()["detail"]
    finally:
        await _cleanup(pool, created_tenant_ids, [user_a_id, user_b_id])


# ── Validation errors ─────────────────────────────────────────────────


async def test_onboarding_422_invalid_slug_format(pool: asyncpg.Pool) -> None:
    """Slug with uppercase / special characters → 422."""
    user_id, _ = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding",
                json={"name": "Valid Name", "slug": "INVALID SLUG!!!"},
            )
        assert response.status_code == 422
    finally:
        await _cleanup(pool, [], [user_id])


async def test_onboarding_422_slug_too_short(pool: asyncpg.Pool) -> None:
    """Slug shorter than 3 chars → 422."""
    user_id, _ = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding",
                json={"name": "Valid Name", "slug": "ab"},
            )
        assert response.status_code == 422
    finally:
        await _cleanup(pool, [], [user_id])


async def test_onboarding_422_missing_name(pool: asyncpg.Pool) -> None:
    """Missing required 'name' field → 422."""
    user_id, _ = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding",
                json={"slug": "valid-slug"},
            )
        assert response.status_code == 422
    finally:
        await _cleanup(pool, [], [user_id])


async def test_onboarding_422_invalid_language(pool: asyncpg.Pool) -> None:
    """Language other than 'tr' or 'en' → 422."""
    user_id, _ = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding",
                json={"name": "Valid", "slug": "valid-slug", "preferred_language": "fr"},
            )
        assert response.status_code == 422
    finally:
        await _cleanup(pool, [], [user_id])


async def test_onboarding_rejects_extra_fields(pool: asyncpg.Pool) -> None:
    """Extra fields → 422 (model has extra='forbid')."""
    user_id, _ = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding",
                json={
                    "name": "Valid",
                    "slug": "valid-slug-extra",
                    "plan": "enterprise",  # not allowed
                },
            )
        assert response.status_code == 422
    finally:
        await _cleanup(pool, [], [user_id])
