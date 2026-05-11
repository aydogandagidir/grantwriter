"""Tests for ``GET /api/v1/tenant/usage``.

Covers:
- 403 for non-admin members (matches the RLS policy intent).
- Empty tenant returns zeroed totals + empty series + null budget.
- A handful of seeded ``tenant_usage_log`` rows aggregate correctly into
  the current-month window AND show up in the monthly series.
- Budget status math: ``at_alert_threshold`` and ``over_budget`` flags
  flip the moment the synthetic spend crosses the configured caps.
- Budget read-side mirrors what ``PUT /api/v1/tenant/llm-config`` writes
  end-to-end.

All tests skip when ``TEST_DATABASE_URL`` is unset — the aggregation SQL
runs against real Postgres because the ``date_trunc`` and ``filter``
clauses don't have a useful in-memory equivalent.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.core.auth import get_current_user_id
from src.core.config import get_settings
from src.core.db import get_db
from src.main import create_app


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


# ── Fixtures (parallel structure to tests/security/test_byok.py) ───────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound usage tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def _master_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Settings need a master key when the BYOK flow brushes against this test."""

    key = "test-master-key-32-bytes-padding!"
    monkeypatch.setenv("LLM_MASTER_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    return key


@pytest.fixture
async def _tenant_admin(
    pool: asyncpg.Pool,
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """Owner role user. Cleans up usage_log + llm_config + audit on teardown."""

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "Usage Admin",
            f"usage-admin-{tenant_id}",
        )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"admin-{user_id}@test",
        )
        await conn.execute(
            """
            insert into public.users (id, tenant_id, role)
            values ($1, $2, 'owner')
            """,
            user_id,
            tenant_id,
        )

    try:
        yield tenant_id, user_id
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "delete from audit_log where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from tenant_usage_log where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from tenant_llm_config where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from public.users where id = $1", user_id
            )
            await conn.execute(
                "delete from auth.users where id = $1", user_id
            )
            await conn.execute("delete from tenants where id = $1", tenant_id)


@pytest.fixture
async def _tenant_member(
    pool: asyncpg.Pool,
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """Plain member — used to verify the 403 path."""

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            tenant_id,
            "Usage Member",
            f"usage-member-{tenant_id}",
        )
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"member-{user_id}@test",
        )
        await conn.execute(
            """
            insert into public.users (id, tenant_id, role)
            values ($1, $2, 'member')
            """,
            user_id,
            tenant_id,
        )

    try:
        yield tenant_id, user_id
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "delete from audit_log where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from tenant_usage_log where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from tenant_llm_config where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from public.users where id = $1", user_id
            )
            await conn.execute(
                "delete from auth.users where id = $1", user_id
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


async def _seed_usage_row(
    pool: asyncpg.Pool,
    *,
    tenant_id: uuid.UUID,
    cost_usd: Decimal,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cached_tokens: int = 0,
    used_byok: bool = False,
    created_at: datetime | None = None,
    model: str = "claude-sonnet-4-6",
) -> None:
    """Insert one synthetic ``llm_call`` row directly.

    Bypasses :mod:`src.llm.cost_tracker` so tests can backdate
    ``created_at`` for the monthly-series assertions. Always passes a
    concrete timezone-aware datetime so asyncpg's prepared-statement
    cache sees a stable parameter type across calls.
    """

    when = created_at or datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into tenant_usage_log (
              tenant_id, event_type, resource,
              input_tokens, output_tokens, cached_tokens,
              cost_usd, used_byok, metadata, created_at
            ) values (
              $1, 'llm_call', $2,
              $3, $4, $5,
              $6, $7, $8::jsonb, $9
            )
            """,
            tenant_id,
            model,
            input_tokens,
            output_tokens,
            cached_tokens,
            cost_usd,
            used_byok,
            json.dumps({"task": "excellence_writer", "provider": "claude"}),
            when,
        )


# ── Tests ──────────────────────────────────────────────────────────────


async def test_member_role_gets_403(
    pool: asyncpg.Pool,
    _tenant_member: tuple[uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, user_id = _tenant_member
    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        response = await client.get("/api/v1/tenant/usage")
    assert response.status_code == 403
    assert "owner/admin" in response.json()["detail"]


async def test_empty_tenant_returns_zeroed_totals_and_null_budget(
    pool: asyncpg.Pool,
    _tenant_admin: tuple[uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, user_id = _tenant_admin
    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        response = await client.get("/api/v1/tenant/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["current_month"] == {
        "llm_call_count": 0,
        "byok_call_count": 0,
        "managed_call_count": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cached_tokens": 0,
        "total_cost_usd": "0.000000",
    }
    assert body["monthly_series"] == []
    assert body["budget"] == {
        "monthly_budget_usd": None,
        "alert_threshold_usd": None,
        "current_month_usd": "0.000000",
        "at_alert_threshold": False,
        "over_budget": False,
        "headroom_usd": None,
    }
    # Fresh starter tenant: 3 / month, none used yet.
    assert body["plan_quota"]["plan"] == "starter"
    assert body["plan_quota"]["monthly_limit"] == 3
    assert body["plan_quota"]["used_this_month"] == 0
    assert body["plan_quota"]["remaining"] == 3


async def test_aggregates_current_month_and_byok_split(
    pool: asyncpg.Pool,
    _tenant_admin: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, user_id = _tenant_admin
    await _seed_usage_row(
        pool, tenant_id=tenant_id, cost_usd=Decimal("0.50"), used_byok=False
    )
    await _seed_usage_row(
        pool, tenant_id=tenant_id, cost_usd=Decimal("0.25"), used_byok=True
    )
    await _seed_usage_row(
        pool,
        tenant_id=tenant_id,
        cost_usd=Decimal("0.10"),
        input_tokens=200,
        output_tokens=100,
        used_byok=True,
    )

    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        response = await client.get("/api/v1/tenant/usage")

    body = response.json()
    cm = body["current_month"]
    assert cm["llm_call_count"] == 3
    assert cm["byok_call_count"] == 2
    assert cm["managed_call_count"] == 1
    assert Decimal(cm["total_cost_usd"]) == Decimal("0.85")
    assert cm["total_input_tokens"] == 100 + 100 + 200
    assert cm["total_output_tokens"] == 50 + 50 + 100


async def test_monthly_series_includes_only_active_months(
    pool: asyncpg.Pool,
    _tenant_admin: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """Backdate one row to two months ago — series should report two buckets,
    one for that month and one for the current month."""

    tenant_id, user_id = _tenant_admin
    now = datetime.now(UTC)
    two_months_ago = (now.replace(day=15) - timedelta(days=60)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )

    await _seed_usage_row(
        pool,
        tenant_id=tenant_id,
        cost_usd=Decimal("1.00"),
        created_at=two_months_ago,
    )
    await _seed_usage_row(
        pool, tenant_id=tenant_id, cost_usd=Decimal("0.30"), used_byok=True
    )

    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        response = await client.get("/api/v1/tenant/usage")

    series = response.json()["monthly_series"]
    assert len(series) == 2
    months = [item["month"] for item in series]
    assert months == sorted(months)  # oldest first
    assert series[-1]["byok_calls"] == 1
    assert series[0]["call_count"] == 1


async def test_budget_status_flags_flip_at_thresholds(
    pool: asyncpg.Pool,
    _tenant_admin: tuple[uuid.UUID, uuid.UUID],
    _master_env: str,
) -> None:
    """End-to-end: PUT sets the budget, GET reads it, flags reflect spend."""

    tenant_id, user_id = _tenant_admin
    _app, client = _build_client(pool=pool, user_id=user_id)
    async with client:
        # Configure budget = 1.00 / alert at 0.50 via the BYOK PUT route.
        put_response = await client.put(
            "/api/v1/tenant/llm-config",
            json={
                "monthly_budget_usd": "1.00",
                "alert_threshold_usd": "0.50",
            },
        )
        assert put_response.status_code == 200

        # Spend = 0.40 → both flags False.
        await _seed_usage_row(
            pool, tenant_id=tenant_id, cost_usd=Decimal("0.40")
        )
        body = (await client.get("/api/v1/tenant/usage")).json()
        assert body["budget"]["at_alert_threshold"] is False
        assert body["budget"]["over_budget"] is False
        assert Decimal(body["budget"]["headroom_usd"]) == Decimal("0.60")

        # Add 0.20 → spend = 0.60 ≥ 0.50 alert threshold.
        await _seed_usage_row(
            pool, tenant_id=tenant_id, cost_usd=Decimal("0.20")
        )
        body = (await client.get("/api/v1/tenant/usage")).json()
        assert body["budget"]["at_alert_threshold"] is True
        assert body["budget"]["over_budget"] is False

        # Add 0.50 → spend = 1.10 > 1.00 budget.
        await _seed_usage_row(
            pool, tenant_id=tenant_id, cost_usd=Decimal("0.50")
        )
        body = (await client.get("/api/v1/tenant/usage")).json()
        assert body["budget"]["over_budget"] is True
        assert Decimal(body["budget"]["headroom_usd"]) == Decimal("-0.10")


async def test_other_tenant_usage_is_invisible(
    pool: asyncpg.Pool,
    _tenant_admin: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """Seed a row under a different tenant; the report must not include it."""

    tenant_id, user_id = _tenant_admin
    foreign_tenant = uuid.uuid4()

    async with pool.acquire() as conn:
        await conn.execute(
            "insert into tenants (id, name, slug) values ($1, $2, $3)",
            foreign_tenant,
            "Foreign",
            f"foreign-{foreign_tenant}",
        )
    try:
        await _seed_usage_row(
            pool, tenant_id=foreign_tenant, cost_usd=Decimal("99.99")
        )
        await _seed_usage_row(
            pool, tenant_id=tenant_id, cost_usd=Decimal("0.10")
        )

        _app, client = _build_client(pool=pool, user_id=user_id)
        async with client:
            body = (await client.get("/api/v1/tenant/usage")).json()
        assert Decimal(body["current_month"]["total_cost_usd"]) == Decimal("0.10")
        assert body["current_month"]["llm_call_count"] == 1
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "delete from tenant_usage_log where tenant_id = $1", foreign_tenant
            )
            await conn.execute(
                "delete from tenants where id = $1", foreign_tenant
            )


async def test_rejects_unauthenticated_users_via_404(
    pool: asyncpg.Pool,
) -> None:
    """A user that doesn't exist in ``public.users`` (or is soft-deleted)
    should 404, not 500. Mirrors the auth.tenant_id() helper behaviour."""

    ghost_user = uuid.uuid4()
    _app, client = _build_client(pool=pool, user_id=ghost_user)
    async with client:
        response = await client.get("/api/v1/tenant/usage")
    assert response.status_code == 404
    assert "no active tenant" in response.json()["detail"]


async def test_index_idx_usage_tenant_time_exists(
    pool: asyncpg.Pool,
) -> None:
    """Schema sanity: the index that backs the aggregation is present.

    We don't assert the planner's choice — on a near-empty test table
    Postgres correctly picks Seq Scan, which would false-fire a planner
    assertion. The schema check is the durable guarantee.
    """

    async with pool.acquire() as conn:
        index_rows = await conn.fetch(
            """
            select indexname from pg_indexes
             where schemaname = 'public'
               and tablename = 'tenant_usage_log'
            """
        )
    index_names = {row["indexname"] for row in index_rows}
    assert "idx_usage_tenant_time" in index_names, index_names
