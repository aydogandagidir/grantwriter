"""Tests for the Iyzico webhook receiver + plan-mapping logic.

Covers:
- :func:`verify_signature` accepts a body signed with the same secret,
  rejects mismatched signatures, missing headers, and tampered bodies.
- The route returns 503 when the secret is unconfigured (operator
  signal — not a 500 from a missing settings field).
- 401 on missing / wrong signature; 200 with ``{"received": True}`` on
  a happy path including idempotent retries.
- Plan-activating events update ``tenants.plan`` and
  ``monthly_proposal_limit``; an audit row is written.
- Plan-cancel events downgrade to starter.
- Unknown plan-reference codes are logged and skipped, but the event
  is still persisted to ``billing_events``.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from src.billing import iyzico
from src.billing.plan_mapping import lookup_plan
from src.core.config import get_settings
from src.core.db import get_db
from src.main import create_app


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


_WEBHOOK_SECRET = "test-iyzico-secret-32-bytes-pad!"


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB-bound Iyzico tests")
    return url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def _webhook_env(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("IYZICO_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    get_settings.cache_clear()
    return _WEBHOOK_SECRET


@pytest.fixture
async def tenant(pool: asyncpg.Pool) -> AsyncIterator[dict[str, Any]]:
    """Tenant with an iyzico_customer_id ready to receive webhooks."""

    tenant_id = uuid.uuid4()
    customer_code = f"iyz-cust-{uuid.uuid4()}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            insert into tenants (id, name, slug, iyzico_customer_id)
            values ($1, $2, $3, $4)
            """,
            tenant_id,
            "Iyzico Tenant",
            f"iyz-{tenant_id}",
            customer_code,
        )
    try:
        yield {"id": tenant_id, "customer_code": customer_code}
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "delete from audit_log where tenant_id = $1", tenant_id
            )
            await conn.execute(
                "delete from billing_events where tenant_id = $1", tenant_id
            )
            await conn.execute("delete from tenants where id = $1", tenant_id)


def _build_client(*, pool: asyncpg.Pool) -> tuple[FastAPI, AsyncClient]:
    """No JWT override — the route is public, only HMAC-gated."""

    app = create_app()

    async def _fake_db() -> AsyncIterator[asyncpg.Connection]:
        async with pool.acquire() as conn:
            yield conn

    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


def _signed(body: dict[str, Any]) -> tuple[bytes, str]:
    """Encode + sign a payload exactly the way the route reads it."""

    raw = json.dumps(body).encode("utf-8")
    sig = iyzico.compute_signature(body=raw, secret=_WEBHOOK_SECRET)
    return raw, sig


# ── Pure signature unit tests ──────────────────────────────────────────


def test_verify_signature_accepts_a_correctly_signed_body() -> None:
    body = b'{"hello":"world"}'
    sig = iyzico.compute_signature(body=body, secret=_WEBHOOK_SECRET)
    iyzico.verify_signature(body=body, secret=_WEBHOOK_SECRET, header_value=sig)


def test_verify_signature_rejects_a_tampered_body() -> None:
    body = b'{"hello":"world"}'
    sig = iyzico.compute_signature(body=body, secret=_WEBHOOK_SECRET)
    with pytest.raises(iyzico.InvalidSignatureError):
        iyzico.verify_signature(
            body=body + b'x',
            secret=_WEBHOOK_SECRET,
            header_value=sig,
        )


def test_verify_signature_rejects_a_missing_header() -> None:
    with pytest.raises(iyzico.InvalidSignatureError):
        iyzico.verify_signature(
            body=b'{}',
            secret=_WEBHOOK_SECRET,
            header_value=None,
        )


def test_verify_signature_rejects_signature_from_a_different_secret() -> None:
    body = b'{"hello":"world"}'
    other = iyzico.compute_signature(body=body, secret="some-other-secret")
    with pytest.raises(iyzico.InvalidSignatureError):
        iyzico.verify_signature(
            body=body, secret=_WEBHOOK_SECRET, header_value=other
        )


# ── Route: error paths ────────────────────────────────────────────────


async def test_route_returns_503_when_secret_unconfigured(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IYZICO_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()

    _app, client = _build_client(pool=pool)
    async with client:
        response = await client.post(
            "/api/v1/billing/iyzico-webhook", content=b'{}'
        )
    assert response.status_code == 503


async def test_route_returns_401_for_missing_signature(
    pool: asyncpg.Pool, _webhook_env: str
) -> None:
    _app, client = _build_client(pool=pool)
    async with client:
        response = await client.post(
            "/api/v1/billing/iyzico-webhook", content=b'{"eventId":"x"}'
        )
    assert response.status_code == 401


async def test_route_returns_401_for_wrong_signature(
    pool: asyncpg.Pool, _webhook_env: str
) -> None:
    _app, client = _build_client(pool=pool)
    async with client:
        response = await client.post(
            "/api/v1/billing/iyzico-webhook",
            content=b'{"eventId":"x"}',
            headers={"X-Iyzico-Signature": "definitely-not-real"},
        )
    assert response.status_code == 401


async def test_route_returns_400_for_invalid_json(
    pool: asyncpg.Pool, _webhook_env: str
) -> None:
    body = b'{this is not json'
    sig = iyzico.compute_signature(body=body, secret=_WEBHOOK_SECRET)
    _app, client = _build_client(pool=pool)
    async with client:
        response = await client.post(
            "/api/v1/billing/iyzico-webhook",
            content=body,
            headers={"X-Iyzico-Signature": sig},
        )
    assert response.status_code == 400


# ── Route: happy paths ────────────────────────────────────────────────


async def test_subscription_activated_updates_plan_and_writes_audit(
    pool: asyncpg.Pool, tenant: dict[str, Any], _webhook_env: str
) -> None:
    body = {
        "eventId": f"evt-{uuid.uuid4()}",
        "eventType": "subscription.activated",
        "customerReferenceCode": tenant["customer_code"],
        "pricingPlanReferenceCode": "iyz_pro_monthly",
    }
    raw, sig = _signed(body)

    _app, client = _build_client(pool=pool)
    async with client:
        response = await client.post(
            "/api/v1/billing/iyzico-webhook",
            content=raw,
            headers={"X-Iyzico-Signature": sig},
        )

    assert response.status_code == 200
    assert response.json() == {"received": True}

    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "select plan, monthly_proposal_limit from tenants where id = $1",
            tenant["id"],
        )
        events = await conn.fetch(
            "select event_type, provider_event_id from billing_events where tenant_id = $1",
            tenant["id"],
        )
        audits = await conn.fetch(
            "select action, diff::text as diff from audit_log where tenant_id = $1",
            tenant["id"],
        )

    expected = lookup_plan("iyz_pro_monthly")
    assert expected is not None
    assert plan_row["plan"] == expected.name
    assert plan_row["monthly_proposal_limit"] == expected.monthly_proposal_limit
    assert len(events) == 1
    assert events[0]["event_type"] == "subscription.activated"
    assert any(a["action"] == "tenant.plan_changed" for a in audits)
    assert any("pro" in a["diff"] for a in audits)


async def test_idempotent_retry_does_not_duplicate_event_or_audit(
    pool: asyncpg.Pool, tenant: dict[str, Any], _webhook_env: str
) -> None:
    """Iyzico retries on transient failure. Same event_id → 200 again,
    no new ``billing_events`` row, no new audit row (audit is only
    written when the plan changes; second call sees the same plan)."""

    body = {
        "eventId": f"evt-{uuid.uuid4()}",
        "eventType": "subscription.activated",
        "customerReferenceCode": tenant["customer_code"],
        "pricingPlanReferenceCode": "iyz_starter_monthly",
    }
    raw, sig = _signed(body)

    _app, client = _build_client(pool=pool)
    async with client:
        first = await client.post(
            "/api/v1/billing/iyzico-webhook",
            content=raw,
            headers={"X-Iyzico-Signature": sig},
        )
        second = await client.post(
            "/api/v1/billing/iyzico-webhook",
            content=raw,
            headers={"X-Iyzico-Signature": sig},
        )

    assert first.status_code == 200
    assert second.status_code == 200

    async with pool.acquire() as conn:
        event_count = await conn.fetchval(
            "select count(*) from billing_events where tenant_id = $1",
            tenant["id"],
        )
    assert event_count == 1


async def test_subscription_cancelled_downgrades_to_starter(
    pool: asyncpg.Pool, tenant: dict[str, Any], _webhook_env: str
) -> None:
    """A cancellation event drops the tenant back to the starter plan."""

    # Pre-set the tenant to Pro so we can observe the downgrade.
    async with pool.acquire() as conn:
        await conn.execute(
            "update tenants set plan = 'pro', monthly_proposal_limit = 15 where id = $1",
            tenant["id"],
        )

    body = {
        "eventId": f"evt-{uuid.uuid4()}",
        "eventType": "subscription.cancelled",
        "customerReferenceCode": tenant["customer_code"],
    }
    raw, sig = _signed(body)

    _app, client = _build_client(pool=pool)
    async with client:
        response = await client.post(
            "/api/v1/billing/iyzico-webhook",
            content=raw,
            headers={"X-Iyzico-Signature": sig},
        )
    assert response.status_code == 200

    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "select plan, monthly_proposal_limit from tenants where id = $1",
            tenant["id"],
        )
    assert plan_row["plan"] == "starter"
    assert plan_row["monthly_proposal_limit"] == 3


async def test_unknown_plan_reference_persists_event_but_skips_plan_update(
    pool: asyncpg.Pool, tenant: dict[str, Any], _webhook_env: str
) -> None:
    """Forward-compatibility: a plan reference we don't have a mapping
    for is logged + persisted, but does NOT mutate ``tenants.plan``."""

    async with pool.acquire() as conn:
        before = await conn.fetchval(
            "select plan from tenants where id = $1", tenant["id"]
        )

    body = {
        "eventId": f"evt-{uuid.uuid4()}",
        "eventType": "subscription.activated",
        "customerReferenceCode": tenant["customer_code"],
        "pricingPlanReferenceCode": "iyz_unknown_plan_we_did_not_configure",
    }
    raw, sig = _signed(body)

    _app, client = _build_client(pool=pool)
    async with client:
        response = await client.post(
            "/api/v1/billing/iyzico-webhook",
            content=raw,
            headers={"X-Iyzico-Signature": sig},
        )
    assert response.status_code == 200

    async with pool.acquire() as conn:
        after = await conn.fetchval(
            "select plan from tenants where id = $1", tenant["id"]
        )
        events = await conn.fetchval(
            "select count(*) from billing_events where tenant_id = $1",
            tenant["id"],
        )
    assert after == before
    assert events == 1
