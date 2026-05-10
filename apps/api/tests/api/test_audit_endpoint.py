"""Tests for ``GET /api/v1/tenant/audit-log``.

Covers:
- Member role gets 403 (admin-only).
- Empty tenant returns ``entries=[]`` and ``next_before=null``.
- Seeded rows return newest-first; ``user_id`` / ``ip_address`` /
  ``diff`` round-trip correctly.
- ``action`` and ``resource_type`` query filters narrow the result.
- Cursor pagination with ``before`` walks pages and stops at the end.
- Cross-tenant isolation: audit rows for tenant B never leak to A.

Skips when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.audit import write_audit_event
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
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound audit tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


async def _create_tenant_with_user(
    pool: asyncpg.Pool, *, role: str
) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            f"audit-{role}-test",
            f"audit-{role}-{tenant_id}",
        )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"{role}-{user_id}@test",
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role) values ($1, $2, $3)",
            user_id,
            tenant_id,
            role,
        )
    return tenant_id, user_id


async def _cleanup_tenant(pool: asyncpg.Pool, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute("delete from audit_log where tenant_id = $1", tenant_id)
        await conn.execute("delete from public.users where id = $1", user_id)
        await conn.execute("delete from auth.users where id = $1", user_id)
        await conn.execute("delete from tenants where id = $1", tenant_id)


@pytest.fixture
async def admin_setup(pool: asyncpg.Pool) -> AsyncIterator[dict[str, Any]]:
    tenant_id, user_id = await _create_tenant_with_user(pool, role="owner")
    try:
        yield {"tenant_id": tenant_id, "user_id": user_id, "pool": pool}
    finally:
        await _cleanup_tenant(pool, tenant_id, user_id)


@pytest.fixture
async def member_setup(pool: asyncpg.Pool) -> AsyncIterator[dict[str, Any]]:
    tenant_id, user_id = await _create_tenant_with_user(pool, role="member")
    try:
        yield {"tenant_id": tenant_id, "user_id": user_id, "pool": pool}
    finally:
        await _cleanup_tenant(pool, tenant_id, user_id)


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


# ── Tests ──────────────────────────────────────────────────────────────


async def test_member_role_gets_403(
    member_setup: dict[str, Any],
) -> None:
    _app, client = _build_client(
        pool=member_setup["pool"], user_id=member_setup["user_id"]
    )
    async with client:
        response = await client.get("/api/v1/tenant/audit-log")
    assert response.status_code == 403
    assert "owner/admin" in response.json()["detail"]


async def test_empty_tenant_returns_empty_page(
    admin_setup: dict[str, Any],
) -> None:
    _app, client = _build_client(
        pool=admin_setup["pool"], user_id=admin_setup["user_id"]
    )
    async with client:
        response = await client.get("/api/v1/tenant/audit-log")
    assert response.status_code == 200
    assert response.json() == {"entries": [], "next_before": None}


async def test_seeded_rows_returned_newest_first_and_round_trip(
    admin_setup: dict[str, Any],
) -> None:
    pool = admin_setup["pool"]
    tenant_id = admin_setup["tenant_id"]
    user_id = admin_setup["user_id"]

    async with pool.acquire() as conn:
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="tenant.llm_config_updated",
            resource_type="tenant_llm_config",
            resource_id=tenant_id,
            diff={"anthropic": "set"},
            ip_address="10.0.0.5",
            user_agent="ua-1",
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="proposal.exported",
            resource_type="proposal",
            resource_id=uuid.uuid4(),
            diff={"format": "docx"},
            ip_address=None,
            user_agent="ua-2",
        )

    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        response = await client.get("/api/v1/tenant/audit-log")

    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 2
    # Newest first — proposal.exported came in second, so it's the head.
    assert body["entries"][0]["action"] == "proposal.exported"
    assert body["entries"][1]["action"] == "tenant.llm_config_updated"
    assert body["entries"][1]["diff"] == {"anthropic": "set"}
    assert body["entries"][1]["ip_address"] == "10.0.0.5"
    assert body["entries"][1]["resource_type"] == "tenant_llm_config"
    assert body["next_before"] is None


async def test_action_filter_narrows_result(
    admin_setup: dict[str, Any],
) -> None:
    pool = admin_setup["pool"]
    tenant_id = admin_setup["tenant_id"]
    user_id = admin_setup["user_id"]

    async with pool.acquire() as conn:
        for action in (
            "tenant.llm_config_updated",
            "proposal.exported",
            "tenant.llm_config_updated",
        ):
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action=action,
                diff={"k": "v"},
            )

    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        response = await client.get(
            "/api/v1/tenant/audit-log?action=tenant.llm_config_updated"
        )
    body = response.json()
    assert len(body["entries"]) == 2
    for entry in body["entries"]:
        assert entry["action"] == "tenant.llm_config_updated"


async def test_resource_type_filter_narrows_result(
    admin_setup: dict[str, Any],
) -> None:
    pool = admin_setup["pool"]
    tenant_id = admin_setup["tenant_id"]
    user_id = admin_setup["user_id"]

    async with pool.acquire() as conn:
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="tenant.llm_config_updated",
            resource_type="tenant_llm_config",
            diff={"k": "v"},
        )
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="proposal.exported",
            resource_type="proposal",
            diff={"k": "v"},
        )

    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        response = await client.get(
            "/api/v1/tenant/audit-log?resource_type=proposal"
        )
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["resource_type"] == "proposal"


async def test_cursor_pagination_walks_pages_and_stops(
    admin_setup: dict[str, Any],
) -> None:
    """Insert 7 rows, page in batches of 3 — three pages of 3+3+1, then done."""

    pool = admin_setup["pool"]
    tenant_id = admin_setup["tenant_id"]
    user_id = admin_setup["user_id"]

    async with pool.acquire() as conn:
        for i in range(7):
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action=f"action_{i}",
                diff={"i": i},
            )

    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        # First page.
        page1 = (await client.get("/api/v1/tenant/audit-log?limit=3")).json()
        assert len(page1["entries"]) == 3
        assert page1["next_before"] is not None

        # Second page.
        page2 = (
            await client.get(
                "/api/v1/tenant/audit-log",
                params={"limit": 3, "before": page1["next_before"]},
            )
        ).json()
        assert len(page2["entries"]) == 3
        assert page2["next_before"] is not None

        # Last page.
        page3 = (
            await client.get(
                "/api/v1/tenant/audit-log",
                params={"limit": 3, "before": page2["next_before"]},
            )
        ).json()
        assert len(page3["entries"]) == 1
        assert page3["next_before"] is None

    # Sanity: across all pages, every event is unique and spans 0..6.
    seen = {
        e["action"]
        for page in (page1, page2, page3)
        for e in page["entries"]
    }
    assert seen == {f"action_{i}" for i in range(7)}


async def test_cross_tenant_isolation(
    admin_setup: dict[str, Any], pool: asyncpg.Pool
) -> None:
    """Audit rows under a foreign tenant must never appear for the caller."""

    tenant_id = admin_setup["tenant_id"]
    user_id = admin_setup["user_id"]
    foreign_tenant, foreign_user = await _create_tenant_with_user(pool, role="owner")
    try:
        async with pool.acquire() as conn:
            await write_audit_event(
                conn,
                tenant_id=foreign_tenant,
                user_id=foreign_user,
                action="foreign_event_should_not_leak",
                diff={"x": 1},
            )
            await write_audit_event(
                conn,
                tenant_id=tenant_id,
                user_id=user_id,
                action="own_event",
                diff={"x": 2},
            )

        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            body = (await client.get("/api/v1/tenant/audit-log")).json()
        assert len(body["entries"]) == 1
        assert body["entries"][0]["action"] == "own_event"
    finally:
        await _cleanup_tenant(pool, foreign_tenant, foreign_user)


async def test_limit_validation_rejects_too_large_value(
    admin_setup: dict[str, Any],
) -> None:
    _app, client = _build_client(
        pool=admin_setup["pool"], user_id=admin_setup["user_id"]
    )
    async with client:
        response = await client.get("/api/v1/tenant/audit-log?limit=500")
    assert response.status_code == 422
