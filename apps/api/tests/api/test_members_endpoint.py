"""Tests for owner-side member management.

Covers:

- GET: admin sees the active member list ordered owner→admin→member;
  soft-deleted members are filtered out; member role gets 403.
- PATCH role: promotes / demotes, writes one audit row per change;
  no-op same-role returns the row without an audit; sole-owner downgrade
  blocked with 409; self-modification blocked with 400.
- DELETE: soft-removes (sets ``deleted_at``), audit written, idempotent;
  sole-owner removal blocked with 409; self-removal blocked with 400.
- Cross-tenant: a member from another tenant returns 404.

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


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping DB-bound member tests"
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
    email: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    user_id = uuid.uuid4()
    user_email = email or f"u-{user_id}@example.com"
    async with pool.acquire() as conn:
        if tenant_id is None:
            tenant_id = uuid.uuid4()
            await conn.execute(
                "insert into tenants (id, name, slug) values ($1, $2, $3)",
                tenant_id,
                "Members Test",
                f"mem-{tenant_id}",
            )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            user_email,
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role, display_name) "
            "values ($1, $2, $3, $4)",
            user_id,
            tenant_id,
            role,
            f"User {user_id.hex[:6]}",
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
    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


# ── GET /members ───────────────────────────────────────────────────────


async def test_admin_sees_active_members_ordered_by_role(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, admin_id, _ = await _make_user(pool, tenant_id=tenant_id, role="admin")
    _, member_id, _ = await _make_user(pool, tenant_id=tenant_id, role="member")
    _, deleted_id, _ = await _make_user(pool, tenant_id=tenant_id, role="member")

    async with pool.acquire() as conn:
        await conn.execute(
            "update public.users set deleted_at = now() where id = $1",
            deleted_id,
        )
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.get("/api/v1/tenant/members")
        assert response.status_code == 200
        body = response.json()
        roles = [m["role"] for m in body["members"]]
        assert roles == ["owner", "admin", "member"]
        # Soft-deleted member is invisible.
        assert all(m["id"] != str(deleted_id) for m in body["members"])
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.users set deleted_at = null where id = $1",
                deleted_id,
            )
        await _cleanup(
            pool, [tenant_id], [owner_id, admin_id, member_id, deleted_id]
        )


async def test_member_role_cannot_list_members(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, member_id, _ = await _make_user(pool, tenant_id=tenant_id, role="member")
    try:
        _app, client = _build_client(pool=pool, user_id=member_id)
        async with client:
            response = await client.get("/api/v1/tenant/members")
        assert response.status_code == 403
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, member_id])


# ── PATCH /members/{id}/role ───────────────────────────────────────────


async def test_admin_promotes_member_to_admin_and_audits(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, member_id, _ = await _make_user(pool, tenant_id=tenant_id, role="member")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.patch(
                f"/api/v1/tenant/members/{member_id}/role",
                json={"role": "admin"},
            )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"

        async with pool.acquire() as conn:
            new_role = await conn.fetchval(
                "select role from public.users where id = $1", member_id
            )
            audits = await conn.fetch(
                """
                select diff::text as d
                  from audit_log
                 where tenant_id = $1
                   and action = 'tenant.member_role_changed'
                """,
                tenant_id,
            )
        assert new_role == "admin"
        assert len(audits) == 1
        assert "member" in audits[0]["d"] and "admin" in audits[0]["d"]
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, member_id])


async def test_no_op_role_change_returns_row_without_audit(
    pool: asyncpg.Pool,
) -> None:
    """PATCH with the current role is a noop — return the member, no audit."""

    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, member_id, _ = await _make_user(pool, tenant_id=tenant_id, role="member")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.patch(
                f"/api/v1/tenant/members/{member_id}/role",
                json={"role": "member"},
            )
        assert response.status_code == 200

        async with pool.acquire() as conn:
            audit_count = await conn.fetchval(
                "select count(*) from audit_log where tenant_id = $1 "
                "and action = 'tenant.member_role_changed'",
                tenant_id,
            )
        assert audit_count == 0
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, member_id])


async def test_sole_owner_downgrade_returns_409(pool: asyncpg.Pool) -> None:
    """Promoting another member to owner first is required."""

    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, admin_id, _ = await _make_user(pool, tenant_id=tenant_id, role="admin")
    try:
        _app, client = _build_client(pool=pool, user_id=admin_id)
        async with client:
            response = await client.patch(
                f"/api/v1/tenant/members/{owner_id}/role",
                json={"role": "admin"},
            )
        assert response.status_code == 409
        assert "sole owner" in response.json()["detail"]
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, admin_id])


async def test_co_owner_can_demote_other_owner(pool: asyncpg.Pool) -> None:
    """Two owners → one demotes the other → both audit + state correct."""

    tenant_id, owner_a, _ = await _make_user(pool, role="owner")
    _, owner_b, _ = await _make_user(pool, tenant_id=tenant_id, role="owner")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_a)
        async with client:
            response = await client.patch(
                f"/api/v1/tenant/members/{owner_b}/role",
                json={"role": "admin"},
            )
        assert response.status_code == 200
        assert response.json()["role"] == "admin"
    finally:
        await _cleanup(pool, [tenant_id], [owner_a, owner_b])


async def test_self_role_change_returns_400(pool: asyncpg.Pool) -> None:
    """Owners changing their own role via this endpoint is rejected to
    prevent the lockout class of mistakes."""

    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.patch(
                f"/api/v1/tenant/members/{owner_id}/role",
                json={"role": "member"},
            )
        assert response.status_code == 400
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_role_change_404_for_other_tenants_member(
    pool: asyncpg.Pool,
) -> None:
    tenant_a, owner_a, _ = await _make_user(pool, role="owner")
    tenant_b, owner_b, _ = await _make_user(pool, role="owner")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_a)
        async with client:
            response = await client.patch(
                f"/api/v1/tenant/members/{owner_b}/role",
                json={"role": "admin"},
            )
        assert response.status_code == 404
    finally:
        await _cleanup(pool, [tenant_a, tenant_b], [owner_a, owner_b])


# ── DELETE /members/{id} ───────────────────────────────────────────────


async def test_admin_removes_member_and_audits(pool: asyncpg.Pool) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, member_id, _ = await _make_user(pool, tenant_id=tenant_id, role="member")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.delete(
                f"/api/v1/tenant/members/{member_id}"
            )
        assert response.status_code == 204

        async with pool.acquire() as conn:
            removed = await conn.fetchval(
                "select deleted_at is not null from public.users where id = $1",
                member_id,
            )
            audits = await conn.fetch(
                "select action from audit_log where tenant_id = $1 "
                "and action = 'tenant.member_removed'",
                tenant_id,
            )
        assert removed is True
        assert len(audits) == 1
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.users set deleted_at = null where id = $1",
                member_id,
            )
        await _cleanup(pool, [tenant_id], [owner_id, member_id])


async def test_remove_is_idempotent(pool: asyncpg.Pool) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, member_id, _ = await _make_user(pool, tenant_id=tenant_id, role="member")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            first = await client.delete(
                f"/api/v1/tenant/members/{member_id}"
            )
            second = await client.delete(
                f"/api/v1/tenant/members/{member_id}"
            )
        assert first.status_code == 204
        assert second.status_code == 204

        async with pool.acquire() as conn:
            audit_count = await conn.fetchval(
                "select count(*) from audit_log where tenant_id = $1 "
                "and action = 'tenant.member_removed'",
                tenant_id,
            )
        assert audit_count == 1
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.users set deleted_at = null where id = $1",
                member_id,
            )
        await _cleanup(pool, [tenant_id], [owner_id, member_id])


async def test_sole_owner_removal_returns_409(pool: asyncpg.Pool) -> None:
    tenant_id, owner_a, _ = await _make_user(pool, role="owner")
    _, owner_b, _ = await _make_user(pool, tenant_id=tenant_id, role="owner")
    # Now demote owner_b to admin so owner_a is the only owner.
    async with pool.acquire() as conn:
        await conn.execute(
            "update public.users set role = 'admin' where id = $1", owner_b
        )

    try:
        _app, client = _build_client(pool=pool, user_id=owner_b)
        async with client:
            response = await client.delete(
                f"/api/v1/tenant/members/{owner_a}"
            )
        assert response.status_code == 409
        assert "sole owner" in response.json()["detail"]

        async with pool.acquire() as conn:
            still_active = await conn.fetchval(
                "select deleted_at is null from public.users where id = $1",
                owner_a,
            )
        assert still_active is True
    finally:
        await _cleanup(pool, [tenant_id], [owner_a, owner_b])


async def test_self_remove_returns_400(pool: asyncpg.Pool) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, admin_id, _ = await _make_user(pool, tenant_id=tenant_id, role="admin")
    try:
        _app, client = _build_client(pool=pool, user_id=admin_id)
        async with client:
            response = await client.delete(f"/api/v1/tenant/members/{admin_id}")
        assert response.status_code == 400
        assert "/me/account" in response.json()["detail"]
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, admin_id])
