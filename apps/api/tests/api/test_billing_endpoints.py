"""Tests for the Iyzico checkout + cancel endpoints.

Two tiers:

A. **In-memory** — 503 when API keys are missing, 401/403 when auth is
   wrong. Run without TEST_DATABASE_URL; FastAPI dependency overrides
   replace the DB.
B. **DB-bound** — happy path + cross-tenant + 404 when no subscription.
   Skip when TEST_DATABASE_URL is unset (existing convention).

The :class:`IyzicoClient` is monkeypatched at the route module level
so we don't make outbound calls during tests; the client unit tests
(`test_iyzico_client.py`) cover the wire layer separately.
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
from src.billing.iyzico_client import (
    CancelResult,
    CheckoutSession,
    IyzicoOutboundError,
)
from src.core.auth import get_current_user_id
from src.core.config import Settings, get_settings
from src.core.db import get_db
from src.main import create_app


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


# ── Fixtures (DB) ──────────────────────────────────────────────────────


@pytest.fixture
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set — skipping DB-bound billing tests"
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
                "Billing Test",
                f"bil-{tenant_id}",
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
    *,
    pool: asyncpg.Pool | None,
    user_id: uuid.UUID | None,
    settings: Settings | None = None,
) -> tuple[FastAPI, AsyncClient]:
    """Build the test app with overrides.

    ``get_db`` is ALWAYS overridden — even when ``pool=None`` we need a
    no-op stub so the dep resolves cleanly during signature inspection
    (``src.core.db`` uses TYPE_CHECKING for ``Request`` so the real dep
    is unsuitable for in-memory tests that don't touch a DB).
    """

    app = create_app()

    if pool is not None:

        async def _real_db() -> AsyncIterator[asyncpg.Connection]:
            async with pool.acquire() as conn:
                yield conn

        app.dependency_overrides[get_db] = _real_db
    else:

        async def _stub_db() -> AsyncIterator[None]:
            yield None  # routes that depend on conn won't be reached

        app.dependency_overrides[get_db] = _stub_db

    if user_id is not None:
        app.dependency_overrides[get_current_user_id] = lambda: user_id
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


# ── In-memory tier (A) ─────────────────────────────────────────────────


def _settings_without_iyzico_keys(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "iyzico_api_key": None,
        "iyzico_secret_key": None,
        "iyzico_webhook_secret": None,
        "supabase_jwt_secret": None,
    }
    base.update(overrides)
    return Settings(**base)


async def test_checkout_returns_503_when_keys_missing() -> None:
    settings = _settings_without_iyzico_keys()
    _app, client = _build_client(
        pool=None, user_id=uuid.uuid4(), settings=settings
    )
    async with client:
        response = await client.post(
            "/api/v1/billing/checkout",
            json={"plan_reference_code": "iyz_pro_monthly"},
        )
    assert response.status_code == 503, response.text
    assert "IYZICO_API_KEY" in response.json()["detail"]


async def test_cancel_returns_503_when_keys_missing() -> None:
    settings = _settings_without_iyzico_keys()
    _app, client = _build_client(
        pool=None, user_id=uuid.uuid4(), settings=settings
    )
    async with client:
        response = await client.delete("/api/v1/billing/subscription")
    assert response.status_code == 503


async def test_checkout_requires_authentication() -> None:
    """No JWT override → HTTPBearer auto_error returns 403 'Not authenticated'.

    FastAPI's :class:`HTTPBearer` defaults to 403 when the header is
    missing entirely (vs 401 for a malformed token). The route is
    correctly authentication-gated either way.
    """

    settings = _settings_without_iyzico_keys()
    _app, client = _build_client(pool=None, user_id=None, settings=settings)
    async with client:
        response = await client.post(
            "/api/v1/billing/checkout",
            json={"plan_reference_code": "iyz_pro_monthly"},
        )
    assert response.status_code == 403


# ── DB-bound tier (B) ──────────────────────────────────────────────────


def _patch_iyzico_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    checkout_result: CheckoutSession | Exception | None = None,
    cancel_result: CancelResult | Exception | None = None,
) -> None:
    """Replace ``IyzicoClient`` in the billing route module with a stub.

    The stub mirrors only the two methods we call. Either a return
    value or an Exception (which it raises) is supplied per method.
    """

    class _Stub:
        def __init__(self, **_: Any) -> None: ...

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def create_subscription_checkout(self, **_: Any) -> CheckoutSession:
            if isinstance(checkout_result, Exception):
                raise checkout_result
            assert checkout_result is not None
            return checkout_result

        async def cancel_subscription(self, **_: Any) -> CancelResult:
            if isinstance(cancel_result, Exception):
                raise cancel_result
            assert cancel_result is not None
            return cancel_result

    import src.api.routes.billing as billing_routes

    monkeypatch.setattr(billing_routes, "IyzicoClient", _Stub)


async def test_checkout_happy_path_writes_audit(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, member_id, _ = await _make_user(pool, role="member")
    try:
        _patch_iyzico_client(
            monkeypatch,
            checkout_result=CheckoutSession(
                token="tok_abc",
                payment_page_url="https://sandbox.iyzipay.com/checkout?token=tok_abc",
                conversation_id=f"tenant:{tenant_id}:1234",
            ),
        )
        settings = _settings_without_iyzico_keys(
            iyzico_api_key="x", iyzico_secret_key="y"  # type: ignore[arg-type]
        )
        _app, client = _build_client(
            pool=pool, user_id=member_id, settings=settings
        )
        async with client:
            response = await client.post(
                "/api/v1/billing/checkout",
                json={"plan_reference_code": "iyz_pro_monthly"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token"] == "tok_abc"
        assert body["payment_page_url"].startswith("https://sandbox")

        async with pool.acquire() as conn:
            audits = await conn.fetch(
                "select action from audit_log where tenant_id = $1 "
                "and action = 'tenant.checkout_initiated'",
                tenant_id,
            )
        assert len(audits) == 1
    finally:
        await _cleanup(pool, [tenant_id], [member_id])


async def test_checkout_502_when_iyzico_errors(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, member_id, _ = await _make_user(pool, role="member")
    try:
        _patch_iyzico_client(
            monkeypatch,
            checkout_result=IyzicoOutboundError(401, '{"err":"bad sig"}'),
        )
        settings = _settings_without_iyzico_keys(
            iyzico_api_key="x", iyzico_secret_key="y"  # type: ignore[arg-type]
        )
        _app, client = _build_client(
            pool=pool, user_id=member_id, settings=settings
        )
        async with client:
            response = await client.post(
                "/api/v1/billing/checkout",
                json={"plan_reference_code": "iyz_pro_monthly"},
            )
        assert response.status_code == 502
    finally:
        await _cleanup(pool, [tenant_id], [member_id])


async def test_cancel_404_when_no_active_subscription(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    try:
        _patch_iyzico_client(
            monkeypatch,
            cancel_result=CancelResult(
                subscription_reference_code="x", status="success"
            ),
        )
        settings = _settings_without_iyzico_keys(
            iyzico_api_key="x", iyzico_secret_key="y"  # type: ignore[arg-type]
        )
        _app, client = _build_client(
            pool=pool, user_id=owner_id, settings=settings
        )
        async with client:
            response = await client.delete("/api/v1/billing/subscription")
        assert response.status_code == 404
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_cancel_happy_path_clears_reference_and_audits(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, owner_id, _ = await _make_user(pool, role="owner")
    try:
        # Pre-seed an active subscription reference.
        async with pool.acquire() as conn:
            await conn.execute(
                "update tenants set iyzico_subscription_reference = $1 where id = $2",
                "sub_ref_xyz",
                tenant_id,
            )
        _patch_iyzico_client(
            monkeypatch,
            cancel_result=CancelResult(
                subscription_reference_code="sub_ref_xyz", status="success"
            ),
        )
        settings = _settings_without_iyzico_keys(
            iyzico_api_key="x", iyzico_secret_key="y"  # type: ignore[arg-type]
        )
        _app, client = _build_client(
            pool=pool, user_id=owner_id, settings=settings
        )
        async with client:
            response = await client.delete("/api/v1/billing/subscription")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["subscription_reference_code"] == "sub_ref_xyz"

        async with pool.acquire() as conn:
            audits = await conn.fetch(
                "select action from audit_log where tenant_id = $1 "
                "and action = 'tenant.subscription_cancelled'",
                tenant_id,
            )
        assert len(audits) == 1
    finally:
        await _cleanup(pool, [tenant_id], [owner_id])


async def test_cancel_403_for_non_admin(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id, member_id, _ = await _make_user(pool, role="member")
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "update tenants set iyzico_subscription_reference = $1 where id = $2",
                "sub_ref_xyz",
                tenant_id,
            )
        _patch_iyzico_client(
            monkeypatch,
            cancel_result=CancelResult(
                subscription_reference_code="sub_ref_xyz", status="success"
            ),
        )
        settings = _settings_without_iyzico_keys(
            iyzico_api_key="x", iyzico_secret_key="y"  # type: ignore[arg-type]
        )
        _app, client = _build_client(
            pool=pool, user_id=member_id, settings=settings
        )
        async with client:
            response = await client.delete("/api/v1/billing/subscription")
        assert response.status_code == 403
    finally:
        await _cleanup(pool, [tenant_id], [member_id])
