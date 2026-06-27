"""Fixtures for the RLS / security test suite.

The DB-touching tests skip when ``TEST_DATABASE_URL`` is not set, so the
fast unit-test lane (no Postgres) still passes. CI exposes a pgvector
container with the migrations + auth_stub applied and points the env at it.

Pool sizing is intentional: ``min_size=1, max_size=1`` gives each test a
dedicated single-connection pool with no concurrency, which sidesteps a
class of asyncpg-on-Windows interleaving bugs.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound RLS tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    """Function-scoped, single-connection asyncpg pool.

    Connects as superuser ``postgres`` so individual tests can ``SET LOCAL
    ROLE`` to whichever role they want to exercise.
    """

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def _auth_helpers_present(pool: asyncpg.Pool) -> bool:
    """True iff ``auth.tenant_id()`` + ``auth.is_tenant_admin()`` exist.

    The local Supabase stack restricts ``postgres`` from creating
    objects in the ``auth`` schema (only ``supabase_admin`` can), so
    migration 009's helpers never land there. CI's bare pgvector
    container has no such restriction and runs the helpers normally,
    where these tests behave as designed.
    """

    async with pool.acquire() as conn:
        present = await conn.fetchval(
            """
            select exists (
              select 1 from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
               where n.nspname = 'auth'
                 and p.proname in ('tenant_id', 'is_tenant_admin')
              having count(*) = 2
            )
            """
        )
    return bool(present)


@pytest.fixture
async def fixture_data(pool: asyncpg.Pool) -> AsyncIterator[dict[str, uuid.UUID]]:
    """Insert two tenants + two users + two proposals; tear down after the test."""

    ids: dict[str, uuid.UUID] = {
        "tenant_a": uuid.uuid4(),
        "tenant_b": uuid.uuid4(),
        "user_a": uuid.uuid4(),
        "user_b": uuid.uuid4(),
        "proposal_a": uuid.uuid4(),
        "proposal_b": uuid.uuid4(),
    }

    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            f"a-{ids['tenant_a']}",
            ids["tenant_b"],
            "Tenant B",
            f"b-{ids['tenant_b']}",
        )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2), ($3, $4)",
            ids["user_a"],
            f"a-{ids['user_a']}@test",
            ids["user_b"],
            f"b-{ids['user_b']}@test",
        )
        await conn.execute(
            """
            insert into public.users (id, tenant_id, role) values
              ($1, $2, 'member'),
              ($3, $4, 'owner')
            """,
            ids["user_a"],
            ids["tenant_a"],
            ids["user_b"],
            ids["tenant_b"],
        )
        await conn.execute(
            """
            insert into proposals (id, tenant_id, created_by, programme_id, language)
            values ($1, $2, $3, 'tubitak_1501', 'tr'),
                   ($4, $5, $6, 'horizon_eu_ria', 'en')
            """,
            ids["proposal_a"],
            ids["tenant_a"],
            ids["user_a"],
            ids["proposal_b"],
            ids["tenant_b"],
            ids["user_b"],
        )

    try:
        yield ids
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "delete from proposals where id = any($1::uuid[])",
                [ids["proposal_a"], ids["proposal_b"]],
            )
            await conn.execute(
                "delete from public.users where id = any($1::uuid[])",
                [ids["user_a"], ids["user_b"]],
            )
            await conn.execute(
                "delete from auth.users where id = any($1::uuid[])",
                [ids["user_a"], ids["user_b"]],
            )
            await conn.execute(
                "delete from tenants where id = any($1::uuid[])",
                [ids["tenant_a"], ids["tenant_b"]],
            )


@asynccontextmanager
async def authenticated_as(
    pool: asyncpg.Pool, user_id: uuid.UUID
) -> AsyncIterator[asyncpg.Connection]:
    """Yield a connection acting as the ``authenticated`` role with a JWT claim.

    The role + claim are set transaction-locally (SET LOCAL / set_config
    with ``is_local=true``) and the transaction is committed on success
    so writes done inside the block persist. The connection is left in
    its default (postgres) role for the next acquirer.
    """

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            claims = json.dumps({"sub": str(user_id), "role": "authenticated"})
            await conn.execute("set local role authenticated")
            # set_config returns the new value, so use fetchval to drain
            # the resulting row — execute() leaves the protocol in a half-
            # consumed state and breaks the next pool.release().
            await conn.fetchval("select set_config('request.jwt.claims', $1, true)", claims)
            yield conn
        except BaseException:
            await tx.rollback()
            raise
        else:
            await tx.commit()


@asynccontextmanager
async def as_role(pool: asyncpg.Pool, role: str) -> AsyncIterator[asyncpg.Connection]:
    """Yield a connection with the given role and no JWT claims."""

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(f"set local role {role}")
            await conn.fetchval("select set_config('request.jwt.claims', '', true)")
            yield conn
        except BaseException:
            await tx.rollback()
            raise
        else:
            await tx.commit()
