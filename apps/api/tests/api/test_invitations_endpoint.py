"""Tests for the tenant invitation flow.

Covers:

- POST issues a token (returned ONCE), audit row written, list/revoke
  surfaces hide the token.
- POST 403 for non-admin members; 409 for an existing user email or a
  duplicate pending invite.
- DELETE is idempotent; missing id is 204 (no audit row created).
- GET /{token} preview is public — no JWT required, returns tenant
  context, 404 for invalid token, 410 for expired/already-accepted.
- POST /accept links the JWT caller to the tenant when emails match,
  rejects mismatch (403), already-in-tenant (409), expired (410).

Skips when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from uuid import UUID

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
            "TEST_DATABASE_URL not set — skipping DB-bound invitation tests"
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
    """Create (tenant + user) or (user under given tenant). Returns ids + email."""

    user_id = uuid.uuid4()
    user_email = email or f"u-{user_id}@example.com"
    async with pool.acquire() as conn:
        if tenant_id is None:
            tenant_id = uuid.uuid4()
            await conn.execute(
                "insert into tenants (id, name, slug) values ($1, $2, $3)",
                tenant_id,
                "Invite Test",
                f"inv-{tenant_id}",
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


async def _make_auth_only_user(
    pool: asyncpg.Pool, *, email: str | None = None
) -> tuple[uuid.UUID, str]:
    """Create an auth.users row only — invitee who hasn't joined a tenant yet."""

    user_id = uuid.uuid4()
    user_email = email or f"u-{user_id}@example.com"
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            user_email,
        )
    return user_id, user_email


async def _cleanup(
    pool: asyncpg.Pool,
    tenant_ids: list[uuid.UUID],
    user_ids: list[uuid.UUID],
) -> None:
    async with pool.acquire() as conn:
        if tenant_ids:
            await conn.execute(
                "delete from tenant_invitations where tenant_id = any($1::uuid[])",
                tenant_ids,
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
    *, pool: asyncpg.Pool, user_id: uuid.UUID | None = None
) -> tuple[FastAPI, AsyncClient]:
    """Build app with either a JWT override (auth) or no override (public route)."""

    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    if user_id is not None:
        app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


# ── POST /tenant/invitations ───────────────────────────────────────────


async def test_admin_creates_invitation_and_token_appears_once(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.post(
                "/api/v1/tenant/invitations",
                json={"email": "alice@example.com", "role": "member"},
            )
            assert response.status_code == 201
            body = response.json()
            assert body["email"] == "alice@example.com"
            assert body["role"] == "member"
            assert len(body["token"]) >= 32
            assert body["accept_url_path"].startswith("/invitations/")

            list_response = await client.get("/api/v1/tenant/invitations")
            assert list_response.status_code == 200
            items = list_response.json()["invitations"]
            assert len(items) == 1
            assert "token" not in items[0]

        # Audit row written.
        async with pool.acquire() as conn:
            audits = await conn.fetch(
                "select action, diff::text as d from audit_log "
                "where tenant_id = $1 and action = 'tenant.invitation_sent'",
                tenant_id,
            )
        assert len(audits) == 1
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_member_cannot_create_invitation(pool: asyncpg.Pool) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    _, member_id, _ = await _make_user(pool, tenant_id=tenant_id, role="member")
    try:
        _app, client = _build_client(pool=pool, user_id=member_id)
        async with client:
            response = await client.post(
                "/api/v1/tenant/invitations",
                json={"email": "x@example.com", "role": "member"},
            )
        assert response.status_code == 403
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, member_id])


async def test_invite_email_already_in_a_tenant_returns_409(
    pool: asyncpg.Pool,
) -> None:
    """An email that already belongs to a tenant member can't be re-invited
    — accept would fail at the public.users PK anyway."""

    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    other_tenant_id, other_owner_id, other_email = await _make_user(
        pool, role="owner"
    )
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.post(
                "/api/v1/tenant/invitations",
                json={"email": other_email, "role": "member"},
            )
        assert response.status_code == 409
        assert "active tenant member" in response.json()["detail"]
    finally:
        await _cleanup(
            pool, [tenant_id, other_tenant_id], [owner_id, other_owner_id]
        )


async def test_invite_email_with_supabase_account_but_no_tenant_succeeds(
    pool: asyncpg.Pool,
) -> None:
    """A Supabase account without a ``public.users`` row is the typical
    invite case — sign up first, then accept. Must NOT 409."""

    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    invitee_id, invitee_email = await _make_auth_only_user(pool)
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            response = await client.post(
                "/api/v1/tenant/invitations",
                json={"email": invitee_email, "role": "member"},
            )
        assert response.status_code == 201
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, invitee_id])


async def test_duplicate_pending_invitation_returns_409(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            first = await client.post(
                "/api/v1/tenant/invitations",
                json={"email": "dup@example.com"},
            )
            second = await client.post(
                "/api/v1/tenant/invitations",
                json={"email": "dup@example.com"},
            )
        assert first.status_code == 201
        assert second.status_code == 409
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_revoke_invitation_returns_204_and_audits(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    try:
        _app, client = _build_client(pool=pool, user_id=owner_id)
        async with client:
            created = await client.post(
                "/api/v1/tenant/invitations",
                json={"email": "rev@example.com"},
            )
            invitation_id = created.json()["id"]
            response = await client.delete(
                f"/api/v1/tenant/invitations/{invitation_id}"
            )
            second = await client.delete(
                f"/api/v1/tenant/invitations/{invitation_id}"
            )
        assert response.status_code == 204
        assert second.status_code == 204  # idempotent

        async with pool.acquire() as conn:
            revoked_audits = await conn.fetch(
                "select action from audit_log "
                "where tenant_id = $1 and action = 'tenant.invitation_revoked'",
                tenant_id,
            )
        # Only the first DELETE wrote an audit; the second was a no-op.
        assert len(revoked_audits) == 1
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


# ── Public preview ─────────────────────────────────────────────────────


async def test_public_preview_returns_tenant_context(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    try:
        _app, admin_client = _build_client(pool=pool, user_id=owner_id)
        async with admin_client:
            created = await admin_client.post(
                "/api/v1/tenant/invitations",
                json={"email": "prev@example.com", "role": "admin"},
            )
            token = created.json()["token"]

        # Public client — no JWT override.
        _app2, public = _build_client(pool=pool, user_id=None)
        async with public:
            response = await public.get(f"/api/v1/invitations/{token}")
        assert response.status_code == 200
        body = response.json()
        assert body["invited_email"] == "prev@example.com"
        assert body["role"] == "admin"
        assert body["tenant_name"] == "Invite Test"
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_public_preview_404_on_unknown_token(pool: asyncpg.Pool) -> None:
    _app, public = _build_client(pool=pool, user_id=None)
    async with public:
        response = await public.get("/api/v1/invitations/not-a-real-token")
    assert response.status_code == 404


async def test_public_preview_410_on_expired_invitation(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    try:
        _app, admin = _build_client(pool=pool, user_id=owner_id)
        async with admin:
            created = await admin.post(
                "/api/v1/tenant/invitations",
                json={"email": "expired@example.com"},
            )
            token = created.json()["token"]

        # Backdate the expiry.
        async with pool.acquire() as conn:
            await conn.execute(
                "update tenant_invitations set expires_at = now() - interval '1 hour' "
                "where token = $1",
                token,
            )

        _app2, public = _build_client(pool=pool, user_id=None)
        async with public:
            response = await public.get(f"/api/v1/invitations/{token}")
        assert response.status_code == 410
        assert "expired" in response.json()["detail"]
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


# ── POST /accept ───────────────────────────────────────────────────────


async def test_accept_links_invitee_to_tenant_and_marks_used(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    invitee_id, invitee_email = await _make_auth_only_user(
        pool, email="join@example.com"
    )
    try:
        _app, admin = _build_client(pool=pool, user_id=owner_id)
        async with admin:
            created = await admin.post(
                "/api/v1/tenant/invitations",
                json={"email": invitee_email, "role": "member"},
            )
            token = created.json()["token"]

        _app2, invitee = _build_client(pool=pool, user_id=invitee_id)
        async with invitee:
            response = await invitee.post(
                "/api/v1/invitations/accept", json={"token": token}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["tenant_id"] == str(tenant_id)
        assert body["role"] == "member"

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "select tenant_id, role from public.users where id = $1",
                invitee_id,
            )
            invitation_row = await conn.fetchrow(
                "select accepted_at from tenant_invitations where token = $1",
                token,
            )
        assert row is not None
        assert UUID(str(row["tenant_id"])) == tenant_id
        assert row["role"] == "member"
        assert invitation_row["accepted_at"] is not None
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, invitee_id])


async def test_accept_rejects_email_mismatch_with_403(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    other_user_id, _ = await _make_auth_only_user(
        pool, email="someoneelse@example.com"
    )
    try:
        _app, admin = _build_client(pool=pool, user_id=owner_id)
        async with admin:
            created = await admin.post(
                "/api/v1/tenant/invitations",
                json={"email": "intended@example.com"},
            )
            token = created.json()["token"]

        _app2, wrong_user = _build_client(pool=pool, user_id=other_user_id)
        async with wrong_user:
            response = await wrong_user.post(
                "/api/v1/invitations/accept", json={"token": token}
            )
        assert response.status_code == 403
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, other_user_id])


async def test_accept_rejects_user_already_in_a_tenant_with_409(
    pool: asyncpg.Pool,
) -> None:
    """Defense-in-depth: even if a stale invitation row sneaks past the
    POST-time guard (race: invitee joined another tenant between POST and
    accept), accept() must still 409 because public.users PK is per-user.

    We bypass the POST validation by inserting the invitation row
    directly — that way we exercise the accept-side guard in isolation.
    """

    import secrets

    tenant_a, owner_a, _ = await _make_user(pool, role="owner")
    tenant_b, owner_b, _ = await _make_user(pool, role="owner")
    # Member of tenant A — already has a public.users row.
    _, member_id, member_email = await _make_user(
        pool, tenant_id=tenant_a, role="member", email="x@example.com"
    )

    token = secrets.token_urlsafe(32)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into tenant_invitations (
                  tenant_id, email, role, invited_by, token
                ) values ($1, $2, 'member', $3, $4)
                """,
                tenant_b,
                member_email,
                owner_b,
                token,
            )

        _app, member_client = _build_client(pool=pool, user_id=member_id)
        async with member_client:
            response = await member_client.post(
                "/api/v1/invitations/accept", json={"token": token}
            )
        assert response.status_code == 409
        assert "already belongs" in response.json()["detail"]
    finally:
        await _cleanup(
            pool, [tenant_a, tenant_b], [owner_a, owner_b, member_id]
        )


async def test_accept_410_on_expired_invitation(pool: asyncpg.Pool) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    invitee_id, invitee_email = await _make_auth_only_user(
        pool, email="late@example.com"
    )
    try:
        _app, admin = _build_client(pool=pool, user_id=owner_id)
        async with admin:
            created = await admin.post(
                "/api/v1/tenant/invitations", json={"email": invitee_email}
            )
            token = created.json()["token"]

        async with pool.acquire() as conn:
            await conn.execute(
                "update tenant_invitations set expires_at = now() - interval '1 hour' "
                "where token = $1",
                token,
            )

        _app2, invitee = _build_client(pool=pool, user_id=invitee_id)
        async with invitee:
            response = await invitee.post(
                "/api/v1/invitations/accept", json={"token": token}
            )
        assert response.status_code == 410
    finally:
        await _cleanup(pool, [tenant_id], [owner_id, invitee_id])
