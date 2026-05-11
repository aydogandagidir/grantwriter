"""Tests for the proposal comments endpoints.

Covers:

- POST root + reply → list returns both, parent_id wired correctly.
- POST with parent_id pointing to a reply → 400 (depth ≤ 1).
- PATCH another author's comment → 403.
- Resolve as author → 200; as admin → 200; as another member → 403.
- DELETE as admin: parent + reply both go (cascade in app code).
- Cross-tenant: B's member touches A's comment → 404.
- include_resolved query toggle.

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
            "TEST_DATABASE_URL not set — skipping DB-bound comment tests"
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
                "Comments Test",
                f"cmt-{tenant_id}",
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
    pool: asyncpg.Pool, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    proposal_id = uuid.uuid4()
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
            json.dumps({}),
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
                "delete from proposal_comments where proposal_id = any($1::uuid[])",
                proposal_ids,
            )
            await conn.execute(
                "delete from proposals where id = any($1::uuid[])",
                proposal_ids,
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


async def test_post_root_and_reply_round_trip(pool: asyncpg.Pool) -> None:
    tenant_id, user_id = await _make_user(pool)
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=user_id
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            root = await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "first thought"},
            )
            assert root.status_code == 201, root.text
            root_id = root.json()["id"]

            reply = await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "follow-up", "parent_id": root_id},
            )
            assert reply.status_code == 201
            assert reply.json()["parent_id"] == root_id

            listing = await client.get(
                f"/api/v1/proposals/{proposal_id}/comments"
            )
            assert listing.status_code == 200
            comments = listing.json()["comments"]
            assert len(comments) == 2
            ids = [c["id"] for c in comments]
            assert root_id in ids and reply.json()["id"] in ids
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[user_id],
            proposal_ids=[proposal_id],
        )


async def test_two_level_reply_rejected(pool: asyncpg.Pool) -> None:
    tenant_id, user_id = await _make_user(pool)
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=user_id
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            root = await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "root"},
            )
            reply = await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "reply", "parent_id": root.json()["id"]},
            )
            assert reply.status_code == 201

            grandchild = await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "grandchild", "parent_id": reply.json()["id"]},
            )
            assert grandchild.status_code == 400
            assert "one level" in grandchild.json()["detail"].lower()
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[user_id],
            proposal_ids=[proposal_id],
        )


async def test_patch_other_authors_comment_403(pool: asyncpg.Pool) -> None:
    tenant_id, alice = await _make_user(pool, role="member")
    _t, bob = await _make_user(pool, tenant_id=tenant_id, role="member")
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=alice
    )
    try:
        # Alice posts.
        _app, alice_client = _build_client(pool=pool, user_id=alice)
        async with alice_client:
            posted = await alice_client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "my thought"},
            )
            comment_id = posted.json()["id"]

        # Bob tries to edit.
        _app2, bob_client = _build_client(pool=pool, user_id=bob)
        async with bob_client:
            response = await bob_client.patch(
                f"/api/v1/comments/{comment_id}",
                json={"content": "lol"},
            )
            assert response.status_code == 403
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[alice, bob],
            proposal_ids=[proposal_id],
        )


async def test_resolve_permissions(pool: asyncpg.Pool) -> None:
    tenant_id, alice = await _make_user(pool, role="member")
    _t, owner = await _make_user(pool, tenant_id=tenant_id, role="owner")
    _t2, charlie = await _make_user(pool, tenant_id=tenant_id, role="member")
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=alice
    )
    try:
        # Alice posts a comment.
        _app, alice_client = _build_client(pool=pool, user_id=alice)
        async with alice_client:
            posted = await alice_client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "x"},
            )
            cid = posted.json()["id"]

        # Charlie (other member) cannot resolve.
        _app, charlie_client = _build_client(pool=pool, user_id=charlie)
        async with charlie_client:
            r1 = await charlie_client.post(
                f"/api/v1/comments/{cid}/resolve"
            )
            assert r1.status_code == 403

        # Owner can resolve.
        _app, owner_client = _build_client(pool=pool, user_id=owner)
        async with owner_client:
            r2 = await owner_client.post(
                f"/api/v1/comments/{cid}/resolve"
            )
            assert r2.status_code == 200
            assert r2.json()["resolved"] is True

        # Resolve audit row was written.
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "select count(*) from audit_log where tenant_id = $1 and "
                "action = 'proposal.comment_resolved'",
                tenant_id,
            )
        assert int(count) == 1
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[alice, owner, charlie],
            proposal_ids=[proposal_id],
        )


async def test_delete_cascades_replies(pool: asyncpg.Pool) -> None:
    tenant_id, owner = await _make_user(pool, role="owner")
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=owner
    )
    try:
        _app, client = _build_client(pool=pool, user_id=owner)
        async with client:
            root = await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "root"},
            )
            await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "reply", "parent_id": root.json()["id"]},
            )

            response = await client.delete(
                f"/api/v1/comments/{root.json()['id']}"
            )
            assert response.status_code == 204

            listing = await client.get(
                f"/api/v1/proposals/{proposal_id}/comments"
            )
            assert listing.json()["comments"] == []
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[owner],
            proposal_ids=[proposal_id],
        )


async def test_cross_tenant_returns_404(pool: asyncpg.Pool) -> None:
    tenant_a, alice = await _make_user(pool)
    tenant_b, bob = await _make_user(pool)
    proposal_a = await _make_proposal(pool, tenant_id=tenant_a, user_id=alice)
    try:
        # Alice posts a comment in her tenant.
        _app, alice_client = _build_client(pool=pool, user_id=alice)
        async with alice_client:
            posted = await alice_client.post(
                f"/api/v1/proposals/{proposal_a}/comments",
                json={"content": "alice's"},
            )
            cid = posted.json()["id"]

        # Bob (other tenant) tries to PATCH / DELETE / list.
        _app2, bob_client = _build_client(pool=pool, user_id=bob)
        async with bob_client:
            assert (
                await bob_client.patch(
                    f"/api/v1/comments/{cid}", json={"content": "x"}
                )
            ).status_code == 404
            assert (
                await bob_client.delete(f"/api/v1/comments/{cid}")
            ).status_code == 404
            assert (
                await bob_client.get(f"/api/v1/proposals/{proposal_a}/comments")
            ).status_code == 404
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_a, tenant_b],
            user_ids=[alice, bob],
            proposal_ids=[proposal_a],
        )


async def test_include_resolved_query_toggle(pool: asyncpg.Pool) -> None:
    tenant_id, user_id = await _make_user(pool)
    proposal_id = await _make_proposal(
        pool, tenant_id=tenant_id, user_id=user_id
    )
    try:
        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            c1 = await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "active"},
            )
            c2 = await client.post(
                f"/api/v1/proposals/{proposal_id}/comments",
                json={"content": "to resolve"},
            )
            await client.post(
                f"/api/v1/comments/{c2.json()['id']}/resolve"
            )

            # Default — only unresolved.
            default_resp = await client.get(
                f"/api/v1/proposals/{proposal_id}/comments"
            )
            assert default_resp.status_code == 200
            ids = [c["id"] for c in default_resp.json()["comments"]]
            assert c1.json()["id"] in ids
            assert c2.json()["id"] not in ids

            # include_resolved=true — both.
            inc_resp = await client.get(
                f"/api/v1/proposals/{proposal_id}/comments",
                params={"include_resolved": "true"},
            )
            assert inc_resp.status_code == 200
            assert {c["id"] for c in inc_resp.json()["comments"]} == {
                c1.json()["id"],
                c2.json()["id"],
            }
    finally:
        await _cleanup(
            pool,
            tenant_ids=[tenant_id],
            user_ids=[user_id],
            proposal_ids=[proposal_id],
        )
