"""Tests for :mod:`src.billing.quota` against a real Postgres.

Covers:
- A fresh tenant (no ``billing_period_start``) gets the first call
  through and lands on the current month after a single consume.
- Hitting the limit returns ``allowed=False`` and a snapshot the FE
  can render (used == limit).
- Stale ``billing_period_start`` (last calendar month) rolls over the
  counter on the next consume — the user starts the new month at 1.
- The atomic SQL closes the read-then-write race: a burst of N+1
  parallel consume calls allows exactly N and rejects the rest.
- :func:`peek_quota` reports zero across a period rollover even when
  the underlying row hasn't been touched yet.

Skips when ``TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from src.billing.quota import (
    QuotaSnapshot,
    TenantNotFoundError,
    consume_quota,
    peek_quota,
)


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound quota tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=4)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def tenant(pool: asyncpg.Pool) -> AsyncIterator[uuid.UUID]:
    """Fresh tenant on the default ``starter`` plan (3 / month)."""

    tenant_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "Quota Test",
            f"quota-{tenant_id}",
        )
    try:
        yield tenant_id
    finally:
        async with pool.acquire() as conn:
            await conn.execute("delete from tenants where id = $1", tenant_id)


# ── Happy path ─────────────────────────────────────────────────────────


async def test_fresh_tenant_first_consume_is_allowed_and_lands_in_current_month(
    pool: asyncpg.Pool, tenant: uuid.UUID
) -> None:
    async with pool.acquire() as conn:
        result = await consume_quota(conn, tenant_id=tenant)

    assert result.allowed is True
    assert result.snapshot.plan == "starter"
    assert result.snapshot.monthly_limit == 3
    assert result.snapshot.used_this_month == 1
    # billing_period_start was NULL — must now be the first of the month.
    assert result.snapshot.period_start.day == 1
    assert result.snapshot.remaining == 2


async def test_consume_to_the_limit_then_one_more_is_denied(
    pool: asyncpg.Pool, tenant: uuid.UUID
) -> None:
    async with pool.acquire() as conn:
        for expected_used in (1, 2, 3):
            r = await consume_quota(conn, tenant_id=tenant)
            assert r.allowed is True, f"call #{expected_used} should be allowed"
            assert r.snapshot.used_this_month == expected_used

        denied = await consume_quota(conn, tenant_id=tenant)

    assert denied.allowed is False
    assert denied.snapshot.used_this_month == 3
    assert denied.snapshot.remaining == 0
    assert denied.snapshot.is_exceeded is True


async def test_period_rollover_resets_counter(
    pool: asyncpg.Pool, tenant: uuid.UUID
) -> None:
    """Backdate ``billing_period_start`` to last month. The next
    consume() should reset the counter to 1 and roll the period
    forward — even though the previous month was at the limit."""

    async with pool.acquire() as conn:
        await conn.execute(
            """
            update tenants
               set monthly_proposals_used = 3,
                   billing_period_start = (date_trunc('month', now() - interval '1 month'))::date
             where id = $1
            """,
            tenant,
        )
        rolled = await consume_quota(conn, tenant_id=tenant)

    assert rolled.allowed is True
    assert rolled.snapshot.used_this_month == 1
    assert rolled.snapshot.remaining == 2


async def test_peek_reports_zero_after_rollover_without_touching_db(
    pool: asyncpg.Pool, tenant: uuid.UUID
) -> None:
    """The DB row still says 'used = 3 last month' but peek normalises to
    the FE-facing view: in the new month, used = 0 until the next consume."""

    async with pool.acquire() as conn:
        await conn.execute(
            """
            update tenants
               set monthly_proposals_used = 3,
                   billing_period_start = (date_trunc('month', now() - interval '1 month'))::date
             where id = $1
            """,
            tenant,
        )
        snapshot = await peek_quota(conn, tenant_id=tenant)

    assert snapshot.used_this_month == 0
    assert snapshot.remaining == snapshot.monthly_limit


async def test_peek_does_not_mutate(
    pool: asyncpg.Pool, tenant: uuid.UUID
) -> None:
    """Two consecutive peeks see the same row — no implicit consume."""

    async with pool.acquire() as conn:
        first = await peek_quota(conn, tenant_id=tenant)
        second = await peek_quota(conn, tenant_id=tenant)

    assert first == second
    assert first.used_this_month == 0


async def test_peek_raises_for_unknown_tenant(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        with pytest.raises(TenantNotFoundError):
            await peek_quota(conn, tenant_id=uuid.uuid4())


# ── Custom plan limits ─────────────────────────────────────────────────


async def test_pro_plan_limit_is_honoured(pool: asyncpg.Pool) -> None:
    """A tenant on the Pro plan with limit=15 should refuse only at 16."""

    tenant_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into tenants (id, name, slug, plan, monthly_proposal_limit)
            values ($1, $2, $3, 'pro', 15)
            """,
            tenant_id,
            "Pro Tenant",
            f"pro-{tenant_id}",
        )
    try:
        async with pool.acquire() as conn:
            for _ in range(15):
                r = await consume_quota(conn, tenant_id=tenant_id)
                assert r.allowed is True
            denied = await consume_quota(conn, tenant_id=tenant_id)
        assert denied.allowed is False
        assert denied.snapshot.plan == "pro"
        assert denied.snapshot.monthly_limit == 15
    finally:
        async with pool.acquire() as conn:
            await conn.execute("delete from tenants where id = $1", tenant_id)


# ── Atomicity ──────────────────────────────────────────────────────────


async def test_concurrent_consumes_respect_the_atomic_cap(
    pool: asyncpg.Pool, tenant: uuid.UUID
) -> None:
    """Burst N+1 consume() calls in parallel; exactly N are allowed.

    This is the regression guard for the read-then-write race. The
    sliding-window log on Redis is independent — this test only proves
    that ``consume_quota``'s single ``UPDATE … RETURNING`` is atomic
    under Postgres' MVCC.
    """

    async def _one_call() -> bool:
        async with pool.acquire() as conn:
            r = await consume_quota(conn, tenant_id=tenant)
            return r.allowed

    # 6 parallel calls against the Starter limit of 3.
    results = await asyncio.gather(*(_one_call() for _ in range(6)))
    allowed_count = sum(1 for ok in results if ok)
    assert allowed_count == 3, results

    # Final state: counter at the limit, next call refused.
    async with pool.acquire() as conn:
        snapshot = await peek_quota(conn, tenant_id=tenant)
    assert snapshot.used_this_month == 3
    assert snapshot.remaining == 0


# ── Snapshot helpers ───────────────────────────────────────────────────


def test_snapshot_remaining_clamps_at_zero() -> None:
    """``QuotaSnapshot`` is a value type — sanity test of the helpers
    so a future regression in the math is caught without standing up DB."""

    from datetime import date as date_cls

    s = QuotaSnapshot(
        plan="starter",
        monthly_limit=3,
        used_this_month=5,
        period_start=date_cls(2026, 5, 1),
    )
    assert s.remaining == 0
    assert s.is_exceeded is True
