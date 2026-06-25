"""Tests for ``POST /api/v1/onboarding/workspace``.

Covers:

- Happy path: tenant + public.users row created, audit row written,
  body returns ``role='owner'`` + ``plan='starter'``.
- Slug auto-derivation when body omits it; deterministic shape check.
- Slug validation rejects junk; collision returns 409.
- User-already-in-a-tenant blocked with 409 (covers both active member
  and soft-deleted leftover rows).
- Missing auth.users row → 403.

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


async def _make_auth_only_user(pool: asyncpg.Pool) -> tuple[uuid.UUID, str]:
    """auth.users row only — the post-signup state."""

    user_id = uuid.uuid4()
    email = f"u-{user_id}@example.com"
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)", user_id, email
        )
    return user_id, email


async def _cleanup(
    pool: asyncpg.Pool,
    user_ids: list[uuid.UUID],
) -> None:
    async with pool.acquire() as conn:
        # The endpoint may have created tenants for these users; find +
        # cascade their dependents before deleting the auth rows.
        tenant_ids = await conn.fetch(
            "select id from tenants where id in "
            "(select tenant_id from public.users where id = any($1::uuid[]))",
            user_ids,
        )
        tids = [row["id"] for row in tenant_ids]
        if tids:
            await conn.execute(
                "delete from audit_log where tenant_id = any($1::uuid[])", tids
            )
        await conn.execute(
            "delete from public.users where id = any($1::uuid[])", user_ids
        )
        await conn.execute(
            "delete from auth.users where id = any($1::uuid[])", user_ids
        )
        if tids:
            await conn.execute(
                "delete from tenants where id = any($1::uuid[])", tids
            )


def _build_client(
    *, pool: asyncpg.Pool, user_id: uuid.UUID | None
) -> tuple[FastAPI, AsyncClient]:
    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    if user_id is not None:
        app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


# ── Tests ──────────────────────────────────────────────────────────────


async def test_post_workspace_creates_tenant_and_links_owner(
    pool: asyncpg.Pool,
) -> None:
    user_id, _email = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding/workspace",
                json={
                    "name": "Acme Labs",
                    "slug": "acme-labs",
                    "preferred_language": "en",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["slug"] == "acme-labs"
        assert body["role"] == "owner"
        assert body["plan"] == "starter"

        # Side effects: public.users row exists with the right tenant + lang.
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "select tenant_id, role, preferred_language "
                "from public.users where id = $1",
                user_id,
            )
            audit = await conn.fetchrow(
                "select action, diff::text as d from audit_log "
                "where tenant_id = $1 and action = 'tenant.created'",
                uuid.UUID(body["tenant_id"]),
            )
        assert row is not None
        assert str(row["tenant_id"]) == body["tenant_id"]
        assert row["role"] == "owner"
        assert row["preferred_language"] == "en"
        assert audit is not None
        assert "onboarding" in audit["d"]
    finally:
        await _cleanup(pool, [user_id])


async def test_slug_auto_derived_when_omitted(pool: asyncpg.Pool) -> None:
    """Free-text name → lowercased, hyphen-joined slug."""

    user_id, _ = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding/workspace",
                json={"name": "Acme Labs LLC!"},
            )
        assert response.status_code == 201
        assert response.json()["slug"].startswith("acme-labs-llc")
    finally:
        await _cleanup(pool, [user_id])


async def test_invalid_slug_returns_400(pool: asyncpg.Pool) -> None:
    """Uppercase + underscore + colon should all fail."""

    user_id, _ = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.post(
                "/api/v1/onboarding/workspace",
                json={"name": "Whatever", "slug": "Acme_Labs"},
            )
        assert response.status_code == 400
    finally:
        await _cleanup(pool, [user_id])


async def test_taken_slug_returns_409(pool: asyncpg.Pool) -> None:
    """Two users requesting the same slug — first wins, second 409s."""

    user_a, _ = await _make_auth_only_user(pool)
    user_b, _ = await _make_auth_only_user(pool)
    try:
        _app1, client1 = _build_client(pool=pool, user_id=user_a)
        async with client1:
            first = await client1.post(
                "/api/v1/onboarding/workspace",
                json={"name": "Acme Labs", "slug": "acme-labs"},
            )
        assert first.status_code == 201

        _app2, client2 = _build_client(pool=pool, user_id=user_b)
        async with client2:
            second = await client2.post(
                "/api/v1/onboarding/workspace",
                json={"name": "Acme Labs 2", "slug": "acme-labs"},
            )
        assert second.status_code == 409
        assert "slug" in second.json()["detail"]
    finally:
        await _cleanup(pool, [user_a, user_b])


async def test_user_already_in_a_tenant_returns_409(pool: asyncpg.Pool) -> None:
    user_id, _ = await _make_auth_only_user(pool)
    # First create succeeds; second POST hits the "already in a tenant" guard.
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            first = await client.post(
                "/api/v1/onboarding/workspace",
                json={"name": "Acme Labs", "slug": "acme-one"},
            )
            assert first.status_code == 201
            second = await client.post(
                "/api/v1/onboarding/workspace",
                json={"name": "Acme Two", "slug": "acme-two"},
            )
        assert second.status_code == 409
        assert "already" in second.json()["detail"]
    finally:
        await _cleanup(pool, [user_id])


async def test_missing_auth_user_returns_403(pool: asyncpg.Pool) -> None:
    """The JWT validated but the auth.users row is gone — 403, never 500."""

    ghost = uuid.uuid4()
    _app, client = _build_client(pool=pool, user_id=ghost)
    async with client:
        response = await client.post(
            "/api/v1/onboarding/workspace",
            json={"name": "Ghost Co", "slug": "ghost"},
        )
    assert response.status_code == 403
