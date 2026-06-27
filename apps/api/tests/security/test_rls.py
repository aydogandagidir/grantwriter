"""Edge-case RLS / auth tests beyond the SQL test suite.

Cross-tenant isolation, INSERT denial, and helper return values are covered
by ``infra/supabase/tests/rls_test.sql`` (which CI runs before this file).
This file owns the four scenarios called out in S1.D2.T2 / docs/09 §6:

  1. service_role bypass (Celery workers)
  2. role escalation (member → admin) takes effect immediately
  3. soft-deleted users cannot access data
  4. JWT signature mismatch returns 401
"""

from __future__ import annotations

import time
import uuid

import asyncpg
import jwt
import pytest
from src.core.auth import JWTValidationError, verify_supabase_jwt

from tests.security.conftest import as_role, authenticated_as

_JWT_SECRET = "test-secret-do-not-use-in-prod-32bytes!"
_OTHER_SECRET = "evil-secret-also-padded-to-32-bytes!!"


# ── 1. service_role bypass ────────────────────────────────────────────────


async def test_service_role_sees_all_tenants(
    pool: asyncpg.Pool, fixture_data: dict[str, uuid.UUID]
) -> None:
    """Celery workers connect as service_role and must see every tenant.

    service_role is created with ``BYPASSRLS`` in auth_stub.sql and on
    Supabase Cloud, so RLS policies are not evaluated for it.
    """

    async with as_role(pool, "service_role") as conn:
        rows = await conn.fetch(
            "select tenant_id from proposals where id = any($1::uuid[])",
            [fixture_data["proposal_a"], fixture_data["proposal_b"]],
        )
    seen_tenants = {row["tenant_id"] for row in rows}
    assert fixture_data["tenant_a"] in seen_tenants
    assert fixture_data["tenant_b"] in seen_tenants


async def test_authenticated_user_sees_only_own_tenant(
    pool: asyncpg.Pool, fixture_data: dict[str, uuid.UUID]
) -> None:
    """Counterpart sanity: same query as authenticated user A returns only A's row."""

    async with authenticated_as(pool, fixture_data["user_a"]) as conn:
        rows = await conn.fetch(
            "select tenant_id from proposals where id = any($1::uuid[])",
            [fixture_data["proposal_a"], fixture_data["proposal_b"]],
        )
    assert {row["tenant_id"] for row in rows} == {fixture_data["tenant_a"]}


# ── 2. Role escalation takes effect immediately ───────────────────────────


async def test_role_escalation_takes_effect_immediately(
    pool: asyncpg.Pool,
    fixture_data: dict[str, uuid.UUID],
    _auth_helpers_present: bool,
) -> None:
    """User A starts as 'member' (public.is_tenant_admin = false). Update to
    'admin' through a service-role connection and confirm the very next
    authenticated query reflects the new role — no caching, no stale view.
    """

    if not _auth_helpers_present:
        pytest.skip(
            "auth.is_tenant_admin() missing — local Supabase locks "
            "auth schema for postgres; CI's bare pgvector container runs it"
        )

    user_a = fixture_data["user_a"]

    async with authenticated_as(pool, user_a) as conn:
        is_admin = await conn.fetchval("select public.is_tenant_admin()")
    assert is_admin is False, "user A should start as plain member"

    async with as_role(pool, "service_role") as conn:
        await conn.execute("update public.users set role = 'admin' where id = $1", user_a)

    async with authenticated_as(pool, user_a) as conn:
        is_admin_now = await conn.fetchval("select public.is_tenant_admin()")
    assert is_admin_now is True, "role bump must be visible on the next call"


# ── 3. Soft-deleted users cannot access data ──────────────────────────────


async def test_soft_deleted_user_loses_all_visibility(
    pool: asyncpg.Pool,
    fixture_data: dict[str, uuid.UUID],
    _auth_helpers_present: bool,
) -> None:
    """Setting ``public.users.deleted_at`` should make public.tenant_id() return
    NULL for that user, and every tenant-scoped policy compares against
    that NULL — so the user sees zero rows everywhere.
    """

    if not _auth_helpers_present:
        pytest.skip(
            "auth.tenant_id() missing — local Supabase locks "
            "auth schema for postgres; CI's bare pgvector container runs it"
        )

    user_a = fixture_data["user_a"]

    async with as_role(pool, "service_role") as conn:
        await conn.execute("update public.users set deleted_at = now() where id = $1", user_a)

    try:
        async with authenticated_as(pool, user_a) as conn:
            tenant_id = await conn.fetchval("select public.tenant_id()")
            visible_proposals = await conn.fetchval("select count(*) from proposals")
            visible_tenants = await conn.fetchval("select count(*) from tenants")
        assert tenant_id is None, "deleted user must resolve to NULL tenant"
        assert visible_proposals == 0
        assert visible_tenants == 0
    finally:
        async with as_role(pool, "service_role") as conn:
            await conn.execute("update public.users set deleted_at = null where id = $1", user_a)


# ── 4. JWT signature mismatch returns 401 ─────────────────────────────────


def _make_jwt(secret: str, *, sub: str = "user-1") -> str:
    return jwt.encode(
        {
            "sub": sub,
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        secret,
        algorithm="HS256",
    )


def test_valid_token_decodes() -> None:
    """Sanity: a token signed with the correct secret decodes."""

    token = _make_jwt(_JWT_SECRET)
    claims = verify_supabase_jwt(token, _JWT_SECRET)
    assert claims["sub"] == "user-1"
    assert claims["aud"] == "authenticated"


def test_jwt_signature_mismatch_raises_401() -> None:
    token = _make_jwt(_OTHER_SECRET)
    with pytest.raises(JWTValidationError) as exc_info:
        verify_supabase_jwt(token, _JWT_SECRET)
    assert exc_info.value.status_code == 401
    assert "signature" in exc_info.value.detail.lower()


def test_jwt_expired_raises_401() -> None:
    token = jwt.encode(
        {
            "sub": "user-1",
            "aud": "authenticated",
            "exp": int(time.time()) - 60,  # expired one minute ago
        },
        _JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(JWTValidationError) as exc_info:
        verify_supabase_jwt(token, _JWT_SECRET)
    assert exc_info.value.status_code == 401


def test_jwt_wrong_audience_raises_401() -> None:
    token = jwt.encode(
        {
            "sub": "user-1",
            "aud": "service_role",
            "exp": int(time.time()) + 3600,
        },
        _JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(JWTValidationError) as exc_info:
        verify_supabase_jwt(token, _JWT_SECRET)
    assert exc_info.value.status_code == 401


def test_jwt_malformed_raises_401() -> None:
    with pytest.raises(JWTValidationError) as exc_info:
        verify_supabase_jwt("not.a.jwt", _JWT_SECRET)
    assert exc_info.value.status_code == 401
