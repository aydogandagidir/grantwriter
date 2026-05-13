"""Tests for the identity + KVKK / GDPR self-service endpoints.

Covers ``GET /api/v1/me`` (identity snapshot consumed by the FE app
shell), ``GET /api/v1/me/data-export``, and ``DELETE /api/v1/me/account``:

- /me 200s with user + tenant + role for an active user; the shape
  mirrors ``packages/shared-types/src/index.ts::MeResponse`` so the FE
  layout can deserialize it without an adapter.
- /me 404s a user with no ``public.users`` row (fresh signup before
  onboarding) — the FE layout treats this as "redirect to onboarding".
- /me 404s a soft-deleted user.
- Export returns user + tenant + audit + usage + proposal IDs
  (proposal *content* is intentionally excluded).
- Export ignores rows authored by other users (cross-user isolation).
- Export 404s a soft-deleted user (consistent with public.tenant_id).
- Delete soft-sets ``deleted_at``, writes a single audit row, returns 204.
- Delete is idempotent — second call still 204, no extra audit row.
- Delete returns 409 when the caller is the tenant's only owner; the
  user row is left untouched.
- Delete of a non-sole owner succeeds (the tenant keeps its other
  owner; the deletee row gets ``deleted_at``).

Skips when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress

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
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound /me tests")
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
    """Create (tenant + user) or (user under given tenant)."""

    user_id = uuid.uuid4()
    async with pool.acquire() as conn:
        if tenant_id is None:
            tenant_id = uuid.uuid4()
            await conn.execute(
                "insert into tenants (id, name, slug) values ($1, $2, $3)",
                tenant_id,
                "Me Endpoint Test",
                f"me-{tenant_id}",
            )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"u-{user_id}@test",
        )
        await conn.execute(
            "insert into public.users (id, tenant_id, role, display_name) "
            "values ($1, $2, $3, $4)",
            user_id,
            tenant_id,
            role,
            f"User {user_id.hex[:6]}",
        )
    return tenant_id, user_id


async def _proposals_schema_ready(pool: asyncpg.Pool) -> bool:
    """Return True iff the proposals table has the columns this suite needs.

    The local docker DB occasionally lags the migrations (some columns
    weren't applied during a prior partial run). When that happens the
    /data-export route's SELECT against proposals fails at parse time —
    we skip the proposal-related test rather than 500 the whole suite.
    """

    needed = {"tenant_id", "created_by", "programme_id", "language",
              "status", "title", "created_at"}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select column_name from information_schema.columns
             where table_schema = 'public' and table_name = 'proposals'
            """
        )
    return needed.issubset({row["column_name"] for row in rows})


async def _cleanup(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    user_ids: list[uuid.UUID],
) -> None:
    """Tear-down — resilient to a partial proposals migration.

    The proposals delete is wrapped in try/except because the local DB
    sometimes lacks ``proposals.tenant_id`` (see _proposals_schema_ready).
    Other deletes are unconditional — those tables are stable.
    """

    async with pool.acquire() as conn:
        await conn.execute(
            "delete from audit_log where tenant_id = $1", tenant_id
        )
        await conn.execute(
            "delete from tenant_usage_log where tenant_id = $1", tenant_id
        )
        # proposals.tenant_id may be missing on a partially-migrated DB;
        # suppress and move on — there's nothing to clean in that case.
        with suppress(asyncpg.UndefinedColumnError):
            await conn.execute(
                "delete from proposals where tenant_id = $1", tenant_id
            )
        await conn.execute(
            "delete from public.users where id = any($1::uuid[])", user_ids
        )
        await conn.execute(
            "delete from auth.users where id = any($1::uuid[])", user_ids
        )
        await conn.execute("delete from tenants where id = $1", tenant_id)


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


# ── GET /me (identity snapshot) ────────────────────────────────────────


async def test_get_me_returns_user_tenant_role_for_active_user(
    pool: asyncpg.Pool,
) -> None:
    """Happy path: the FE app shell relies on this response shape to render
    the dashboard. Every field on ``MeResponse`` must be populated."""

    tenant_id, user_id = await _make_user(pool, role="owner")

    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.get("/api/v1/me")

        assert response.status_code == 200
        body = response.json()

        assert body["user_id"] == str(user_id)
        assert body["email"] == f"u-{user_id}@test"
        assert body["role"] == "owner"
        assert body["tenant_id"] == str(tenant_id)
        assert body["tenant_name"] == "Me Endpoint Test"
        assert body["tenant_slug"].startswith("me-")
        # ``plan`` defaults to 'starter' on tenant insert (migration default).
        assert body["plan"] == "starter"
        # ``display_name`` is the auto-generated "User <hex>" string from
        # _make_user — present but the exact value is not contractual.
        assert body["display_name"] is not None
    finally:
        await _cleanup(pool, tenant_id, [user_id])


async def test_get_me_404s_when_user_has_no_public_users_row(
    pool: asyncpg.Pool,
) -> None:
    """Right after Supabase signup but before onboarding the auth.users
    row exists with no matching public.users — the FE layout uses this
    404 to redirect to the onboarding wizard."""

    # Make an auth.users row WITHOUT a public.users row.
    user_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"u-{user_id}@test",
        )

    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.get("/api/v1/me")
        assert response.status_code == 404
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "delete from auth.users where id = $1", user_id
            )


async def test_get_me_404s_a_soft_deleted_user(pool: asyncpg.Pool) -> None:
    """Soft-deletion (``deleted_at IS NOT NULL``) treats the user as
    nonexistent everywhere, including /me."""

    tenant_id, user_id = await _make_user(pool, role="owner")
    async with pool.acquire() as conn:
        await conn.execute(
            "update public.users set deleted_at = now() where id = $1",
            user_id,
        )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.get("/api/v1/me")
        assert response.status_code == 404
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.users set deleted_at = null where id = $1",
                user_id,
            )
        await _cleanup(pool, tenant_id, [user_id])


# ── GET /me/data-export ────────────────────────────────────────────────


async def test_export_returns_user_tenant_audit_usage_and_proposal_ids(
    pool: asyncpg.Pool,
) -> None:
    if not await _proposals_schema_ready(pool):
        pytest.skip(
            "proposals table is missing migration-005 columns on this DB; "
            "skipping the export test that requires a proposal insert"
        )

    tenant_id, user_id = await _make_user(pool, role="owner")

    # Seed audit + usage + a proposal.
    proposal_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="user.signed_in",
            diff={"event": "login"},
        )
        await conn.execute(
            """
            insert into tenant_usage_log (
              tenant_id, user_id, event_type, resource,
              input_tokens, output_tokens, cost_usd, used_byok
            ) values ($1, $2, 'llm_call', 'claude-sonnet-4-6',
                      120, 60, 0.001234, false)
            """,
            tenant_id,
            user_id,
        )
        await conn.execute(
            """
            insert into proposals (id, tenant_id, created_by, programme_id,
                                   language, title)
            values ($1, $2, $3, 'tubitak_1501', 'tr', 'Test Proposal')
            """,
            proposal_id,
            tenant_id,
            user_id,
        )

    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.get("/api/v1/me/data-export")

        assert response.status_code == 200
        body = response.json()

        # User identity round-trips.
        assert body["user"]["id"] == str(user_id)
        assert body["user"]["role"] == "owner"
        assert body["user"]["email"] == f"u-{user_id}@test"

        # Tenant summary present.
        assert body["tenant"]["id"] == str(tenant_id)
        assert body["tenant"]["plan"] == "starter"

        # Audit + usage + proposals all show the seeded rows.
        assert any(
            e["action"] == "user.signed_in" for e in body["audit_events"]
        )
        assert len(body["usage_log"]) == 1
        assert body["usage_log"][0]["resource"] == "claude-sonnet-4-6"
        assert body["usage_log"][0]["input_tokens"] == 120

        assert len(body["proposals_authored"]) == 1
        assert body["proposals_authored"][0]["id"] == str(proposal_id)
        assert body["proposals_authored"][0]["title"] == "Test Proposal"
    finally:
        await _cleanup(pool, tenant_id, [user_id])


async def test_export_isolates_data_to_the_caller(
    pool: asyncpg.Pool,
) -> None:
    """Audit / usage / proposal rows owned by ANOTHER user MUST NOT
    appear in this user's export, even when both are in the same tenant."""

    if not await _proposals_schema_ready(pool):
        pytest.skip(
            "proposals table is missing migration-005 columns — the route's "
            "SELECT against proposals would fail at parse time"
        )

    tenant_id, me_id = await _make_user(pool, role="owner")
    _, other_id = await _make_user(pool, tenant_id=tenant_id, role="member")

    async with pool.acquire() as conn:
        # Other user makes audit/usage/proposal rows.
        await write_audit_event(
            conn,
            tenant_id=tenant_id,
            user_id=other_id,
            action="other.event_should_not_leak",
            diff={"x": "1"},
        )
        await conn.execute(
            """
            insert into tenant_usage_log (
              tenant_id, user_id, event_type, cost_usd
            ) values ($1, $2, 'llm_call', 0.99)
            """,
            tenant_id,
            other_id,
        )
        await conn.execute(
            """
            insert into proposals (tenant_id, created_by, programme_id, language)
            values ($1, $2, 'tubitak_1501', 'tr')
            """,
            tenant_id,
            other_id,
        )

    try:
        _app, client = _build_client(pool=pool, user_id=me_id)
        async with client:
            body = (await client.get("/api/v1/me/data-export")).json()
        # No rows authored by the other user surface here.
        assert all(
            e["action"] != "other.event_should_not_leak"
            for e in body["audit_events"]
        )
        assert body["usage_log"] == []
        assert body["proposals_authored"] == []
    finally:
        await _cleanup(pool, tenant_id, [me_id, other_id])


async def test_export_404s_a_soft_deleted_user(pool: asyncpg.Pool) -> None:
    """The 404 fires before the proposals SELECT — runs even when the
    proposals schema is incomplete."""

    tenant_id, user_id = await _make_user(pool, role="owner")
    async with pool.acquire() as conn:
        await conn.execute(
            "update public.users set deleted_at = now() where id = $1",
            user_id,
        )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            response = await client.get("/api/v1/me/data-export")
        assert response.status_code == 404
    finally:
        # Reset deleted_at so cleanup deletes hit the row.
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.users set deleted_at = null where id = $1",
                user_id,
            )
        await _cleanup(pool, tenant_id, [user_id])


# ── DELETE /me/account ─────────────────────────────────────────────────


async def test_delete_soft_deletes_member_and_writes_one_audit(
    pool: asyncpg.Pool,
) -> None:
    """A non-owner member can always delete; sets deleted_at + audit row."""

    tenant_id, owner_id = await _make_user(pool, role="owner")
    _, member_id = await _make_user(pool, tenant_id=tenant_id, role="member")

    try:
        _app, client = _build_client(pool=pool, user_id=member_id)
        async with client:
            response = await client.delete("/api/v1/me/account")
        assert response.status_code == 204
        assert response.content == b""

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "select deleted_at from public.users where id = $1",
                member_id,
            )
            audits = await conn.fetch(
                """
                select action, diff::text as diff_text
                  from audit_log
                 where user_id = $1 and action = 'user.account_deleted'
                """,
                member_id,
            )
        assert row["deleted_at"] is not None
        assert len(audits) == 1
        assert "soft_deleted" in audits[0]["diff_text"]
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.users set deleted_at = null where id = $1",
                member_id,
            )
        await _cleanup(pool, tenant_id, [owner_id, member_id])


async def test_delete_is_idempotent(pool: asyncpg.Pool) -> None:
    """Second DELETE on a soft-deleted user is still 204, no second audit row."""

    tenant_id, owner_id = await _make_user(pool, role="owner")
    _, member_id = await _make_user(pool, tenant_id=tenant_id, role="member")

    try:
        _app, client = _build_client(pool=pool, user_id=member_id)
        async with client:
            first = await client.delete("/api/v1/me/account")
            second = await client.delete("/api/v1/me/account")
        assert first.status_code == 204
        assert second.status_code == 204

        async with pool.acquire() as conn:
            audit_count = await conn.fetchval(
                "select count(*) from audit_log "
                "where user_id = $1 and action = 'user.account_deleted'",
                member_id,
            )
        assert audit_count == 1
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.users set deleted_at = null where id = $1",
                member_id,
            )
        await _cleanup(pool, tenant_id, [owner_id, member_id])


async def test_delete_rejects_sole_owner_with_409(pool: asyncpg.Pool) -> None:
    """An owner whose tenant has no other active owner cannot self-delete —
    that would orphan the tenant. Row must be untouched after the 409."""

    tenant_id, owner_id = await _make_user(pool, role="owner")
    # No second owner.

    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.delete("/api/v1/me/account")
        assert response.status_code == 409
        assert "sole owner" in response.json()["detail"]

        async with pool.acquire() as conn:
            still_active = await conn.fetchval(
                "select deleted_at is null from public.users where id = $1",
                owner_id,
            )
        assert still_active is True
    finally:
        await _cleanup(pool, tenant_id, [owner_id])


async def test_owner_with_co_owner_can_self_delete(pool: asyncpg.Pool) -> None:
    """Two owners → either one can leave, the other keeps the tenant alive."""

    tenant_id, owner_a = await _make_user(pool, role="owner")
    _, owner_b = await _make_user(pool, tenant_id=tenant_id, role="owner")

    try:
        _app, client = _build_client(pool=pool, user_id=owner_a)
        async with client:
            response = await client.delete("/api/v1/me/account")
        assert response.status_code == 204

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "select deleted_at from public.users where id = $1",
                owner_a,
            )
            other_active = await conn.fetchval(
                "select deleted_at is null from public.users where id = $1",
                owner_b,
            )
        assert row["deleted_at"] is not None
        assert other_active is True
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "update public.users set deleted_at = null where id = $1",
                owner_a,
            )
        await _cleanup(pool, tenant_id, [owner_a, owner_b])
